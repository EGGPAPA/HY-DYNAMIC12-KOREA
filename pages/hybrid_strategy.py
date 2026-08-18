from datetime import date, timedelta

import pandas as pd
import streamlit as st
import yfinance as yf

import korea_backtest_ui as bt

st.set_page_config(page_title="HY DYNAMIC12 하이브리드", page_icon="🧩", layout="wide")

ETF_MAP = {
    "TIGER 코리아TOP10": "292150.KS",
    "KODEX 200": "069500.KS",
}


def close_series(raw):
    if raw is None or raw.empty:
        return pd.Series(dtype=float)
    s = raw["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s = pd.to_numeric(s, errors="coerce").dropna()
    s.index = pd.to_datetime(s.index)
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)
    return s


def stats(curve, initial):
    curve = pd.to_numeric(curve, errors="coerce").dropna()
    if curve.empty:
        return {}
    final = float(curve.iloc[-1])
    total = final / initial - 1
    days = max((curve.index[-1] - curve.index[0]).days, 1)
    years = max(days / 365.25, 1 / 365.25)
    cagr = (final / initial) ** (1 / years) - 1
    dd = curve / curve.cummax() - 1
    return {"final": final, "total": total, "cagr": cagr, "mdd": float(dd.min())}


st.title("🧩 HY DYNAMIC12 + ETF 하이브리드")
st.caption("개별주 전략을 기본 엔진으로 사용하면서 남는 자금을 ETF에 배분해 강세장 참여율을 높이는 실험용 백테스트입니다.")

today = date.today()
c1, c2, c3 = st.columns(3)
start = c1.date_input("시작일", value=today - timedelta(days=365 * 3), max_value=today)
end = c2.date_input("종료일", value=today, max_value=today)
initial = c3.number_input("초기자금(원)", min_value=1_000_000, value=10_000_000, step=1_000_000)

c4, c5, c6 = st.columns(3)
etf_name = c4.selectbox("ETF", list(ETF_MAP.keys()))
hy_weight = c5.slider("HY 기본비중(%)", 50, 90, 70, 5)
auto_opt = c6.checkbox("50~90% 비중 자동탐색", value=True)

st.info("HY 조건: 월 최대 2종목 · 동시 2종목 · 진입 78점 · 손절 -6% · +20%에서 20% 매도 · +40%에서 30% 매도 · 잔여 50%는 고점대비 -10% 트레일링 · 최대 90거래일")

if st.button("🚀 하이브리드 백테스트 실행", type="primary", use_container_width=True):
    if start >= end:
        st.error("시작일은 종료일보다 앞서야 합니다.")
        st.stop()

    with st.spinner("HY 전략과 ETF를 같은 기간으로 계산 중입니다..."):
        data = bt._download(start - timedelta(days=220), end)
        _, _, hy_eq, hy_stats = bt._simulate(
            data, start, end,
            2, 2, 90,
            6.0, 10.0, 0.30, float(initial), 78.0,
            20.0, 20.0, 40.0, 30.0,
        )
        raw = yf.download(ETF_MAP[etf_name], start=str(start), end=str(end + timedelta(days=1)), auto_adjust=True, progress=False)
        etf_close = close_series(raw)

    if not hy_stats or hy_eq.empty or etf_close.empty:
        st.error("HY 또는 ETF 가격 데이터를 충분히 가져오지 못했습니다. 잠시 후 다시 실행해 주세요.")
        st.stop()

    hy_curve = hy_eq["HY DYNAMIC12"].dropna()
    common_index = hy_curve.index.union(etf_close.index).sort_values()
    hy_norm = hy_curve.reindex(common_index).ffill().bfill() / float(hy_curve.iloc[0])
    etf_norm = etf_close.reindex(common_index).ffill().bfill() / float(etf_close.iloc[0])

    weights = list(range(50, 95, 5)) if auto_opt else [hy_weight]
    rows = []
    curves = {}
    for w in weights:
        hw = w / 100.0
        curve = float(initial) * (hw * hy_norm + (1 - hw) * etf_norm)
        s = stats(curve, float(initial))
        # 수익률 우선, 위험 보조: CAGR - MDD 절대값의 25%
        score = s["cagr"] - abs(s["mdd"]) * 0.25
        rows.append({"HY비중(%)": w, "ETF비중(%)": 100-w, "최종자산(원)": round(s["final"]), "누적수익률(%)": round(s["total"]*100, 1), "CAGR(%)": round(s["cagr"]*100, 1), "MDD(%)": round(s["mdd"]*100, 1), "균형점수": round(score*100, 2)})
        curves[w] = curve

    result = pd.DataFrame(rows).sort_values(["균형점수", "CAGR(%)"], ascending=False).reset_index(drop=True)
    best_w = int(result.iloc[0]["HY비중(%)"])
    best_curve = curves[best_w]
    best_stats = stats(best_curve, float(initial))

    hy_only = float(initial) * hy_norm
    etf_only = float(initial) * etf_norm
    hs = stats(hy_only, float(initial))
    es = stats(etf_only, float(initial))

    st.success(f"추천 비중: HY {best_w}% + {etf_name} {100-best_w}%")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("하이브리드 누적수익률", f"{best_stats['total']*100:.1f}%")
    m2.metric("하이브리드 CAGR", f"{best_stats['cagr']*100:.1f}%")
    m3.metric("하이브리드 MDD", f"{best_stats['mdd']*100:.1f}%")
    m4.metric("최종자산", f"{best_stats['final']:,.0f}원")

    compare = pd.DataFrame({
        "HY DYNAMIC12": hy_only,
        etf_name: etf_only,
        f"HY {best_w}% + ETF {100-best_w}%": best_curve,
    }).ffill().dropna()
    st.markdown("### 📈 누적자산 비교")
    st.line_chart(compare)

    st.markdown("### 📊 전략 비교")
    summary = pd.DataFrame([
        {"전략": "HY DYNAMIC12 100%", "누적수익률(%)": hs["total"]*100, "CAGR(%)": hs["cagr"]*100, "MDD(%)": hs["mdd"]*100},
        {"전략": f"{etf_name} 100%", "누적수익률(%)": es["total"]*100, "CAGR(%)": es["cagr"]*100, "MDD(%)": es["mdd"]*100},
        {"전략": f"HY {best_w}% + ETF {100-best_w}%", "누적수익률(%)": best_stats["total"]*100, "CAGR(%)": best_stats["cagr"]*100, "MDD(%)": best_stats["mdd"]*100},
    ])
    for col in ["누적수익률(%)", "CAGR(%)", "MDD(%)"]:
        summary[col] = summary[col].round(1)
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.markdown("### 🔎 HY 비중 탐색")
    st.dataframe(result, use_container_width=True, hide_index=True)
    st.caption("현재 버전은 정적 비중 혼합입니다. 다음 단계에서는 HY 매수신호가 없을 때 ETF 비중을 높이고, 강한 개별주 신호가 발생하면 ETF에서 HY로 자금을 이동하는 동적 하이브리드로 확장할 수 있습니다.")