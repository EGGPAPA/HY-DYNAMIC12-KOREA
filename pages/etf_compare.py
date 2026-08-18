from datetime import date, timedelta

import pandas as pd
import streamlit as st
import yfinance as yf

import korea_backtest_ui as bt

st.set_page_config(page_title="HY DYNAMIC12 ETF 비교", page_icon="⚖️", layout="wide")

BENCHMARKS = {
    "TIGER 코리아TOP10": "292150.KS",
    "KODEX 200": "069500.KS",
}


def _series_from_download(raw):
    if raw is None or raw.empty:
        return pd.Series(dtype=float)
    close = raw["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = pd.to_numeric(close, errors="coerce").dropna()
    close.index = pd.to_datetime(close.index)
    if getattr(close.index, "tz", None) is not None:
        close.index = close.index.tz_localize(None)
    return close


@st.cache_data(ttl=3600, show_spinner=False)
def _benchmark_prices(symbol, start, end):
    raw = yf.download(symbol, start=str(start), end=str(end + timedelta(days=1)), auto_adjust=True, progress=False)
    return _series_from_download(raw)


def _benchmark_stats(close, start, end, initial_cash):
    s = close[(close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end))].copy()
    if s.empty:
        return pd.Series(dtype=float), {}
    curve = (s / float(s.iloc[0])) * float(initial_cash)
    final = float(curve.iloc[-1])
    total = final / float(initial_cash) - 1
    years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25, 1 / 365.25)
    cagr = (final / float(initial_cash)) ** (1 / years) - 1
    dd = curve / curve.cummax() - 1
    mdd = float(dd.min()) if not dd.empty else 0.0
    return curve, {"final": final, "total": total, "cagr": cagr, "mdd": mdd}


def _verdict(hy, tiger, kodex):
    etfs = [tiger, kodex]
    if all(hy["cagr"] > x["cagr"] and hy["mdd"] > x["mdd"] for x in etfs):
        return "🟢 HY DYNAMIC12 우위 — 두 ETF보다 CAGR이 높고 MDD도 낮습니다."
    if hy["cagr"] > max(x["cagr"] for x in etfs):
        return "🟡 HY DYNAMIC12 수익률 우위 — 다만 MDD까지 함께 확인해야 합니다."
    if hy["cagr"] < min(x["cagr"] for x in etfs):
        return "🔴 ETF 단순보유 우위 — 현재 기간에는 개별주 전략의 추가 수고가 보상되지 않았습니다."
    return "🟡 혼합 결과 — 한 ETF에는 앞서지만 다른 ETF에는 뒤집니다. 기간을 바꿔 재검증하세요."


st.title("⚖️ HY DYNAMIC12 vs TIGER 코리아TOP10 vs KODEX 200")
st.caption("동일 시작일·종료일·초기자금으로 개별주 선택매매와 국내 대표 ETF 단순보유를 비교합니다.")

c1, c2, c3 = st.columns(3)
today = date.today()
start = c1.date_input("시작일", value=today - timedelta(days=365 * 3), max_value=today)
end = c2.date_input("종료일", value=today, max_value=today)
initial_cash = c3.number_input("초기자금(원)", min_value=1_000_000, value=10_000_000, step=1_000_000)

st.markdown("#### HY DYNAMIC12 전략 설정")
d1, d2, d3 = st.columns(3)
monthly_limit = d1.selectbox("월 최대 신규매수", [1, 2], index=1)
max_positions = d2.selectbox("동시 최대 보유종목", [1, 2, 3], index=1)
hold_days = d3.selectbox("최대 보유기간(거래일)", [40, 60, 90, 120], index=2)

e1, e2, e3 = st.columns(3)
min_score = e1.number_input("최소 진입점수", 50.0, 100.0, 78.0, 1.0)
stop_pct = e2.number_input("기본 손절률(%)", 2.0, 15.0, 6.0, 0.5)
cost_pct = e3.number_input("왕복 비용+슬리피지(%)", 0.0, 2.0, 0.30, 0.05)

p1, p2, p3 = st.columns(3)
tp1 = p1.number_input("1차 익절률(%)", 5.0, 50.0, 20.0, 1.0)
tp2 = p2.number_input("2차 익절률(%)", 10.0, 100.0, 40.0, 1.0)
trail = p3.number_input("트레일링(%)", 5.0, 25.0, 10.0, 1.0)

