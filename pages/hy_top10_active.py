from datetime import date, timedelta

import pandas as pd
import streamlit as st
import yfinance as yf

import korea_backtest_ui as bt

st.set_page_config(page_title="HY TOP10 ACTIVE", page_icon="🎯", layout="wide")

TOP10_UNIVERSE = [
    ("000660", "SK하이닉스", "KOSPI"),
    ("005930", "삼성전자", "KOSPI"),
    ("005380", "현대차", "KOSPI"),
    ("105560", "KB금융", "KOSPI"),
    ("068270", "셀트리온", "KOSPI"),
    ("000270", "기아", "KOSPI"),
    ("035420", "NAVER", "KOSPI"),
    ("055550", "신한지주", "KOSPI"),
    ("005490", "POSCO홀딩스", "KOSPI"),
    ("006400", "삼성SDI", "KOSPI"),
]

ETF_MAP = {
    "TIGER 코리아TOP10": "292150.KS",
    "KODEX 200": "069500.KS",
}


def _series(raw):
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


def _curve_stats(curve, initial):
    curve = pd.to_numeric(curve, errors="coerce").dropna()
    if curve.empty:
        return {}
    final = float(curve.iloc[-1])
    total = final / initial - 1
    days = max((curve.index[-1] - curve.index[0]).days, 1)
    years = max(days / 365.25, 1 / 365.25)
    cagr = (final / initial) ** (1 / years) - 1
    mdd = float((curve / curve.cummax() - 1).min())
    return {"final": final, "total": total, "cagr": cagr, "mdd": mdd}


def _buy_hold(symbol, start, end, initial):
    raw = yf.download(symbol, start=str(start), end=str(end + timedelta(days=1)), auto_adjust=True, progress=False)
    close = _series(raw)
    close = close[(close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end))]
    if close.empty:
        return pd.Series(dtype=float), {}
    curve = close / float(close.iloc[0]) * float(initial)
    return curve, _curve_stats(curve, float(initial))


st.title("🎯 HY TOP10 ACTIVE")
st.caption("TIGER 코리아TOP10의 대표 10종목만 후보군으로 두고, HY의 손절·익절·트레일링 규칙을 적용해 ETF 단순보유와 비교합니다.")

st.warning("현재 1차 검증판은 고정 TOP10 후보군을 사용합니다. 과거 시점별 실제 ETF 편입종목 변경내역까지 반영한 역사적 구성 검증은 다음 단계입니다.")

c1, c2, c3 = st.columns(3)
today = date.today()
start = c1.date_input("시작일", value=today - timedelta(days=365 * 3), max_value=today)
end = c2.date_input("종료일", value=today, max_value=today)
initial = c3.number_input("초기자금(원)", min_value=1_000_000, value=10_000_000, step=1_000_000)

st.markdown("#### ACTIVE 매매 설정")
a1, a2, a3 = st.columns(3)
monthly_limit = a1.selectbox("월 최대 신규매수", [1, 2], index=1)
max_positions = a2.selectbox("동시 최대 보유", [1, 2, 3], index=1)
hold_days = a3.selectbox("최대 보유기간(거래일)", [60, 90, 120], index=1)

b1, b2, b3 = st.columns(3)
min_score = b1.number_input("최소 진입점수", min_value=50.0, max_value=100.0, value=78.0, step=1.0)
stop_pct = b2.number_input("기본 손절률(%)", min_value=2.0, max_value=15.0, value=6.0, step=0.5)
trail_pct = b3.number_input("고점대비 전량매도(%)", min_value=5.0, max_value=25.0, value=10.0, step=1.0)

p1, p2, p3, p4 = st.columns(4)
tp1 = p1.number_input("1차 익절률(%)", min_value=5.0, max_value=50.0, value=20.0, step=1.0)
sell1 = p2.number_input("1차 매도비율(%)", min_value=0.0, max_value=90.0, value=20.0, step=5.0)
tp2 = p3.number_input("2차 익절률(%)", min_value=10.0, max_value=100.0, value=40.0, step=1.0)
sell2 = p4.number_input("2차 매도비율(%)", min_value=0.0, max_value=90.0, value=30.0, step=5.0)

cost = st.number_input("왕복 비용+슬리피지(%)", min_value=0.0, max_value=2.0, value=0.30, step=0.05)

st.markdown("#### 현재 TOP10 후보군")
st.dataframe(pd.DataFrame(TOP10_UNIVERSE, columns=["종목코드", "종목명", "시장"]), use_container_width=True, hide_index=True)

