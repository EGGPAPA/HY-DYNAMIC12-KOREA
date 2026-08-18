import math
from itertools import product

import pandas as pd
import streamlit as st
import yfinance as yf

st.title('🎯 TIGER TOP10 · MDD -20% 이내 CAGR 극대화')
st.caption('TIGER 코리아TOP10의 높은 CAGR은 최대한 유지하면서 MDD를 -20% 안쪽으로 낮추는 조합을 집중 탐색합니다. 전일 신호를 다음 거래일 수익률에 적용합니다.')

c1, c2, c3 = st.columns(3)
with c1:
    start = st.date_input('시작일', pd.Timestamp('2023-08-19'))
with c2:
    end = st.date_input('종료일', pd.Timestamp.today())
with c3:
    initial = st.number_input('초기자금(원)', min_value=1_000_000, value=10_000_000, step=1_000_000)

target_mdd = st.slider('목표 최대낙폭 MDD(%)', 15, 25, 20, 1)
mode = st.radio('탐색 모드', ['빠른 탐색', '정밀 탐색'], horizontal=True)
st.info('목표: MDD 제한을 먼저 지키고, 그 안에서 CAGR이 가장 높은 전략을 선택합니다. 강세장 100% → 단기 이평 이탈 1차 축소 → 장기 이평 이탈 2차 축소 → 고점 하락 최종방어 → 추세 회복 시 100% 복귀.')


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


def run(px, fast, slow, w1, w2, dd_trigger, severe_w, buffer):
    mf, ms = px.rolling(fast).mean(), px.rolling(slow).mean()
    dd = px / px.cummax() - 1
    weights, severe = [], False
    for i in range(len(px)):
        p = px.iloc[i]
        if dd.iloc[i] <= -dd_trigger / 100: severe = True
        if severe and pd.notna(mf.iloc[i]) and pd.notna(ms.iloc[i]) and p >= mf.iloc[i] * (1 + buffer/100) and p >= ms.iloc[i]: severe = False
        if severe: w = severe_w / 100
        elif pd.notna(ms.iloc[i]) and p < ms.iloc[i]: w = w2 / 100
        elif pd.notna(mf.iloc[i]) and p < mf.iloc[i]: w = w1 / 100
        else: w = 1.0
        weights.append(w)
    w = pd.Series(weights, index=px.index).shift(1).fillna(1.0)
    return initial * (1 + px.pct_change().fillna(0) * w).cumprod()


if st.button('🚀 MDD -20% 집중 최적화 실행', type='primary', use_container_width=True):
    px = load_price(start, end)
    if px.empty or len(px) < 205:
        st.error('가격 데이터가 부족합니다. 잠시 후 다시 실행해 주세요.')
        st.stop()

    bh = initial * px / px.iloc[0]
    _, bh_cagr, bh_mdd = metrics(bh)

    if mode == '빠른 탐색':
        fasts, slows = [40, 60, 80, 100], [100, 120, 140, 160]
        w1s, w2s = [80, 90, 100], [20, 30, 40]
        dds, severe_ws, buffers = [10, 12, 15], [0, 10, 20], [0, 2]
    else:
        fasts, slows = [40, 50, 60, 70, 80, 90, 100], [100, 120, 140, 160, 180]
        w1s, w2s = [80, 90, 100], [10, 20, 30, 40, 50]
        dds, severe_ws, buffers = [8, 10, 12, 15, 18], [0, 10, 20, 30], [0, 1, 2, 3]

    configs = [x for x in product(fasts, slows, w1s, w2s, dds, severe_ws, buffers) if x[0] < x[1] and x[3] <= x[2]]
    rows, curves = [], {}
    bar = st.progress(0, text=f'0/{len(configs)} 조합')
    for n, cfg in enumerate(configs, 1):
        eq = run(px, *cfg)
        ret, cagr, mdd = metrics(eq)
        rows.append({'단기이평':cfg[0], '장기이평':cfg[1], '1차ETF(%)':cfg[2], '2차ETF(%)':cfg[3], '고점하락방어(%)':cfg[4], '최종ETF(%)':cfg[5], '재진입버퍼(%)':cfg[6], '누적수익률(%)':ret, 'CAGR(%)':cagr, 'MDD(%)':mdd, '목표충족':mdd >= -target_mdd})
        curves[cfg] = eq
        if n % 100 == 0 or n == len(configs): bar.progress(n/len(configs), text=f'{n}/{len(configs)} 조합')
    bar.empty()

    df = pd.DataFrame(rows)
    safe = df[df['목표충족']].sort_values(['CAGR(%)','MDD(%)'], ascending=[False,False]).reset_index(drop=True)
    if safe.empty:
        st.warning(f'MDD -{target_mdd}% 이내 조합이 없습니다. 가장 가까운 후보를 표시합니다.')
        df['초과낙폭'] = (-target_mdd - df['MDD(%)']).clip(lower=0)
        best = df.sort_values(['초과낙폭','CAGR(%)'], ascending=[True,False]).iloc[0]
    else:
        best = safe.iloc[0]

    cfg = (int(best['단기이평']), int(best['장기이평']), int(best['1차ETF(%)']), int(best['2차ETF(%)']), int(best['고점하락방어(%)']), int(best['최종ETF(%)']), int(best['재진입버퍼(%)']))
    best_eq = curves[cfg]
    a,b,c,d = st.columns(4)
    a.metric('Buy & Hold CAGR', f'{bh_cagr:.1f}%')
    b.metric('최적화 CAGR', f"{best['CAGR(%)']:.1f}%", f"{best['CAGR(%)']-bh_cagr:+.1f}%p")
    c.metric('Buy & Hold MDD', f'{bh_mdd:.1f}%')
    d.metric('최적화 MDD', f"{best['MDD(%)']:.1f}%", f"{best['MDD(%)']-bh_mdd:+.1f}%p")

    st.success(f"추천: {cfg[0]}일선 이탈→ETF {cfg[2]}% · {cfg[1]}일선 이탈→ETF {cfg[3]}% · 고점 -{cfg[4]}%→ETF {cfg[5]}% · 회복 버퍼 +{cfg[6]}%")
    st.line_chart(pd.concat([bh.rename('TIGER TOP10 Buy & Hold'), best_eq.rename('MDD20 CAGR 최적화')], axis=1))

    st.subheader(f'🏆 MDD -{target_mdd}% 이내 CAGR TOP15')
    if not safe.empty: st.dataframe(safe.head(15).round(2), use_container_width=True, hide_index=True)

    st.subheader('💰 1,000만원 성장 시뮬레이션')
    rate = float(best['CAGR(%)'])
    vals = []
    for y in [1,3,5]: vals.append(initial * (1 + rate/100) ** y)
    g1,g2,g3,g4 = st.columns(4)
    g1.metric('백테스트 CAGR', f'{rate:.1f}%')
    g2.metric('1년 후', f'{vals[0]:,.0f}원')
    g3.metric('3년 후', f'{vals[1]:,.0f}원')
    g4.metric('5년 후', f'{vals[2]:,.0f}원')
    st.warning('과거 백테스트 결과는 미래 수익을 보장하지 않습니다. 특히 약 3년 구간의 높은 CAGR은 장기 기대수익률로 그대로 사용하면 안 됩니다.')