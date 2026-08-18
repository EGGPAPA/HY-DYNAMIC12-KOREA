from datetime import date, timedelta

import pandas as pd
import streamlit as st

import korea_backtest_ui as bt

st.set_page_config(page_title="HY DYNAMIC12 익절 최적화", page_icon="🔍", layout="wide")


def _score(stats):
    return stats["cagr"] * 100.0 + stats["mdd"] * 70.0


def _candidate_sets():
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


def _run_one(data, s, e, candidate, params):
    tp1, sell1, tp2, sell2, trail = candidate
    monthly_limit, max_positions, hold_days, stop_pct, cost_pct, initial_cash, min_score = params
    _, _, _, stats = bt._simulate(
        data, s, e, monthly_limit, max_positions, hold_days,
        stop_pct, trail, cost_pct, float(initial_cash), min_score,
        tp1, sell1, tp2, sell2,
    )
    return stats


st.title("🔍 HY DYNAMIC12 익절 최적화 + 기간분할 검증")
st.caption("24개 익절 조합을 비교하고, 서로 다른 기간에서도 반복해서 강한 조합을 찾아 과최적화 위험을 줄입니다.")

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

st.info("최종 추천은 전체기간 성적만 보지 않습니다. 기간별 순위의 평균과 최악 구간 MDD를 함께 반영해 반복적으로 강한 조합을 우선합니다.")

if st.button("🚀 익절 최적화 + 기간분할 검증 실행", type="primary", use_container_width=True):
    if start >= end:
        st.error("시작일은 종료일보다 앞서야 합니다.")
        st.stop()

    original_market_ok = bt._market_ok
    @st.cache_data(ttl=3600, show_spinner=False)
    def market_ok_cached(dt):
        return original_market_ok(dt)
    bt._market_ok = market_ok_cached

    with st.spinner("가격 데이터를 불러오는 중..."):
        data = bt._download(start - timedelta(days=220), end)

    total_days = (end - start).days
    cut1 = start + timedelta(days=total_days // 3)
    cut2 = start + timedelta(days=(total_days * 2) // 3)
    periods = [
        ("초기구간", start, cut1),
        ("중간구간", cut1 + timedelta(days=1), cut2),
        ("최근구간", cut2 + timedelta(days=1), end),
        ("전체기간", start, end),
    ]

    candidates = _candidate_sets()
    params = (monthly_limit, max_positions, hold_days, stop_pct, cost_pct, initial_cash, min_score)
    raw = []
    progress = st.progress(0, text="기간분할 검증 시작")
    total_runs = len(candidates) * len(periods)
    done = 0

    for ci, candidate in enumerate(candidates, start=1):
        tp1, sell1, tp2, sell2, trail = candidate
        base = {
            "조합ID": ci, "1차익절(%)": tp1, "1차매도(%)": sell1,
            "2차익절(%)": tp2, "2차매도(%)": sell2,
            "트레일링(%)": trail, "잔여비율(%)": 100 - sell1 - sell2,
        }
        for pname, ps, pe in periods:
            stats = _run_one(data, ps, pe, candidate, params)
            row = dict(base)
            row["기간"] = pname
            if stats:
                row.update({
                    "누적수익률(%)": stats["total"] * 100,
                    "CAGR(%)": stats["cagr"] * 100,
                    "MDD(%)": stats["mdd"] * 100,
                    "승률(%)": stats["win"] * 100,
                    "거래수": stats["n"],
                    "균형점수": _score(stats),
                })
            raw.append(row)
            done += 1
            progress.progress(done / total_runs, text=f"{done}/{total_runs} 검증 완료")

    progress.empty()
    df = pd.DataFrame(raw)
    valid = df.dropna(subset=["균형점수"]).copy()
    if valid.empty:
        st.warning("비교 가능한 결과가 없습니다.")
        st.stop()

    # 각 기간에서 24개 조합의 순위를 계산한다.
    valid["기간순위"] = valid.groupby("기간")["균형점수"].rank(method="min", ascending=False)
    split = valid[valid["기간"] != "전체기간"].copy()
    whole = valid[valid["기간"] == "전체기간"].copy()

    robust = split.groupby("조합ID").agg(
        평균기간순위=("기간순위", "mean"),
        최악기간순위=("기간순위", "max"),
        평균CAGR=("CAGR(%)", "mean"),
        최악구간CAGR=("CAGR(%)", "min"),
        최악MDD=("MDD(%)", "min"),
        기간평균균형점수=("균형점수", "mean"),
    ).reset_index()

    whole_cols = whole[["조합ID", "누적수익률(%)", "CAGR(%)", "MDD(%)", "승률(%)", "거래수", "기간순위"]].copy()
    whole_cols = whole_cols.rename(columns={"기간순위": "전체기간순위"})
    meta = valid.drop_duplicates("조합ID")[["조합ID", "1차익절(%)", "1차매도(%)", "2차익절(%)", "2차매도(%)", "트레일링(%)", "잔여비율(%)"]]
    robust = robust.merge(meta, on="조합ID").merge(whole_cols, on="조합ID", how="left")

    # 낮은 평균순위가 가장 중요하고, 동률이면 최악순위와 전체기간 균형을 본다.
    robust = robust.sort_values(["평균기간순위", "최악기간순위", "전체기간순위", "평균CAGR"], ascending=[True, True, True, False]).reset_index(drop=True)
    robust.insert(0, "최종순위", range(1, len(robust) + 1))
    best = robust.iloc[0]

    st.success(
        f"기간분할 최종 추천: +{best['1차익절(%)']:.0f}%에서 {best['1차매도(%)']:.0f}% 매도 → "
        f"+{best['2차익절(%)']:.0f}%에서 {best['2차매도(%)']:.0f}% 매도 → "
        f"잔여 {best['잔여비율(%)']:.0f}%는 고점 대비 -{best['트레일링(%)']:.0f}% 추적"
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("전체 CAGR", f"{best['CAGR(%)']:.1f}%")
    m2.metric("전체 누적수익률", f"{best['누적수익률(%)']:.1f}%")
    m3.metric("최악 구간 MDD", f"{best['최악MDD']:.1f}%")
    m4.metric("평균 기간순위", f"{best['평균기간순위']:.1f}위")

    st.subheader("🏆 기간분할 안정성 TOP5")
    show_cols = ["최종순위", "1차익절(%)", "1차매도(%)", "2차익절(%)", "2차매도(%)", "트레일링(%)", "잔여비율(%)", "평균기간순위", "최악기간순위", "평균CAGR", "최악구간CAGR", "최악MDD", "누적수익률(%)", "CAGR(%)", "MDD(%)", "승률(%)", "거래수"]
    st.dataframe(robust[show_cols].head(5).round(1), use_container_width=True, hide_index=True)

    st.subheader("📅 추천 1위의 기간별 성적")
    best_detail = valid[valid["조합ID"] == best["조합ID"]][["기간", "누적수익률(%)", "CAGR(%)", "MDD(%)", "승률(%)", "거래수", "기간순위"]]
    st.dataframe(best_detail.round(1), use_container_width=True, hide_index=True)

    st.subheader("전체 24개 안정성 순위")
    st.dataframe(robust[show_cols].round(1), use_container_width=True, hide_index=True)

    st.warning("기간분할 검증도 미래 수익을 보장하지 않습니다. 특히 거래수가 적은 구간은 순위 변동이 클 수 있으므로 여러 기간에서 반복적으로 상위권인 조합을 실전 후보로 보는 용도입니다.")
