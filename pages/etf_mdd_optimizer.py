import math
from itertools import product

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.title('🛡️ ETF MDD 방어 최적화 · 다단계 비중조절 + 기간분할 검증')
st.caption('TIGER 코리아TOP10 / KODEX200의 높은 CAGR을 최대한 유지하면서 MDD를 줄이고, 초기·중간·최근 구간에서도 반복적으로 강한 규칙을 찾습니다.')

c1, c2, c3 = st.columns(3)
with c1:
    start = st.date_input('시작일', pd.Timestamp('2023-08-19'))
with c2:
    end = st.date_input('종료일', pd.Timestamp.today())
with c3:
    initial = st.number_input('초기자금(원)', min_value=1_000_000, value=10_000_000, step=1_000_000)

ETF = {'TIGER 코리아TOP10': '292150.KS', 'KODEX200': '069500.KS'}
etf_name = st.selectbox('ETF', list(ETF))
target_mdd = st.slider('목표 최대낙폭 MDD(%)', min_value=15, max_value=35, value=25, step=1)
st.info('다단계 방어: 강세장에서는 ETF 100% 유지 → 단기 이평 이탈 시 1차 축소 → 장기 이평 이탈 시 2차 축소 → 고점대비 큰 하락 시 최종 방어. 전일 신호를 다음 거래일 수익률에 적용합니다.')


def metrics(eq):
    eq = pd.to_numeric(eq, errors='coerce').dropna()
    if len(eq) < 2:
        return 0.0, 0.0, 0.0
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    mdd = (eq / eq.cummax() - 1).min()
    return total * 100, cagr * 100, mdd * 100


def segment_metrics(eq, s, e):
    seg = eq[(eq.index >= s) & (eq.index <= e)].dropna()
    if len(seg) < 2:
        return 0.0, 0.0, 0.0
    return metrics(initial * (seg / seg.iloc[0]))


def _normalize_close(data):
    if data is None or data.empty:
        return pd.Series(dtype=float)
    try:
        if isinstance(data.columns, pd.MultiIndex):
            if 'Close' in data.columns.get_level_values(0):
                s = data['Close']
            else:
                s = data.xs('Close', axis=1, level=1)
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
        else:
            s = data['Close']
        s = pd.to_numeric(s, errors='coerce').dropna()
        s.index = pd.to_datetime(s.index)
        if getattr(s.index, 'tz', None) is not None:
            s.index = s.index.tz_localize(None)
        return s
    except Exception:
        return pd.Series(dtype=float)


def _yahoo_chart_fallback(ticker, start, end):
    try:
        p1 = int(pd.Timestamp(start).timestamp())
        p2 = int((pd.Timestamp(end) + pd.Timedelta(days=2)).timestamp())
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}'
        params = {'period1': p1, 'period2': p2, 'interval': '1d', 'events': 'history', 'includeAdjustedClose': 'true'}
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        result = r.json().get('chart', {}).get('result')
        if not result:
            return pd.Series(dtype=float)
        result = result[0]
        ts = result.get('timestamp', [])
        quote = result.get('indicators', {}).get('adjclose', [{}])[0].get('adjclose')
        if not quote:
            quote = result.get('indicators', {}).get('quote', [{}])[0].get('close', [])
        s = pd.Series(quote, index=pd.to_datetime(ts, unit='s'), dtype='float64').dropna()
        if getattr(s.index, 'tz', None) is not None:
            s.index = s.index.tz_localize(None)
        return s
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=1800, show_spinner=False)
def load_price(ticker, start, end):
    req_start = pd.Timestamp(start)
    req_end = pd.Timestamp(end)
    s = pd.Series(dtype=float)

    # 1) yfinance 지정기간
    try:
        d = yf.download(
            ticker,
            start=str(req_start.date()),
            end=str((req_end + pd.Timedelta(days=1)).date()),
            auto_adjust=True,
            progress=False,
            threads=False,
            timeout=12,
        )
        s = _normalize_close(d)
    except Exception:
        pass

    # 2) Ticker.history 재시도
    if s.empty:
        try:
            d = yf.Ticker(ticker).history(
                start=str(req_start.date()),
                end=str((req_end + pd.Timedelta(days=1)).date()),
                auto_adjust=True,
                timeout=12,
            )
            s = _normalize_close(d)
        except Exception:
            pass

    # 3) Yahoo chart API 직접 폴백
    if s.empty:
        s = _yahoo_chart_fallback(ticker, req_start, req_end)

    # 4) max 이력 마지막 재시도
    if s.empty:
        try:
            d = yf.download(ticker, period='max', auto_adjust=True, progress=False, threads=False, timeout=12)
            s = _normalize_close(d)
        except Exception:
            pass

    if s.empty:
        return s
    s = s[(s.index >= req_start) & (s.index <= req_end)]
    return s


