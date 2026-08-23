from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

try:
    from pykrx import stock
    PYKRX_OK = True
except Exception:
    stock = None
    PYKRX_OK = False

SEOUL = ZoneInfo("Asia/Seoul")


def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(x)))


def _won(value):
    if value is None or pd.isna(value):
        return "-"
    try:
        return f"{int(round(float(value))):,}원"
    except Exception:
        return str(value)


def _weekday(date_obj):
    d = date_obj
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _safe_cap_snapshot(date_obj):
    """Return the nearest KRX market-cap snapshot on or before date_obj."""
    if not PYKRX_OK:
        return pd.DataFrame(), None

    d = _weekday(date_obj)
    for _ in range(10):
        date_s = d.strftime("%Y%m%d")
        frames = []
        for market in ["KOSPI", "KOSDAQ"]:
            try:
                df = stock.get_market_cap_by_ticker(date_s, market=market)
                if df is None or df.empty:
                    continue
                x = df.reset_index().copy()
                code_col = x.columns[0]
                x = x.rename(columns={code_col: "종목코드"})
                if "시가총액" not in x.columns:
                    continue
                x["종목코드"] = x["종목코드"].astype(str).str.zfill(6)
                x["시가총액"] = pd.to_numeric(x["시가총액"], errors="coerce")
                x["시장"] = market
                frames.append(x[["종목코드", "시장", "시가총액"]])
            except Exception:
                continue

        if frames:
            out = pd.concat(frames, ignore_index=True).dropna(subset=["시가총액"])
            if not out.empty:
                out = out.sort_values("시가총액", ascending=False).reset_index(drop=True)
                out["시총순위"] = range(1, len(out) + 1)
                return out, date_s
        d -= timedelta(days=1)

    return pd.DataFrame(), None


@st.cache_data(ttl=3600)
def get_market_cap_momentum():
    """Compare current total-market cap ranks with 4-week and 12-week snapshots."""
    today = datetime.now(SEOUL).date()
    now_df, now_date = _safe_cap_snapshot(today)
    w4_df, w4_date = _safe_cap_snapshot(today - timedelta(days=28))
    w12_df, w12_date = _safe_cap_snapshot(today - timedelta(days=84))

    if now_df.empty:
        return pd.DataFrame(), {"now": now_date, "w4": w4_date, "w12": w12_date}

    out = now_df[["종목코드", "시장", "시가총액", "시총순위"]].copy()
    out = out.rename(columns={"시총순위": "현재순위"})

    if not w4_df.empty:
        out = out.merge(
            w4_df[["종목코드", "시총순위"]].rename(columns={"시총순위": "4주전순위"}),
            on="종목코드",
            how="left",
        )
    else:
        out["4주전순위"] = pd.NA

    if not w12_df.empty:
        out = out.merge(
            w12_df[["종목코드", "시총순위"]].rename(columns={"시총순위": "12주전순위"}),
            on="종목코드",
            how="left",
        )
    else:
        out["12주전순위"] = pd.NA

    out["4주순위변화"] = pd.to_numeric(out["4주전순위"], errors="coerce") - out["현재순위"]
    out["12주순위변화"] = pd.to_numeric(out["12주전순위"], errors="coerce") - out["현재순위"]

    def cap_score(r):
        d4 = 0 if pd.isna(r["4주순위변화"]) else float(r["4주순위변화"])
        d12 = 0 if pd.isna(r["12주순위변화"]) else float(r["12주순위변화"])
        rank_now = float(r["현재순위"])
        top_bonus = 10 if rank_now <= 30 else (5 if rank_now <= 60 else 0)
        return round(_clip(50 + d4 * 2.0 + d12 * 0.8 + top_bonus), 1)

    out["시총모멘텀점수"] = out.apply(cap_score, axis=1)
    return out, {"now": now_date, "w4": w4_date, "w12": w12_date}


def wealth_jump_score(row, cap_score=50.0):
    technical = float(row.get("기술점수", 50) or 50)
    fundamental = float(row.get("펀더멘털", 50) or 50)
    flow = float(row.get("수급점수", 50) or 50)
    base = float(row.get("종합점수", 50) or 50)

    score = (
        cap_score * 0.20
        + technical * 0.25
        + fundamental * 0.25
        + flow * 0.20
        + base * 0.10
    )
    if row.get("과열") == "과열":
        score -= 8
    return round(_clip(score), 1)


def jump_grade(score, cap_score, flow_real, overheat, regime):
    if not flow_real:
        return "🔵 관찰"
    if score >= 85 and cap_score >= 65 and overheat != "과열" and regime != "약세장":
        return "🔥 S 집중후보"
    if score >= 75 and overheat != "과열":
        return "🟢 A 매수후보"
    if score >= 65:
        return "🟡 B 관찰"
    return "⚪ 대기"


