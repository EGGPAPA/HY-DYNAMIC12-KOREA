from datetime import date, timedelta

import pandas as pd
import streamlit as st

import korea_backtest_ui as bt

st.set_page_config(page_title="HY DYNAMIC12 익절 최적화", page_icon="🔍", layout="wide")


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_market_ok(dt):
    return bt._market_ok.__wrapped__(dt) if hasattr(bt._market_ok, "__wrapped__") else bt._market_ok(dt)


def _score(stats):
    # 수익성만 좇지 않고 MDD를 함께 벌점 처리한다.
    return stats["cagr"] * 100.0 + stats["mdd"] * 70.0


def _candidate_sets():
    # 과도한 조합 탐색으로 인한 과최적화를 줄이기 위한 실전형 후보군
    return [
        (10, 20, 20, 30, 8), (10, 20, 20, 30, 10),
        (10, 20, 25, 30, 10), (10, 20, 25, 30, 12),
        (15, 20, 20, 30, 8), (15, 20, 20, 30, 10),
        (15, 20, 25, 30, 8), (15, 20, 25, 30, 10),
        (15, 20, 25, 30, 12), (15, 20, 30, 30, 10),
        (15, 20, 30, 30, 12), (15, 20, 40, 30, 12),
        (15, 30, 25, 30, 10), (15, 30, 30, 30, 10),
        (20, 20, 30, 30, 8), (20, 20, 30, 30, 10),
        (20, 20, 30, 30, 12), (20, 20, 40, 30, 10),
        (20, 20, 40, 30, 12), (20, 30, 30, 30, 10),
        (20, 30, 40, 30, 10), (20, 30, 40, 30, 12),
        (25, 20, 40, 30, 10), (25, 20, 40, 30, 12),
    ]


st.title("🔍 HY DYNAMIC12 익절 최적화")
st.caption("같은 매수전략을 유지한 채 1·2차 익절률, 매도비율, 트레일링 조합 24개를 자동 비교해 TOP5를 찾습니다.")

c1, c2, c3 = st.columns(3)
today = date.today()
start = c1.date_input("시작일", value=today - timedelta(days=365 * 3), max_value=today)
end = c2.date_input("종료일", value=today, max_value=today)
initial_cash = c3.number_input("초기자금(원)", min_value=1_000_000, value=10_000_000, step=1_000_000)

d1, d2, d3 = st.columns(3)
monthly_limit = d1.selectbox("월 최대 신규매수", [1, 2], index=1)
max_positions = d2.selectbox("동시 최대 보유종목", [1, 2, 3], index=1)
hold_days = d3.selectbox("최대 보유기간(거래일)", [40, 60, 90, 120], index=2)

e1, e2, e3 = st.columns(3)
min_score = e1.number_input("최소 진입점수", 50.0, 100.0, 78.0, 1.0)
stop_pct = e2.number_input("기본 손절률(%)", 2.0, 15.0, 6.0, 0.5)
cost_pct = e3.number_input("왕복 비용+슬리피지(%)", 0.0, 2.0, 0.30, 0.05)

st.info("선정 기준: CAGR을 높게 평가하되 MDD에 벌점을 줍니다. 수익률 1위만 고르는 방식보다 실전 위험을 함께 고려합니다.")

if st.button("🚀 익절 최적화 실행 · 24개 조합", type="primary", use_container_width=True):
    if start >= end:
        st.error("시작일은 종료일보다 앞서야 합니다.")
        st.stop()

    # _simulate 내부의 월별 시장판단 다운로드를 Streamlit 캐시로 공유
    original_market_ok = bt._market_ok
    @st.cache_data(ttl=3600, show_spinner=False)
    def market_ok_cached(dt):
        return original_market_ok(dt)
    bt._market_ok = market_ok_cached

    with st.spinner("가격 데이터를 불러오는 중..."):
        data = bt._download(start - timedelta(days=220), end)

    candidates = _candidate_sets()
    rows = []
    progress = st.progress(0, text="익절 조합 비교 시작")

    for i, (tp1, sell1, tp2, sell2, trail) in enumerate(candidates, start=1):
        trades, exits, eq, stats = bt._simulate(
            data, start, end, monthly_limit, max_positions, hold_days,
            stop_pct, trail, cost_pct, float(initial_cash), min_score,
            tp1, sell1, tp2, sell2,
        )
        if stats:
            rows.append({
                "1차익절(%)": tp1,
                "1차매도(%)": sell1,
                "2차익절(%)": tp2,
                "2차매도(%)": sell2,
                "트레일링(%)": trail,
                "잔여비율(%)": 100 - sell1 - sell2,
                "누적수익률(%)": round(stats["total"] * 100, 1),
                "CAGR(%)": round(stats["cagr"] * 100, 1),
                "MDD(%)": round(stats["mdd"] * 100, 1),
                "승률(%)": round(stats["win"] * 100, 1),
                "거래수": stats["n"],
                "균형점수": round(_score(stats), 2),
            })
        progress.progress(i / len(candidates), text=f"{i}/{len(candidates)} 조합 완료")

    progress.empty()
    if not rows:
        st.warning("비교 가능한 백테스트 결과가 없습니다.")
        st.stop()

    result = pd.DataFrame(rows).sort_values(["균형점수", "CAGR(%)"], ascending=False).reset_index(drop=True)
    result.insert(0, "순위", range(1, len(result) + 1))
    top5 = result.head(5).copy()
    best = top5.iloc[0]

    st.success(
        f"추천 조합: +{best['1차익절(%)']:.0f}%에서 {best['1차매도(%)']:.0f}% 매도 → "
        f"+{best['2차익절(%)']:.0f}%에서 {best['2차매도(%)']:.0f}% 매도 → "
        f"잔여 {best['잔여비율(%)']:.0f}%는 고점 대비 -{best['트레일링(%)']:.0f}% 추적"
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("추천 CAGR", f"{best['CAGR(%)']:.1f}%")
    m2.metric("추천 누적수익률", f"{best['누적수익률(%)']:.1f}%")
    m3.metric("추천 MDD", f"{best['MDD(%)']:.1f}%")
    m4.metric("추천 승률", f"{best['승률(%)']:.1f}%")

    st.subheader("🏆 익절 전략 TOP5")
    st.dataframe(top5, use_container_width=True, hide_index=True)

    st.subheader("전체 24개 조합")
    st.dataframe(result, use_container_width=True, hide_index=True)

    st.warning("최적화 결과는 해당 기간에 맞춰진 값일 수 있습니다. TOP5 중 1위만 고정하지 말고 1년·3년·5년 등 서로 다른 기간에서 반복해서 상위권에 남는 조합을 우선하세요.")