def run_staged(px, fast_ma, slow_ma, w1, w2, dd_trigger, severe_weight, recover_buffer):
    ma_fast = px.rolling(fast_ma).mean()
    ma_slow = px.rolling(slow_ma).mean()
    draw = px / px.cummax() - 1
    vals = []
    severe = False
    for i in range(len(px)):
        p = px.iloc[i]
        mf = ma_fast.iloc[i]
        ms = ma_slow.iloc[i]
        dd = draw.iloc[i]
        if dd <= -dd_trigger / 100:
            severe = True
        if severe and pd.notna(mf) and pd.notna(ms) and p >= mf * (1 + recover_buffer / 100) and p >= ms:
            severe = False
        if severe:
            weight = severe_weight / 100
        elif pd.notna(ms) and p < ms:
            weight = w2 / 100
        elif pd.notna(mf) and p < mf:
            weight = w1 / 100
        else:
            weight = 1.0
        vals.append(weight)
    weight = pd.Series(vals, index=px.index).shift(1).fillna(1.0)
    eq = initial * (1 + px.pct_change().fillna(0) * weight).cumprod()
    return eq, weight


def growth_value(principal, rate, years):
    return principal * ((1 + rate / 100) ** years)


def doubling_years(rate):
    return math.log(2) / math.log(1 + rate / 100) if rate > 0 else float('inf')


def show_growth_dashboard(backtest_cagr, backtest_mdd):
    st.divider()
    st.header('💰 1,000만원 성장 시뮬레이터')
    st.caption('보수 15% · 기준 25% · 현재 백테스트 CAGR을 비교합니다. 백테스트 수익률은 미래 예상수익률이 아닙니다.')
    scenarios = [('보수적', 15.0), ('기준', 25.0), ('백테스트', float(backtest_cagr))]
    rows = []
    for name, rate in scenarios:
        rows.append({
            '시나리오': name,
            '연 수익률(%)': rate,
            '월평균 단순환산(원)': initial * rate / 100 / 12,
            '1년 후(원)': growth_value(initial, rate, 1),
            '3년 후(원)': growth_value(initial, rate, 3),
            '5년 후(원)': growth_value(initial, rate, 5),
            '2배 예상기간(년)': doubling_years(rate),
        })
    df = pd.DataFrame(rows)
    g1, g2, g3, g4 = st.columns(4)
    g1.metric('현재 투자금', f'{initial:,.0f}원')
    g2.metric('백테스트 CAGR', f'{backtest_cagr:.1f}%')
    g3.metric('백테스트 기준 1년 후', f'{growth_value(initial, backtest_cagr, 1):,.0f}원')
    g4.metric('백테스트 기준 2배 기간', f'{doubling_years(backtest_cagr):.2f}년')
    fmt = df.copy()
    for col in ['월평균 단순환산(원)', '1년 후(원)', '3년 후(원)', '5년 후(원)']:
        fmt[col] = fmt[col].round(0).astype('int64')
    st.dataframe(fmt.round({'연 수익률(%)': 1, '2배 예상기간(년)': 2}), use_container_width=True, hide_index=True)
    st.success(f'🎯 백테스트 CAGR {backtest_cagr:.1f}%가 반복된다는 단순 가정에서는 원금 2배까지 약 {doubling_years(backtest_cagr):.2f}년입니다.')
    st.warning(f'⚠️ 백테스트 MDD는 {backtest_mdd:.1f}%였습니다. 실제 수익은 월별로 크게 달라질 수 있습니다.')