def action_decision(r, regime):
    """Translate scores into a simple execution-oriented label without placing orders."""
    score = float(r.get("Conviction", 0) or 0)
    cap = float(r.get("시총모멘텀", 50) or 50)
    flow = float(r.get("수급점수", 50) or 50)
    tech = float(r.get("기술점수", 50) or 50)
    overheat = r.get("과열") == "과열"
    flow_real = bool(r.get("수급실데이터"))
    d4 = r.get("4주순위변화")
    d4_num = 0.0 if pd.isna(d4) else float(d4)

    if not flow_real:
        return "⏳ 대기", "0%", "수급 데이터 확인 전 신규진입 보류"
    if regime == "약세장":
        if score < 60 or flow < 40 or d4_num <= -5:
            return "🔴 매도검토", "0%", "약세장 + 종목 신호 악화"
        return "⏳ 대기", "0%", "약세장에서는 신규진입보다 방어 우선"
    if overheat:
        return "⏳ 대기", "0%", "과열 구간 추격매수 금지"
    if score >= 85 and cap >= 65 and flow >= 60 and tech >= 70:
        return "🚀 1차매수", "30%", "S급 조건 충족 · 1차 분할진입"
    if score >= 78 and cap >= 55 and flow >= 55 and tech >= 65:
        return "🚀 1차매수", "20%", "A급 상단 · 소규모 1차 진입"
    if score >= 72 and flow >= 50 and tech >= 60:
        return "⏳ 대기", "0%", "후보는 유지하되 신호 강화 대기"
    if score < 60 or flow < 40 or d4_num <= -5:
        return "⚠️ 비중축소", "-25%", "Conviction·수급·시총 모멘텀 약화"
    return "⏳ 대기", "0%", "조건 불충분"


def _reason_lines(r):
    reasons = []
    d4 = r.get("4주순위변화")
    if pd.notna(d4) and float(d4) > 0:
        reasons.append(f"시총 순위가 4주간 {int(float(d4))}단계 상승")
    if float(r.get("기술점수", 0) or 0) >= 70:
        reasons.append(f"가격·추세 기술점수 {float(r.get('기술점수', 0)):.0f}점")
    if float(r.get("수급점수", 0) or 0) >= 65:
        reasons.append(f"외국인·기관 수급점수 {float(r.get('수급점수', 0)):.0f}점")
    if float(r.get("펀더멘털", 0) or 0) >= 65:
        reasons.append(f"펀더멘털 점수 {float(r.get('펀더멘털', 0)):.0f}점")
    if not reasons:
        reasons.append("현재 정량 신호가 강하지 않아 추가 확인 필요")
    return reasons[:3]


