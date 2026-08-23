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


def _score_text(value):
    if value is None or pd.isna(value):
        return "데이터 없음"
    try:
        return f"{float(value):.1f}"
    except Exception:
        return str(value)


def _rank_text(value):
    if value is None or pd.isna(value):
        return "-"
    try:
        return f"{int(float(value)):,}"
    except Exception:
        return str(value)


def _weekday(date_obj):
    d = date_obj
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _safe_cap_snapshot(date_obj):
    """최근 실제 KRX 거래일의 KOSPI+KOSDAQ 시가총액 스냅샷을 반환한다."""
    if not PYKRX_OK:
        return pd.DataFrame(), None

    d = _weekday(date_obj)
    last_error = None
    for _ in range(15):
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
            except Exception as e:
                last_error = type(e).__name__

        if frames:
            out = pd.concat(frames, ignore_index=True).dropna(subset=["시가총액"])
            if not out.empty:
                out = out.sort_values("시가총액", ascending=False).reset_index(drop=True)
                out["시총순위"] = range(1, len(out) + 1)
                return out, date_s
        d -= timedelta(days=1)

    return pd.DataFrame(), f"ERROR:{last_error}" if last_error else None


@st.cache_data(ttl=3600)
def get_market_cap_momentum():
    """현재(최근 거래일), 4주 전, 12주 전 시총 순위를 비교한다."""
    today = datetime.now(SEOUL).date()
    now_df, now_date = _safe_cap_snapshot(today)
    w4_df, w4_date = _safe_cap_snapshot(today - timedelta(days=28))
    w12_df, w12_date = _safe_cap_snapshot(today - timedelta(days=84))

    meta = {"now": now_date, "w4": w4_date, "w12": w12_date}
    if now_df.empty:
        return pd.DataFrame(), meta

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
        if pd.isna(r.get("4주전순위")) or pd.isna(r.get("12주전순위")):
            return pd.NA
        d4 = float(r["4주순위변화"])
        d12 = float(r["12주순위변화"])
        rank_now = float(r["현재순위"])
        top_bonus = 10 if rank_now <= 30 else (5 if rank_now <= 60 else 0)
        return round(_clip(50 + d4 * 2.0 + d12 * 0.8 + top_bonus), 1)

    out["시총모멘텀점수"] = out.apply(cap_score, axis=1)
    return out, meta


def _call_flow(date_s, market, investor):
    try:
        return stock.get_market_net_purchases_of_equities_by_ticker(
            date_s, date_s, market=market, investor=investor
        )
    except TypeError:
        return stock.get_market_net_purchases_of_equities_by_ticker(
            date_s, date_s, market, investor
        )


@st.cache_data(ttl=1800)
def get_latest_krx_flow():
    """개장 전·휴일에도 직전 실제 거래일을 찾아 외국인/기관 수급을 가져온다."""
    if not PYKRX_OK:
        return {}, None, "pykrx 사용 불가"

    d = _weekday(datetime.now(SEOUL).date())
    last_error = None
    for _ in range(15):
        date_s = d.strftime("%Y%m%d")
        flow = {}
        rows_found = 0
        try:
            for investor, key in [("외국인", "외국인순매수"), ("기관합계", "기관순매수")]:
                for market in ["KOSPI", "KOSDAQ"]:
                    df = _call_flow(date_s, market, investor)
                    if df is None or df.empty or "순매수거래대금" not in df.columns:
                        continue
                    values = pd.to_numeric(df["순매수거래대금"], errors="coerce").fillna(0)
                    rows_found += len(values)
                    for code, val in values.items():
                        code = str(code).zfill(6)
                        flow.setdefault(code, {})[key] = float(val)
        except Exception as e:
            last_error = type(e).__name__
            flow = {}
            rows_found = 0

        if flow and rows_found > 0:
            return flow, date_s, "KRX 실데이터"
        d -= timedelta(days=1)

    reason = f"KRX 호출 실패({last_error})" if last_error else "최근 거래일 수급 없음"
    return {}, None, reason


def _build_flow_scores(rows, flow_map):
    """분석 후보군 안에서 외국인·기관 순매수거래대금의 상대 백분위 점수를 계산한다."""
    if not flow_map:
        return {}

    data = []
    for r in rows:
        code = str(r.get("_종목코드", "")).zfill(6)
        fm = flow_map.get(code)
        if not fm:
            continue
        data.append({
            "종목코드": code,
            "외국인": float(fm.get("외국인순매수", 0) or 0),
            "기관": float(fm.get("기관순매수", 0) or 0),
        })

    df = pd.DataFrame(data)
    if df.empty:
        return {}
    df["외국인점수"] = df["외국인"].rank(pct=True) * 100
    df["기관점수"] = df["기관"].rank(pct=True) * 100
    df["수급점수"] = (df["외국인점수"] + df["기관점수"]) / 2
    return dict(zip(df["종목코드"], df["수급점수"].round(1)))