q1, q2 = st.columns(2)
sell1 = q1.number_input("1차 매도비율(%)", 0.0, 90.0, 20.0, 5.0)
sell2 = q2.number_input("2차 매도비율(%)", 0.0, 90.0, 30.0, 5.0)

st.info("ETF는 해당 기간 첫 거래일에 전액 매수해 종료일까지 보유하는 Buy & Hold 방식입니다. yfinance의 auto_adjust=True 수정주가를 사용해 가격조정/분배금 영향을 가능한 한 반영합니다.")

if st.button("▶ 3개 전략 동일조건 비교", type="primary", use_container_width=True):
    if start >= end:
        st.error("시작일은 종료일보다 앞서야 합니다.")
        st.stop()
    if tp2 <= tp1 or sell1 + sell2 >= 100:
        st.error("2차 익절률은 1차보다 높고, 1·2차 매도비율 합계는 100% 미만이어야 합니다.")
        st.stop()

    with st.spinner("HY DYNAMIC12와 ETF 2개를 동일 기간으로 계산 중..."):
        data = bt._download(start - timedelta(days=220), end)
        _, _, hy_eq, hy_stats = bt._simulate(
            data, start, end, monthly_limit, max_positions, hold_days,
            stop_pct, trail, cost_pct, float(initial_cash), min_score,
            tp1, sell1, tp2, sell2,
        )
        tiger_close = _benchmark_prices(BENCHMARKS["TIGER 코리아TOP10"], start, end)
        kodex_close = _benchmark_prices(BENCHMARKS["KODEX 200"], start, end)
        tiger_curve, tiger_stats = _benchmark_stats(tiger_close, start, end, initial_cash)
        kodex_curve, kodex_stats = _benchmark_stats(kodex_close, start, end, initial_cash)

    if not hy_stats or not tiger_stats or not kodex_stats:
        st.error("비교 데이터 일부를 불러오지 못했습니다. 잠시 후 다시 실행해 주세요.")
        st.stop()

    hy = {"final": hy_stats["final"], "total": hy_stats["total"], "cagr": hy_stats["cagr"], "mdd": hy_stats["mdd"]}
    rows = [
        {"전략": "HY DYNAMIC12", **hy},
        {"전략": "TIGER 코리아TOP10", **tiger_stats},
        {"전략": "KODEX 200", **kodex_stats},
    ]
    table = pd.DataFrame(rows)
    table["최종자산(원)"] = table["final"].round(0).astype(int)
    table["누적수익률(%)"] = (table["total"] * 100).round(1)
    table["CAGR(%)"] = (table["cagr"] * 100).round(1)
    table["MDD(%)"] = (table["mdd"] * 100).round(1)
    table = table[["전략", "최종자산(원)", "누적수익률(%)", "CAGR(%)", "MDD(%)"]]

    tiger_alpha = (hy["cagr"] - tiger_stats["cagr"]) * 100
    kodex_alpha = (hy["cagr"] - kodex_stats["cagr"]) * 100

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("HY 누적수익률", f"{hy['total']*100:.1f}%")
    m2.metric("TIGER TOP10 대비 초과 CAGR", f"{tiger_alpha:+.1f}%p")
    m3.metric("KODEX200 대비 초과 CAGR", f"{kodex_alpha:+.1f}%p")
    m4.metric("HY MDD", f"{hy['mdd']*100:.1f}%")

    st.subheader("📊 동일조건 성과 비교")
    st.dataframe(table, use_container_width=True, hide_index=True)

    compare = pd.DataFrame(index=hy_eq.index)
    compare["HY DYNAMIC12"] = hy_eq["HY DYNAMIC12"]
    compare["TIGER 코리아TOP10"] = tiger_curve.reindex(compare.index).ffill()
    compare["KODEX 200"] = kodex_curve.reindex(compare.index).ffill()
    st.subheader("📈 1,000만원 기준 누적 자산")
    st.line_chart(compare)

    st.subheader("🎯 판정")
    st.success(_verdict(hy, tiger_stats, kodex_stats))
    st.caption("비교기간을 1년·3년·5년으로 바꿔도 같은 결론이 반복되는지 확인하는 것이 중요합니다. 한 기간의 우위만으로 전략을 확정하지 마세요.")