if st.button("🚀 HY TOP10 ACTIVE 비교 실행", type="primary", use_container_width=True):
    if start >= end:
        st.error("시작일은 종료일보다 앞서야 합니다.")
        st.stop()
    if tp2 <= tp1 or sell1 + sell2 >= 100:
        st.error("2차 익절률은 1차보다 높고, 1·2차 매도비율 합계는 100% 미만이어야 합니다.")
        st.stop()

    original_universe = list(bt.UNIVERSE)

    with st.spinner("TOP10 ACTIVE, 기존 HY, ETF 2개를 같은 기간으로 계산 중입니다..."):
        # 전체 데이터는 한 번만 다운로드
        full_data = bt._download(start - timedelta(days=220), end)

        # 기존 HY DYNAMIC12
        bt.UNIVERSE = original_universe
        _, _, hy_eq, hy_stats = bt._simulate(
            full_data, start, end, 2, 2, 90,
            6.0, 10.0, cost, float(initial), 78.0,
            20.0, 20.0, 40.0, 30.0,
        )

        # TOP10 ACTIVE
        bt.UNIVERSE = TOP10_UNIVERSE
        _, _, active_eq, active_stats = bt._simulate(
            full_data, start, end, monthly_limit, max_positions, hold_days,
            stop_pct, trail_pct, cost, float(initial), min_score,
            tp1, sell1, tp2, sell2,
        )
        bt.UNIVERSE = original_universe

        tiger_curve, tiger_stats = _buy_hold(ETF_MAP["TIGER 코리아TOP10"], start, end, float(initial))
        kodex_curve, kodex_stats = _buy_hold(ETF_MAP["KODEX 200"], start, end, float(initial))

    bt.UNIVERSE = original_universe

    if not active_stats or not hy_stats or not tiger_stats or not kodex_stats:
        st.error("비교 데이터 일부를 계산하지 못했습니다. 잠시 후 다시 실행해 주세요.")
        st.stop()

    rows = [
        {"전략": "HY TOP10 ACTIVE", **active_stats},
        {"전략": "기존 HY DYNAMIC12", **hy_stats},
        {"전략": "TIGER 코리아TOP10 Buy & Hold", **tiger_stats},
        {"전략": "KODEX 200 Buy & Hold", **kodex_stats},
    ]
    summary = pd.DataFrame(rows)
    summary["최종자산(원)"] = summary["final"].round(0).astype(int)
    summary["누적수익률(%)"] = (summary["total"] * 100).round(1)
    summary["CAGR(%)"] = (summary["cagr"] * 100).round(1)
    summary["MDD(%)"] = (summary["mdd"] * 100).round(1)
    summary = summary[["전략", "최종자산(원)", "누적수익률(%)", "CAGR(%)", "MDD(%)"]]

    active_cagr = active_stats["cagr"] * 100
    tiger_cagr = tiger_stats["cagr"] * 100
    active_mdd = active_stats["mdd"] * 100
    tiger_mdd = tiger_stats["mdd"] * 100

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("TOP10 ACTIVE CAGR", f"{active_cagr:.1f}%")
    m2.metric("TIGER 대비 초과 CAGR", f"{active_cagr - tiger_cagr:+.1f}%p")
    m3.metric("TOP10 ACTIVE MDD", f"{active_mdd:.1f}%")
    m4.metric("TIGER MDD", f"{tiger_mdd:.1f}%")

    if active_cagr > tiger_cagr and active_mdd > tiger_mdd:
        st.success("🟢 TOP10 ACTIVE가 TIGER TOP10보다 수익률도 높고 MDD도 낮습니다.")
    elif active_cagr > tiger_cagr:
        st.warning("🟡 TOP10 ACTIVE가 수익률은 앞서지만 MDD를 더 확인해야 합니다.")
    elif active_mdd > tiger_mdd:
        st.info("🔵 TOP10 ACTIVE는 ETF보다 수익률은 낮지만 낙폭을 줄였습니다. 수익률 개선 여지가 핵심입니다.")
    else:
        st.warning("🔴 현재 설정에서는 TIGER TOP10 단순보유가 더 유리합니다.")

    st.markdown("### 📊 동일조건 전략 비교")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    common = active_eq.index.union(hy_eq.index).union(tiger_curve.index).union(kodex_curve.index).sort_values()
    chart = pd.DataFrame(index=common)
    chart["HY TOP10 ACTIVE"] = active_eq["HY DYNAMIC12"].reindex(common).ffill()
    chart["기존 HY DYNAMIC12"] = hy_eq["HY DYNAMIC12"].reindex(common).ffill()
    chart["TIGER 코리아TOP10"] = tiger_curve.reindex(common).ffill()
    chart["KODEX 200"] = kodex_curve.reindex(common).ffill()
    chart = chart.ffill().dropna(how="all")

    st.markdown("### 📈 1,000만원 기준 누적자산")
    st.line_chart(chart)

    st.caption("다음 단계는 TOP10 ACTIVE의 손절·익절·보유기간 조합을 자동 최적화하고, 과거 실제 TIGER TOP10 편입종목 변경내역을 반영해 생존편향을 줄이는 것입니다.")