def wealth_jump_score(row, cap_score, flow_score):
    """핵심 실데이터가 없으면 Conviction을 억지로 계산하지 않는다."""
    if cap_score is None or pd.isna(cap_score) or flow_score is None or pd.isna(flow_score):
        return None

    technical = float(row.get("기술점수", 50) or 50)
    fundamental = float(row.get("펀더멘털", 50) or 50)
    base = float(row.get("종합점수", 50) or 50)
    score = (
        float(cap_score) * 0.20
        + technical * 0.25
        + fundamental * 0.25
        + float(flow_score) * 0.20
        + base * 0.10
    )
    if row.get("과열") == "과열":
        score -= 8
    return round(_clip(score), 1)


def jump_grade(score, cap_score, flow_real, overheat, regime):
    if not flow_real or score is None or cap_score is None or pd.isna(cap_score):
        return "⚪ 데이터대기"
    if score >= 85 and float(cap_score) >= 65 and overheat != "과열" and regime != "약세장":
        return "🔥 S 집중후보"
    if score >= 75 and overheat != "과열":
        return "🟢 A 매수후보"
    if score >= 65:
        return "🟡 B 관찰"
    return "⚪ 대기"


def action_decision(r, regime):
    score = r.get("Conviction")
    cap = r.get("시총모멘텀")
    flow = r.get("수급점수")
    tech = float(r.get("기술점수", 50) or 50)
    overheat = r.get("과열") == "과열"
    d4 = r.get("4주순위변화")

    if score is None or cap is None or flow is None or pd.isna(cap) or pd.isna(flow):
        return "⏳ 데이터대기", "0%", "KRX 수급·시총 실데이터 확보 전 신규진입 보류"

    score = float(score)
    cap = float(cap)
    flow = float(flow)
    d4_num = 0.0 if d4 is None or pd.isna(d4) else float(d4)

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
        return "⏳ 대기", "0%", "후보 유지 · 신호 강화 대기"
    if score < 60 or flow < 40 or d4_num <= -5:
        return "⚠️ 비중축소", "-25%", "Conviction·수급·시총 모멘텀 약화"
    return "⏳ 대기", "0%", "조건 불충분"


def _reason_lines(r):
    reasons = []
    d4 = r.get("4주순위변화")
    if d4 is not None and pd.notna(d4) and float(d4) > 0:
        reasons.append(f"시총 순위가 4주간 {int(float(d4))}단계 상승")
    if float(r.get("기술점수", 0) or 0) >= 70:
        reasons.append(f"가격·추세 기술점수 {float(r.get('기술점수', 0)):.0f}점")
    flow = r.get("수급점수")
    if flow is not None and pd.notna(flow) and float(flow) >= 65:
        reasons.append(f"외국인·기관 수급점수 {float(flow):.0f}점")
    if float(r.get("펀더멘털", 0) or 0) >= 65:
        reasons.append(f"펀더멘털 점수 {float(r.get('펀더멘털', 0)):.0f}점")
    if not reasons:
        reasons.append("현재 정량 신호가 강하지 않아 추가 확인 필요")
    return reasons[:3]


