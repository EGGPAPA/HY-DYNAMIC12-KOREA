import math
from itertools import product

import pandas as pd
import streamlit as st
import yfinance as yf

st.title('🎯 TIGER TOP10 · 강세장 보존형 MDD/CAGR 최적화')
st.caption('강세장에서는 100% 보유를 우선하고, 장기 추세가 실제로 약해질 때만 단계적으로 방어합니다. 전일 신호를 다음 거래일 수익률에 적용합니다.')

c1, c2, c3 = st.columns(3)
with c1:
    start = st.date_input('시작일', pd.Timestamp('2023-08-19'))
with c2:
    end = st.date_input('종료일', pd.Timestamp.today())
with c3:
    initial = st.number_input('초기자금(원)', min_value=1_000_000, value=10_000_000, step=1_000_000)

target_mdd = st.slider('목표 최대낙폭 MDD(%)', 15, 30, 22, 1)
min_capture = st.slider('강세장 수익 포착 목표(%)', 60, 95, 80, 5)
mode = st.radio('탐색 모드', ['빠른 탐색', '정밀 탐색'], horizontal=True)
st.info('핵심: 장기 이동평균 위 + 장기 이동평균 상승이면 강세장으로 보고 100% 보유합니다. 단기 흔들림만으로는 매도하지 않고, 장기 추세 약화와 고점 하락이 겹칠 때 단계적으로 방어합니다.')


def normalize_close(d):
    if d is None or d.empty:
        return pd.Series(dtype=float)
    try:
        if isinstance(d.columns, pd.MultiIndex):
            s = d['Close'] if 'Close' in d.columns.get_level_values(0) else d.xs('Close', axis=1, level=1)
            if isinstance(s, pd.DataFrame): s = s.iloc[:, 0]
        else:
            s = d['Close']
        s = pd.to_numeric(s, errors='coerce').dropna()
        s.index = pd.to_datetime(s.index).tz_localize(None) if getattr(pd.to_datetime(s.index), 'tz', None) is not None else pd.to_datetime(s.index)
        return s
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=1800, show_spinner=False)
def load_price(start, end):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    out = pd.Series(dtype=float)
    try:
        out = normalize_close(yf.download('292150.KS', start=str(s.date()), end=str((e + pd.Timedelta(days=1)).date()), auto_adjust=True, progress=False, threads=False, timeout=15))
    except Exception:
        pass
    if out.empty:
        try:
            out = normalize_close(yf.Ticker('292150.KS').history(period='max', auto_adjust=True, timeout=15))
        except Exception:
            pass
    return out[(out.index >= s) & (out.index <= e)] if not out.empty else out


def metrics(eq):
    eq = pd.to_numeric(eq, errors='coerce').dropna()
    if len(eq) < 2: return 0.0, 0.0, 0.0
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1/365.25)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    mdd = (eq / eq.cummax() - 1).min()
    return total * 100, cagr * 100, mdd * 100


def run(px, fast, slow, slope_days, weak_w, bear_w, dd_trigger, severe_w, buffer):
    mf = px.rolling(fast).mean()
    ms = px.rolling(slow).mean()
    slope = ms.pct_change(slope_days)
    dd = px / px.cummax() - 1
    weights, severe = [], False
    for i in range(len(px)):
        p = px.iloc[i]
        valid = pd.notna(ms.iloc[i]) and pd.notna(slope.iloc[i])
        bull = valid and p >= ms.iloc[i] and slope.iloc[i] > 0

        # 강세장에서는 고점 조정이 있어도 100% 보유하여 상승 포착률을 높인다.
        if bull:
            severe = False
            w = 1.0
        else:
            if dd.iloc[i] <= -dd_trigger / 100:
                severe = True
            # 장기 추세 회복 + 버퍼 충족 시 정상 복귀
            if severe and valid and p >= ms.iloc[i] * (1 + buffer/100) and slope.iloc[i] >= 0:
                severe = False
            if severe:
                w = severe_w / 100
            elif valid and p < ms.iloc[i] and slope.iloc[i] < 0:
                w = bear_w / 100
            elif pd.notna(mf.iloc[i]) and p < mf.iloc[i]:
                w = weak_w / 100
            else:
                w = 1.0
        weights.append(w)
    w = pd.Series(weights, index=px.index).shift(1).fillna(1.0)
    return initial * (1 + px.pct_change().fillna(0) * w).cumprod()