def render_wealth_jump_tab(rows, regime="중립장", analysis_at=None):
    st.subheader("🚀 HY 부의 점프")
    st.caption("시총 순위 변화 + 기존 HY 기술·펀더멘털·수급 점수를 결합해 '집중 연구할 후보'를 찾습니다.")

    if not rows:
        st.info("먼저 '전체시장 분석'에서 자동분석을 실행하세요.")
        return

    cap_df, dates = get_market_cap_momentum()
    if cap_df.empty:
        st.warning("KRX 시가총액 데이터를 가져오지 못해 시총 모멘텀은 중립값 50점으로 계산합니다.")
        cap_map = {}
    else:
        cap_map = cap_df.set_index("종목코드").to_dict("index")
        st.caption(
            f"시총 기준일: 현재 {dates.get('now') or '-'} · 4주 {dates.get('w4') or '-'} · 12주 {dates.get('w12') or '-'}"
        )

    jump_rows = []
    for row in rows:
        x = dict(row)
        code = str(x.get("_종목코드", "")).zfill(6)
        cap = cap_map.get(code, {})
        cap_score = float(cap.get("시총모멘텀점수", 50) or 50)
        x["현재순위"] = cap.get("현재순위")
        x["4주전순위"] = cap.get("4주전순위")
        x["12주전순위"] = cap.get("12주전순위")
        x["4주순위변화"] = cap.get("4주순위변화")
        x["12주순위변화"] = cap.get("12주순위변화")
        x["시총모멘텀"] = cap_score
        x["Conviction"] = wealth_jump_score(x, cap_score)
        x["점프등급"] = jump_grade(
            x["Conviction"], cap_score, bool(x.get("수급실데이터")), x.get("과열", "정상"), regime
        )
        action, allocation, action_reason = action_decision(x, regime)
        x["실행"] = action
        x["권장진입"] = allocation
        x["실행근거"] = action_reason
        jump_rows.append(x)

    jump_rows = sorted(jump_rows, key=lambda x: x["Conviction"], reverse=True)
    top = jump_rows[:10]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("시장상태", regime)
    c2.metric("🚀 1차매수", sum(r["실행"].startswith("🚀") for r in top))
    c3.metric("🔥 S급", sum(str(r["점프등급"]).startswith("🔥") for r in top))
    c4.metric("분석시각", analysis_at or "확인 불가")

    st.markdown("## ⚡ 오늘의 실행판")
    st.caption("이 표만 먼저 보세요. 매수는 반드시 분할진입 기준이며 자동 주문은 실행하지 않습니다.")
    action_display = []
    for i, r in enumerate(top, 1):
        action_display.append({
            "순위": i,
            "종목": r.get("종목명"),
            "실행": r.get("실행"),
            "진입비중": r.get("권장진입"),
            "현재가": _won(r.get("현재가")),
            "1차매수가": _won(r.get("1차 매수가")),
            "2차매수가": _won(r.get("2차 매수가")),
            "Conviction": r.get("Conviction"),
            "수급": r.get("수급점수"),
            "시총M": r.get("시총모멘텀"),
            "과열": r.get("과열"),
            "한줄판단": r.get("실행근거"),
        })
    st.dataframe(pd.DataFrame(action_display), use_container_width=True, hide_index=True)

    buy_now = [r for r in top if r["실행"].startswith("🚀")]
    if buy_now:
        names = ", ".join(r["종목명"] for r in buy_now[:3])
        st.success(f"오늘 1차매수 후보: {names} · 표의 1차매수가 부근에서만 분할진입")
    else:
        st.info("오늘은 1차매수 조건을 모두 충족한 종목이 없습니다. 억지로 매수하지 않습니다.")

    st.markdown("### TOP10 Conviction")
    display = []
    for i, r in enumerate(top, 1):
        d4 = r.get("4주순위변화")
        d12 = r.get("12주순위변화")
        display.append({
            "순위": i,
            "종목": r.get("종목명"),
            "Conviction": r.get("Conviction"),
            "등급": r.get("점프등급"),
            "현재 시총순위": r.get("현재순위"),
            "4주 변화": None if pd.isna(d4) else round(float(d4), 0),
            "12주 변화": None if pd.isna(d12) else round(float(d12), 0),
            "시총모멘텀": r.get("시총모멘텀"),
            "기술": r.get("기술점수"),
            "펀더멘털": r.get("펀더멘털"),
            "수급": r.get("수급점수"),
            "과열": r.get("과열"),
        })
    st.dataframe(pd.DataFrame(display), use_container_width=True, hide_index=True)

    st.markdown("### 📡 시총 레이더")
    if cap_df.empty:
        st.info("시총 레이더 데이터가 없습니다.")
    else:
        universe_names = {str(r.get("_종목코드", "")).zfill(6): r.get("종목명", "") for r in rows}
        radar = cap_df.copy()
        radar["종목명"] = radar["종목코드"].map(universe_names)
        radar = radar[radar["종목명"].notna()]
        radar = radar.sort_values(["4주순위변화", "12주순위변화"], ascending=False).head(12)
        radar_display = radar[["종목명", "현재순위", "4주전순위", "4주순위변화", "12주전순위", "12주순위변화", "시총모멘텀점수"]].copy()
        st.dataframe(radar_display, use_container_width=True, hide_index=True)

    st.markdown("### 🔎 후보 상세")
    selected = st.selectbox("종목 선택", [r["종목명"] for r in top], key="wealth_jump_selected")
    selected_row = next(r for r in top if r["종목명"] == selected)

    a, b, c, d = st.columns(4)
    a.metric("실행", selected_row["실행"])
    b.metric("Conviction", f"{selected_row['Conviction']:.1f}")
    c.metric("진입비중", selected_row["권장진입"])
    d.metric("시총 모멘텀", f"{selected_row['시총모멘텀']:.1f}")

    st.info(selected_row["실행근거"])
    st.markdown("**정량 매수근거 3가지**")
    for reason in _reason_lines(selected_row):
        st.write(f"- {reason}")

    warnings = []
    if selected_row.get("과열") == "과열":
        warnings.append("20일 고점 부근 + 모멘텀 과열: 추격매수 주의")
    d4 = selected_row.get("4주순위변화")
    if pd.notna(d4) and float(d4) < 0:
        warnings.append(f"시총 순위가 4주간 {abs(int(float(d4)))}단계 하락")
    if float(selected_row.get("수급점수", 50) or 50) < 45:
        warnings.append("외국인·기관 수급이 약함")
    if regime == "약세장":
        warnings.append("KOSPI 약세장: 집중비중 확대보다 방어 우선")

    if warnings:
        st.warning(" · ".join(warnings))
    else:
        st.success("현재 정량 위험경고 없음")

    st.caption("실행판은 정량 신호를 행동으로 단순화한 보조 도구입니다. 자동 주문을 실행하지 않으며 기업·산업의 정성적 확인은 별도로 필요합니다.")