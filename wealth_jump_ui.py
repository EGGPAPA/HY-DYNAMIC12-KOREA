from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from naver_fallback import get_flow_map as get_naver_flow_map
from naver_fallback import get_market_cap_ranking as get_naver_cap_ranking
from integrated_signal_ui import render_integrated_decision

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


def _weekday(d):
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _safe_cap_snapshot(date_obj):
    if not PYKRX_OK:
        return pd.DataFrame(), None
    d = _weekday(date_obj)
    for _ in range(15):
        date_s = d.strftime("%Y%m%d")
        frames = []
        for market in ["KOSPI", "KOSDAQ"]:
            try:
                df = stock.get_market_cap_by_ticker(date_s, market=market)
                if df is None or df.empty:
                    continue
                x = df.reset_index().copy()
                x = x.rename(columns={x.columns[0]: "종목코드"})
                if "시가총액" not in x.columns:
                    continue
                x["종목코드"] = x["종목코드"].astype(str).str.zfill(6)
                x["시가총액"] = pd.to_numeric(x["시가총액"], errors="coerce")
                frames.append(x[["종목코드", "시가총액"]])
            except Exception:
                continue
        if frames:
            out = pd.concat(frames, ignore_index=True).dropna(subset=["시가총액"])
            if not out.empty:
                out = out.sort_values("시가총액", ascending=False).reset_index(drop=True)
                out["현재순위"] = range(1, len(out) + 1)
                return out, date_s
        d -= timedelta(days=1)
    return pd.DataFrame(), None


@st.cache_data(ttl=3600)
def get_market_cap_data():
    today = datetime.now(SEOUL).date()
    now_df, now_date = _safe_cap_snapshot(today)
    w4_df, _ = _safe_cap_snapshot(today - timedelta(days=28))
    w12_df, _ = _safe_cap_snapshot(today - timedelta(days=84))

    if not now_df.empty:
        out = now_df.copy()
        if not w4_df.empty:
            out = out.merge(w4_df[["종목코드", "현재순위"]].rename(columns={"현재순위": "4주전순위"}), on="종목코드", how="left")
        else:
            out["4주전순위"] = pd.NA
        if not w12_df.empty:
            out = out.merge(w12_df[["종목코드", "현재순위"]].rename(columns={"현재순위": "12주전순위"}), on="종목코드", how="left")
        else:
            out["12주전순위"] = pd.NA
        out["4주순위변화"] = pd.to_numeric(out["4주전순위"], errors="coerce") - out["현재순위"]
        out["12주순위변화"] = pd.to_numeric(out["12주전순위"], errors="coerce") - out["현재순위"]

        def score(r):
            if pd.isna(r.get("4주전순위")) or pd.isna(r.get("12주전순위")):
                return None
            rank = float(r["현재순위"])
            d4 = float(r["4주순위변화"])
            d12 = float(r["12주순위변화"])
            bonus = 10 if rank <= 30 else (5 if rank <= 60 else 0)
            return round(_clip(50 + d4 * 2.0 + d12 * 0.8 + bonus), 1)

        out["시총모멘텀점수"] = out.apply(score, axis=1)
        return out, {"source": "KRX", "date": now_date, "full": True}

    nv, nv_date = get_naver_cap_ranking()
    if not nv.empty:
        out = nv.copy()
        out["4주전순위"] = pd.NA
        out["12주전순위"] = pd.NA
        out["4주순위변화"] = pd.NA
        out["12주순위변화"] = pd.NA

        def fallback_score(r):
            rank = float(r["현재순위"])
            if rank <= 30:
                return 65.0
            if rank <= 60:
                return 60.0
            if rank <= 100:
                return 55.0
            return 50.0

        out["시총모멘텀점수"] = out.apply(fallback_score, axis=1)
        return out, {"source": "NAVER 현재순위(대체)", "date": nv_date, "full": False}

    return pd.DataFrame(), {"source": "없음", "date": None, "full": False}


def _call_flow(date_s, market, investor):
    try:
        return stock.get_market_net_purchases_of_equities_by_ticker(date_s, date_s, market=market, investor=investor)
    except TypeError:
        return stock.get_market_net_purchases_of_equities_by_ticker(date_s, date_s, market, investor)


@st.cache_data(ttl=1800)
def get_krx_flow():
    if not PYKRX_OK:
        return {}, None
    d = _weekday(datetime.now(SEOUL).date())
    for _ in range(15):
        date_s = d.strftime("%Y%m%d")
        flow = {}
        try:
            for investor, key in [("외국인", "외국인순매수"), ("기관합계", "기관순매수")]:
                for market in ["KOSPI", "KOSDAQ"]:
                    df = _call_flow(date_s, market, investor)
                    if df is None or df.empty or "순매수거래대금" not in df.columns:
                        continue
                    vals = pd.to_numeric(df["순매수거래대금"], errors="coerce").fillna(0)
                    for code, val in vals.items():
                        flow.setdefault(str(code).zfill(6), {})[key] = float(val)
        except Exception:
            flow = {}
        if flow:
            return flow, date_s
        d -= timedelta(days=1)
    return {}, None