if st.button('🚀 강세장 보존형 최적화 실행', type='primary', use_container_width=True):
    px = load_price(start, end)
    if px.empty or len(px) < 205:
        st.error('가격 데이터가 부족합니다. 잠시 후 다시 실행해 주세요.')
        st.stop()

    bh = initial * px / px.iloc[0]
    _, bh_cagr, bh_mdd = metrics(bh)

    if mode == '빠른 탐색':
        fasts, slows, slopes = [40, 60, 80], [120, 160, 200], [10, 20]
        weak_ws, bear_ws = [80, 90, 100], [30, 50, 70]
        dds, severe_ws, buffers = [12, 15, 18, 20], [0, 20, 40], [0, 2]
    else:
        fasts, slows, slopes = [30, 40, 50, 60, 80, 100], [100, 120, 140, 160, 180, 200], [5, 10, 15, 20]
        weak_ws, bear_ws = [70, 80, 90, 100], [20, 30, 40, 50, 60, 70, 80]
        dds, severe_ws, buffers = [10, 12, 15, 18, 20, 22], [0, 10, 20, 30, 40], [0, 1, 2]

    configs = [x for x in product(fasts, slows, slopes, weak_ws, bear_ws, dds, severe_ws, buffers)
               if x[0] < x[1] and x[4] <= x[3]]
    rows, curves = [], {}
    bar = st.progress(0, text=f'0/{len(configs)} 조합')
    for n, cfg in enumerate(configs, 1):
        eq = run(px, *cfg)
        ret, cagr, mdd = metrics(eq)
        capture = (cagr / bh_cagr * 100) if bh_cagr > 0 else 100.0
        rows.append({'단기이평':cfg[0], '장기이평':cfg[1], '장기기울기일':cfg[2], '약화ETF(%)':cfg[3], '약세ETF(%)':cfg[4],
                     '고점하락방어(%)':cfg[5], '최종ETF(%)':cfg[6], '재진입버퍼(%)':cfg[7], '누적수익률(%)':ret,
                     'CAGR(%)':cagr, 'MDD(%)':mdd, '수익포착률(%)':capture,
                     'MDD목표':mdd >= -target_mdd, '포착목표':capture >= min_capture})
        curves[cfg] = eq
        if n % 100 == 0 or n == len(configs):
            bar.progress(n/len(configs), text=f'{n}/{len(configs)} 조합')
    bar.empty()

    df = pd.DataFrame(rows)
    qualified = df[df['MDD목표'] & df['포착목표']].sort_values(['CAGR(%)','MDD(%)'], ascending=[False,False]).reset_index(drop=True)
    if qualified.empty:
        st.warning('MDD와 강세장 수익 포착 목표를 동시에 만족한 조합이 없습니다. 가장 균형적인 후보를 표시합니다.')
        df['MDD초과'] = (-target_mdd - df['MDD(%)']).clip(lower=0)
        df['포착부족'] = (min_capture - df['수익포착률(%)']).clip(lower=0)
        df['균형벌점'] = df['MDD초과'] * 3 + df['포착부족']
        best = df.sort_values(['균형벌점','CAGR(%)'], ascending=[True,False]).iloc[0]
    else:
        best = qualified.iloc[0]

    cfg = (int(best['단기이평']), int(best['장기이평']), int(best['장기기울기일']), int(best['약화ETF(%)']), int(best['약세ETF(%)']), int(best['고점하락방어(%)']), int(best['최종ETF(%)']), int(best['재진입버퍼(%)']))
    best_eq = curves[cfg]

    a,b,c,d,e = st.columns(5)
    a.metric('Buy & Hold CAGR', f'{bh_cagr:.1f}%')
    b.metric('최적화 CAGR', f"{best['CAGR(%)']:.1f}%", f"{best['CAGR(%)']-bh_cagr:+.1f}%p")
    c.metric('수익 포착률', f"{best['수익포착률(%)']:.1f}%")
    d.metric('Buy & Hold MDD', f'{bh_mdd:.1f}%')
    e.metric('최적화 MDD', f"{best['MDD(%)']:.1f}%", f"{best['MDD(%)']-bh_mdd:+.1f}%p")

    st.success(f"추천: 강세장 100% 유지 · {cfg[0]}일선 약화→ETF {cfg[3]}% · {cfg[1]}일선 하락추세→ETF {cfg[4]}% · 고점 -{cfg[5]}%→ETF {cfg[6]}% · 회복 버퍼 +{cfg[7]}%")
    st.line_chart(pd.concat([bh.rename('TIGER TOP10 Buy & Hold'), best_eq.rename('강세장 보존형 전략')], axis=1))

    st.subheader(f'🏆 MDD -{target_mdd}% / 수익포착 {min_capture}% 이상 TOP15')
    show = qualified if not qualified.empty else df.sort_values(['균형벌점','CAGR(%)'], ascending=[True,False])
    st.dataframe(show.head(15).round(2), use_container_width=True, hide_index=True)

    st.subheader('📅 기간별 검증 안내')
    st.caption('전체기간 최적값만 믿지 말고 2023~24, 2024~25, 2025~26처럼 1년 단위로 시작일/종료일을 바꿔 수익 포착률과 MDD가 반복적으로 유지되는지 확인하세요.')

    st.subheader('💰 1,000만원 성장 시뮬레이션')
    rate = float(best['CAGR(%)'])
    vals = [initial * (1 + rate/100) ** y for y in [1,3,5]]
    g1,g2,g3,g4 = st.columns(4)
    g1.metric('백테스트 CAGR', f'{rate:.1f}%')
    g2.metric('1년 후', f'{vals[0]:,.0f}원')
    g3.metric('3년 후', f'{vals[1]:,.0f}원')
    g4.metric('5년 후', f'{vals[2]:,.0f}원')
    st.warning('과거 백테스트 결과는 미래 수익을 보장하지 않습니다. 특히 단기간의 매우 높은 CAGR은 장기 기대수익률로 그대로 사용하면 안 됩니다.')