def render_wealth_jump_tab(rows, regime="중립장", analysis_at=None):
    st.subheader("🚀 HY 부의 점프")
    st.caption("시총 순위 변화 + HY 기술·펀더멘털 + 최근 실제 거래일 외국인·기관 수급을 결합합니다.")

    if not rows:
        st.info("먼저 '전체시장 분석'에서 자동분석을 실행하세요.")
        return

    cap_df, cap_dates = get_market_cap_momentum()
    flow_map, flow_date, flow_source = get_latest_krx_flow()
    flow_scores = _build_flow_scores(rows, flow_map)

    cap_ok = not cap_df.empty and not str(cap_dates.get("now") or "").startswith("ERROR:")
    flow_ok = bool(flow_scores) and flow_date is not None

    s1, s2, s3 = st.columns(3)
    s1.metric("KRX 수급", f"✅ {flow_date}" if flow_ok else "❌ 데이터 없음")
    s2.metric("KRX 시총", f"✅ {cap_dates.get('now')}" if cap_ok else "❌ 데이터 없음")
    s3.metric("시장상태", regime)

    if not flow_ok:
        st.error(f"외국인·기관 수급 실데이터를 받지 못했습니다: {flow_source}. 수급 50점으로 대체하지 않습니다.")
    if not cap_ok:
        st.error("시가총액 순위 실데이터를 받지 못했습니다. 시총M 50점으로 대체하지 않습니다.")

    cap_map = cap_df.set_index("종목코드").to_dict("index") if cap_ok else {}
    jump_rows = []
    for row in rows:
        x = dict(row)
        code = str(x.get("_종목코드", "")).zfill(6)
        cap = cap_map.get(code, {})
        cap_score = cap.get("시총모멘텀점수")
        if cap_score is not None and pd.isna(cap_score):
            cap_score = None
        live_flow_score = flow_scores.get(code)

        x["현재순위"] = cap.get("현재순위")
        x["4주전순위"] = cap.get("4주전순위")
        x["12주전순위"] = cap.get("12주전순위")
        x["4주순위변화"] = cap.get("4주순위변화")
        x["12주순위변화"] = cap.get("12주순위변화")
        x["시총모멘텀"] = cap_score
        x["수급점수"] = live_flow_score
        x["수급실데이터"] = live_flow_score is not None
        x["Conviction"] = wealth_jump_score(x, cap_score, live_flow_score)
        x["점프등급"] = jump_grade(
            x["Conviction"], cap_score, x["수급실데이터"], x.get("과열", "정상"), regime
        )
        action, allocation, action_reason = action_decision(x, regime)
        x["실행"] = action
        x["권장진입"] = allocation
        x["실행근거"] = action_reason
        jump_rows.append(x)

    def sort_key(r):
        conviction = r.get("Conviction")
        if conviction is not None and pd.notna(conviction):
            return (1, float(conviction))
        return (0, float(r.get("종합점수", 0) or 0))

    jump_rows = sorted(jump_rows, key=sort_key, reverse=True)
    top = jump_rows[:10]

    st.markdown("## ⚡ 오늘의 실행판")
    st.caption("여기만 먼저 보세요. 실데이터가 없으면 매수 신호를 만들지 않습니다.")
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
            "Conviction": _score_text(r.get("Conviction")),
            "수급": _score_text(r.get("수급점수")),
            "시총M": _score_text(r.get("시총모멘텀")),
            "과열": r.get("과열"),
            "한줄판단": r.get("실행근거"),
        })
    st.dataframe(pd.DataFrame(action_display), use_container_width=True, hide_index=True)

    buy_now = [r for r in top if str(r.get("실행", "")).startswith("🚀")]
    if buy_now:
        names = ", ".join(r["종목명"] for r in buy_now[:3])
        st.success(f"오늘 1차매수 후보: {names} · 표의 1차매수가 부근에서만 분할진입")
    elif not flow_ok or not cap_ok:
        st.warning("필수 KRX 실데이터가 완전하지 않아 오늘의 신규매수 판정을 잠갔습니다.")
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
            "Conviction": _score_text(r.get("Conviction")),
            "등급": r.get("점프등급"),
            "현재 시총순위": _rank_text(r.get("현재순위")),
            "4주 변화": "-" if d4 is None or pd.isna(d4) else f"{float(d4):+.0f}",
            "12주 변화": "-" if d12 is None or pd.isna(d12) else f"{float(d12):+.0f}",
            "시총모멘텀": _score_text(r.get("시총모멘텀")),
            "기술": _score_text(r.get("기술점수")),
            "펀더멘털": _score_text(r.get("펀더멘털")),
            "수급": _score_text(r.get("수급점수")),
            "과열": r.get("과열"),
        })
    st.dataframe(pd.DataFrame(display), use_container_width=True, hide_index=True)

    st.markdown("### 📡 시총 레이더")
    if not cap_ok:
        st.info("시총 레이더 실데이터가 없습니다.")
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
    b.metric("Conviction", _score_text(selected_row.get("Conviction")))
    c.metric("진입비중", selected_row["권장진입"])
    d.metric("시총 모멘텀", _score_text(selected_row.get("시총모멘텀")))

    st.info(selected_row["실행근거"])
    st.markdown("**정량 매수근거 3가지**")
    for reason in _reason_lines(selected_row):
        st.write(f"- {reason}")

    warnings = []
    if selected_row.get("과열") == "과열":
        warnings.append("20일 고점 부근 + 모멘텀 과열: 추격매수 주의")
    d4 = selected_row.get("4주순위변화")
    if d4 is not None and pd.notna(d4) and float(d4) < 0:
        warnings.append(f"시총 순위가 4주간 {abs(int(float(d4)))}단계 하락")
    flow = selected_row.get("수급점수")
    if flow is not None and pd.notna(flow) and float(flow) < 45:
        warnings.append("외국인·기관 수급이 약함")
    if regime == "약세장":
        warnings.append("KOSPI 약세장: 집중비중 확대보다 방어 우선")

    if warnings:
        st.warning(" · ".join(warnings))
    elif selected_row.get("Conviction") is None:
        st.warning("실데이터 부족으로 종합판정을 보류합니다.")
    else:
        st.success("현재 정량 위험경고 없음")

    st.caption("KRX 수급·시총이 누락되면 50점으로 가정하지 않고 '데이터 없음'으로 표시하며 신규매수 판정을 잠급니다.")