def _build_flow_scores(rows, flow_map):
    data = []
    for r in rows:
        code = str(r.get("_종목코드", "")).zfill(6)
        fm = flow_map.get(code)
        if not fm:
            continue
        data.append({"종목코드": code, "외국인": float(fm.get("외국인순매수", 0) or 0), "기관": float(fm.get("기관순매수", 0) or 0)})
    df = pd.DataFrame(data)
    if df.empty:
        return {}
    df["외국인점수"] = df["외국인"].rank(pct=True) * 100
    df["기관점수"] = df["기관"].rank(pct=True) * 100
    df["수급점수"] = (df["외국인점수"] + df["기관점수"]) / 2
    return dict(zip(df["종목코드"], df["수급점수"].round(1)))


def get_flow_data(rows):
    flow_map, flow_date = get_krx_flow()
    if flow_map:
        scores = _build_flow_scores(rows, flow_map)
        if scores:
            return scores, {"source": "KRX", "date": flow_date}

    codes = [str(r.get("_종목코드", "")).zfill(6) for r in rows]
    nv_map, nv_date = get_naver_flow_map(codes)
    scores = _build_flow_scores(rows, nv_map)
    if scores:
        return scores, {"source": "NAVER 투자자동향(대체)", "date": nv_date}
    return {}, {"source": "없음", "date": None}


def wealth_jump_score(row, cap_score, flow_score):
    if cap_score is None or flow_score is None or pd.isna(cap_score) or pd.isna(flow_score):
        return None
    technical = float(row.get("기술점수", 50) or 50)
    fundamental = float(row.get("펀더멘털", 50) or 50)
    base = float(row.get("종합점수", 50) or 50)
    score = float(cap_score) * .20 + technical * .25 + fundamental * .25 + float(flow_score) * .20 + base * .10
    if row.get("과열") == "과열":
        score -= 8
    return round(_clip(score), 1)


def action_decision(r, regime):
    score, cap, flow = r.get("Conviction"), r.get("시총모멘텀"), r.get("수급점수")
    tech = float(r.get("기술점수", 50) or 50)
    if score is None or cap is None or flow is None:
        return "⏳ 데이터대기", "0%", "수급·시총 데이터 확보 전 신규진입 보류"
    score, cap, flow = float(score), float(cap), float(flow)
    if regime == "약세장":
        return "⏳ 대기", "0%", "약세장에서는 신규진입보다 방어 우선"
    if r.get("과열") == "과열":
        return "⏳ 대기", "0%", "과열 구간 추격매수 금지"
    if score >= 85 and cap >= 65 and flow >= 60 and tech >= 70:
        return "🚀 1차매수", "30%", "S급 조건 충족 · 1차 분할진입"
    if score >= 78 and cap >= 55 and flow >= 55 and tech >= 65:
        return "🚀 1차매수", "20%", "A급 상단 · 소규모 1차 진입"
    if score >= 72 and flow >= 50 and tech >= 60:
        return "⏳ 대기", "0%", "후보 유지 · 신호 강화 대기"
    if score < 60 or flow < 40:
        return "⚠️ 비중축소", "-25%", "종합 확신도 또는 수급 약화"
    return "⏳ 대기", "0%", "조건 불충분"