if st.button('🚀 다단계 ETF 방어 + 기간분할 검증 실행', use_container_width=True, type='primary'):
    with st.spinner('ETF 가격과 다단계 비중조절 조합을 계산하고 있습니다...'):
        px = load_price(ETF[etf_name], start, end)

        if px.empty:
            st.error('ETF 가격 데이터를 가져오지 못했습니다. Yahoo/yfinance가 일시적으로 응답하지 않는 상태일 수 있습니다. 1~2분 후 다시 실행해 주세요.')
            st.stop()

        actual_start = px.index.min().date()
        if actual_start > start:
            st.warning(f'선택한 시작일은 ETF 가격 이력보다 앞섭니다. 실제 데이터가 시작되는 {actual_start}부터 자동으로 백테스트합니다.')
        if len(px) < 205:
            st.error(f'사용 가능한 거래일이 {len(px)}일뿐이라 장기 이동평균 검증이 어렵습니다.')
            st.stop()

        bh = initial * (px / px.iloc[0])
        _, bh_cagr, bh_mdd = metrics(bh)
        rows = []
        curves = {}

        fast_set = [60, 80, 100, 120]
        slow_set = [120, 140, 160, 200]
        w1_set = [70, 80, 90]
        w2_set = [30, 50, 70]
        dd_set = [12, 15, 18, 20]
        severe_set = [0, 20, 40]
        buffer_set = [0, 2]

        configs = [x for x in product(fast_set, slow_set, w1_set, w2_set, dd_set, severe_set, buffer_set) if x[0] < x[1] and x[3] <= x[2]]
        bar = st.progress(0, text=f'0/{len(configs)} 조합')
        for n, (fast_ma, slow_ma, w1, w2, dd_trigger, severe_weight, buffer) in enumerate(configs, 1):
            eq, _ = run_staged(px, fast_ma, slow_ma, w1, w2, dd_trigger, severe_weight, buffer)
            ret, cagr, mdd = metrics(eq)
            within = mdd >= -target_mdd
            score = cagr - max(0, abs(mdd) - target_mdd) * 4.0
            rows.append({
                '단기이평': fast_ma,
                '장기이평': slow_ma,
                '1차축소후 ETF비중(%)': w1,
                '2차축소후 ETF비중(%)': w2,
                '고점대비 최종방어(%)': dd_trigger,
                '최종방어 ETF비중(%)': severe_weight,
                '재진입버퍼(%)': buffer,
                '누적수익률(%)': ret,
                'CAGR(%)': cagr,
                'MDD(%)': mdd,
                '목표MDD충족': within,
                '점수': score,
            })
            curves[(fast_ma, slow_ma, w1, w2, dd_trigger, severe_weight, buffer)] = eq
            if n % 50 == 0 or n == len(configs):
                bar.progress(n / len(configs), text=f'{n}/{len(configs)} 조합')
        bar.empty()

        res = pd.DataFrame(rows)
        safe = res[res['목표MDD충족']].sort_values(['CAGR(%)', 'MDD(%)'], ascending=[False, False]).reset_index(drop=True)
        if len(safe):
            best = safe.iloc[0]
        else:
            best = res.sort_values(['점수', 'CAGR(%)'], ascending=False).iloc[0]

        key = (
            int(best['단기이평']), int(best['장기이평']), int(best['1차축소후 ETF비중(%)']),
            int(best['2차축소후 ETF비중(%)']), int(best['고점대비 최종방어(%)']),
            int(best['최종방어 ETF비중(%)']), int(best['재진입버퍼(%)'])
        )
        best_eq = curves[key]

        a, b, c, d = st.columns(4)
        a.metric('Buy & Hold CAGR', f'{bh_cagr:.1f}%')
        b.metric('다단계 방어 CAGR', f"{best['CAGR(%)']:.1f}%", f"{best['CAGR(%)'] - bh_cagr:+.1f}%p")
        c.metric('Buy & Hold MDD', f'{bh_mdd:.1f}%')
        d.metric('다단계 방어 MDD', f"{best['MDD(%)']:.1f}%", f"{best['MDD(%)'] - bh_mdd:+.1f}%p")

        st.success(
            f"전체기간 최고 CAGR 추천: {int(best['단기이평'])}일선 이탈→ETF {int(best['1차축소후 ETF비중(%)'])}% · "
            f"{int(best['장기이평'])}일선 이탈→ETF {int(best['2차축소후 ETF비중(%)'])}% · "
            f"고점대비 -{int(best['고점대비 최종방어(%)'])}%→ETF {int(best['최종방어 ETF비중(%)'])}% · "
            f"이평 회복(+{int(best['재진입버퍼(%)'])}% 버퍼) 시 정상복귀"
        )
        st.caption(f'실제 백테스트 기간: {px.index.min().date()} ~ {px.index.max().date()} · {len(px):,} 거래일')
        st.subheader('📈 누적자산 비교')
        st.line_chart(pd.concat([bh.rename(f'{etf_name} Buy & Hold'), best_eq.rename('다단계 MDD 방어')], axis=1))

        st.subheader(f'🎯 MDD -{target_mdd}% 이내 CAGR TOP10')
        if len(safe):
            st.dataframe(safe.head(10).round(2), use_container_width=True, hide_index=True)
        else:
            st.warning(f'MDD -{target_mdd}% 조건을 만족하는 조합이 없습니다.')

        st.divider()
        st.header('🧪 기간분할 안정성 검증')
        idx = px.index
        cut1 = idx[len(idx) // 3]
        cut2 = idx[(len(idx) * 2) // 3]
        periods = [('초기구간', idx[0], cut1), ('중간구간', cut1, cut2), ('최근구간', cut2, idx[-1])]
        candidate_pool = safe.head(30) if len(safe) >= 5 else res.sort_values(['점수', 'CAGR(%)'], ascending=False).head(30)
        robust_rows = []
        for _, cand in candidate_pool.iterrows():
            ckey = (
                int(cand['단기이평']), int(cand['장기이평']), int(cand['1차축소후 ETF비중(%)']),
                int(cand['2차축소후 ETF비중(%)']), int(cand['고점대비 최종방어(%)']),
                int(cand['최종방어 ETF비중(%)']), int(cand['재진입버퍼(%)'])
            )
            ceq = curves[ckey]
            seg_cagrs, seg_mdds, excess, mdd_improvements = [], [], [], []
            for _, ps, pe in periods:
                _, cc, cm = segment_metrics(ceq, ps, pe)
                _, bc, bm = segment_metrics(bh, ps, pe)
                seg_cagrs.append(cc); seg_mdds.append(cm); excess.append(cc - bc); mdd_improvements.append(cm - bm)
            violations = sum(1 for m in seg_mdds if m < -target_mdd)
            robust_score = sum(excess) / 3 + 0.5 * min(excess) + 0.25 * sum(mdd_improvements) / 3 - violations * 5
            robust_rows.append({
                '단기이평': ckey[0], '장기이평': ckey[1], '1차ETF비중(%)': ckey[2], '2차ETF비중(%)': ckey[3],
                '최종방어하락(%)': ckey[4], '최종ETF비중(%)': ckey[5], '재진입버퍼(%)': ckey[6],
                '전체CAGR(%)': cand['CAGR(%)'], '전체MDD(%)': cand['MDD(%)'],
                '초기CAGR(%)': seg_cagrs[0], '중간CAGR(%)': seg_cagrs[1], '최근CAGR(%)': seg_cagrs[2],
                '최악구간CAGR(%)': min(seg_cagrs), '평균ETF초과CAGR(%p)': sum(excess) / 3,
                '최악ETF초과CAGR(%p)': min(excess), '평균MDD개선(%p)': sum(mdd_improvements) / 3,
                '목표MDD위반구간': violations, '안정성점수': robust_score,
            })

        robust = pd.DataFrame(robust_rows).sort_values(['안정성점수', '전체CAGR(%)'], ascending=False).reset_index(drop=True)
        rb = robust.iloc[0]
        st.success(
            f"기간분할 최종 추천: {int(rb['단기이평'])}일선→ETF {int(rb['1차ETF비중(%)'])}% · "
            f"{int(rb['장기이평'])}일선→ETF {int(rb['2차ETF비중(%)'])}% · "
            f"고점 -{int(rb['최종방어하락(%)'])}%→ETF {int(rb['최종ETF비중(%)'])}% · 재진입 버퍼 +{int(rb['재진입버퍼(%)'])}%"
        )
        r1, r2, r3, r4 = st.columns(4)
        r1.metric('안정성 추천 전체 CAGR', f"{rb['전체CAGR(%)']:.1f}%")
        r2.metric('안정성 추천 전체 MDD', f"{rb['전체MDD(%)']:.1f}%")
        r3.metric('평균 ETF 초과 CAGR', f"{rb['평균ETF초과CAGR(%p)']:+.1f}%p")
        r4.metric('목표 MDD 위반 구간', f"{int(rb['목표MDD위반구간'])}/3")
        st.subheader('🏆 기간분할 안정성 TOP10')
        st.dataframe(robust.head(10).round(2), use_container_width=True, hide_index=True)

        rb_key = (
            int(rb['단기이평']), int(rb['장기이평']), int(rb['1차ETF비중(%)']), int(rb['2차ETF비중(%)']),
            int(rb['최종방어하락(%)']), int(rb['최종ETF비중(%)']), int(rb['재진입버퍼(%)'])
        )
        rb_eq = curves[rb_key]
        detail = []
        for name, ps, pe in periods:
            _, rc, rm = segment_metrics(rb_eq, ps, pe)
            _, bc, bm = segment_metrics(bh, ps, pe)
            detail.append({'기간': name, '방어 CAGR(%)': rc, 'ETF CAGR(%)': bc, '초과 CAGR(%p)': rc - bc, '방어 MDD(%)': rm, 'ETF MDD(%)': bm, 'MDD 개선(%p)': rm - bm})
        st.subheader('📅 최종 추천의 구간별 성적')
        st.dataframe(pd.DataFrame(detail).round(2), use_container_width=True, hide_index=True)

        st.subheader('🔥 CAGR 50% 이상 후보')
        hi = res[res['CAGR(%)'] >= 50].sort_values(['MDD(%)', 'CAGR(%)'], ascending=[False, False])
        if len(hi):
            st.dataframe(hi.head(15).round(2), use_container_width=True, hide_index=True)
        else:
            st.info('이번 기간에는 CAGR 50% 이상인 방어 조합이 없습니다.')

        show_growth_dashboard(float(rb['전체CAGR(%)']), float(rb['전체MDD(%)']))
        st.caption('과거 백테스트는 미래 수익을 보장하지 않습니다. 성장 시뮬레이터의 백테스트 시나리오는 과거 CAGR이 그대로 반복된다는 가정일 뿐 실제 예상수익률이 아닙니다.')