def render_wealth_jump_tab(rows, regime="중립장", analysis_at=None):
    st.subheader("🚀 HY 부의 점프")
    st.caption("KRX를 우선 사용하고, KRX 호출이 막히면 네이버 금융 읽기 전용 데이터로 자동 보완합니다.")
    if not rows:
        st.info("먼저 '전체시장 분석'에서 자동분석을 실행하세요.")
        return

    cap_df, cap_meta = get_market_cap_data()
    flow_scores, flow_meta = get_flow_data(rows)
    cap_ok = not cap_df.empty
    flow_ok = bool(flow_scores)

    s1, s2, s3 = st.columns(3)
    s1.metric("수급", f"✅ {flow_meta['source']} · {flow_meta['date']}" if flow_ok else "❌ 데이터 없음")
    s2.metric("시총", f"✅ {cap_meta['source']} · {cap_meta['date']}" if cap_ok else "❌ 데이터 없음")
    s3.metric("시장상태", regime)

    if cap_ok and not cap_meta.get("full"):
        st.warning("KRX 시총 이력이 막혀 네이버 현재 시총순위로 대체했습니다. 4주·12주 순위변화는 표시하지 않습니다.")
    if not flow_ok:
        st.error("KRX와 네이버 모두 수급 데이터를 받지 못했습니다. 신규매수 판정을 잠급니다.")
    if not cap_ok:
        st.error("KRX와 네이버 모두 시총 데이터를 받지 못했습니다. 신규매수 판정을 잠급니다.")

    cap_map = cap_df.set_index("종목코드").to_dict("index") if cap_ok else {}
    jump_rows = []
    for row in rows:
        x = dict(row)
        code = str(x.get("_종목코드", "")).zfill(6)
        cap = cap_map.get(code, {})
        cap_score = cap.get("시총모멘텀점수")
        live_flow = flow_scores.get(code)
        x["현재순위"] = cap.get("현재순위")
        x["4주순위변화"] = cap.get("4주순위변화")
        x["12주순위변화"] = cap.get("12주순위변화")
        x["시총모멘텀"] = cap_score
        x["수급점수"] = live_flow
        x["Conviction"] = wealth_jump_score(x, cap_score, live_flow)
        x["실행"], x["권장진입"], x["실행근거"] = action_decision(x, regime)
        jump_rows.append(x)

    jump_rows.sort(key=lambda r: (r.get("Conviction") is not None, float(r.get("Conviction") or r.get("종합점수", 0) or 0)), reverse=True)
    top = jump_rows[:10]

    render_integrated_decision(rows, jump_rows, regime=regime)

    st.markdown("## ⚡ 오늘의 실행판")
    action_display = []
    for i, r in enumerate(top, 1):
        action_display.append({"순위": i, "종목": r.get("종목명"), "실행": r.get("실행"), "진입비중": r.get("권장진입"), "현재가": _won(r.get("현재가")), "1차매수가": _won(r.get("1차 매수가")), "2차매수가": _won(r.get("2차 매수가")), "종합 확신도": _score_text(r.get("Conviction")), "수급": _score_text(r.get("수급점수")), "시총M": _score_text(r.get("시총모멘텀")), "과열": r.get("과열"), "한줄판단": r.get("실행근거")})
    st.dataframe(pd.DataFrame(action_display), use_container_width=True, hide_index=True)

    buy_now = [r for r in top if str(r.get("실행", "")).startswith("🚀")]
    if buy_now:
        st.success("오늘 1차매수 후보: " + ", ".join(r["종목명"] for r in buy_now[:3]))
    elif not flow_ok or not cap_ok:
        st.warning("필수 데이터가 완전하지 않아 오늘의 신규매수 판정을 잠갔습니다.")
    else:
        st.info("오늘은 1차매수 조건을 모두 충족한 종목이 없습니다. 억지로 매수하지 않습니다.")

    st.markdown("### TOP10 종합 확신도")
    display = []
    for i, r in enumerate(top, 1):
        d4, d12 = r.get("4주순위변화"), r.get("12주순위변화")
        display.append({"순위": i, "종목": r.get("종목명"), "Conviction": _score_text(r.get("Conviction")), "현재 시총순위": _rank_text(r.get("현재순위")), "4주 변화": "-" if d4 is None or pd.isna(d4) else f"{float(d4):+.0f}", "12주 변화": "-" if d12 is None or pd.isna(d12) else f"{float(d12):+.0f}", "시총모멘텀": _score_text(r.get("시총모멘텀")), "기술": _score_text(r.get("기술점수")), "펀더멘털": _score_text(r.get("펀더멘털")), "수급": _score_text(r.get("수급점수")), "과열": r.get("과열")})
    st.dataframe(pd.DataFrame(display), use_container_width=True, hide_index=True)

    st.markdown("### 📡 시총 레이더")
    if not cap_ok:
        st.info("시총 데이터가 없습니다.")
    else:
        names = {str(r.get("_종목코드", "")).zfill(6): r.get("종목명", "") for r in rows}
        radar = cap_df.copy()
        radar["종목명"] = radar["종목코드"].map(names)
        radar = radar[radar["종목명"].notna()].head(12)
        cols = [c for c in ["종목명", "현재순위", "4주순위변화", "12주순위변화", "시총모멘텀점수"] if c in radar.columns]
        st.dataframe(radar[cols], use_container_width=True, hide_index=True)

    st.markdown("### 🔎 후보 상세")
    selected = st.selectbox("종목 선택", [r["종목명"] for r in top], key="wealth_jump_selected")
    r = next(x for x in top if x["종목명"] == selected)
    a, b, c, d = st.columns(4)
    a.metric("실행", r["실행"])
    b.metric("종합 확신도", _score_text(r.get("Conviction")))
    c.metric("진입비중", r["권장진입"])
    d.metric("시총 모멘텀", _score_text(r.get("시총모멘텀")))
    st.info(r["실행근거"])
    st.caption("KRX 실패 시 네이버 금융 읽기 전용 API를 보조 소스로 사용합니다. 네이버 시총 대체값은 현재 순위 기반이므로 4주·12주 순위변화보다 보수적으로 해석하세요.")
