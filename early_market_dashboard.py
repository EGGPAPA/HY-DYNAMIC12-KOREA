"""Standalone read-only early-market screening dashboard for the HY sidebar."""
import base64
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import altair as alt
import pandas as pd
import requests
import streamlit as st

from early_market_charts import get_naver_chart_rows
from early_entry_levels import compute_price_levels, checked_levels, decision_text, krw

REPO = "EGGPAPA/HY-DYNAMIC12-KOREA"
STATE_URL = f"https://api.github.com/repos/{REPO}/contents/data/ma_convergence_daily.json"
STATE_BRANCH = "monitor-state"


@st.cache_data(ttl=60, show_spinner=False)
def load_early_snapshot():
    """Read latest completed scan. Never publish or change monitoring state."""
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "Cache-Control": "no-cache"}
    try:
        key = str(st.secrets.get("GITHUB_PAT", "")).strip()
    except Exception:
        key = ""
    if key:
        headers["Authorization"] = f"Bearer {key}"
    response = requests.get(STATE_URL, headers=headers,
                            params={"ref": STATE_BRANCH}, timeout=15)
    response.raise_for_status()
    return json.loads(base64.b64decode(response.json()["content"]).decode("utf-8"))


@st.cache_data(ttl=900, show_spinner=False)
def load_early_chart(ticker, asof):
    # A ticker is fetched only when explicitly selected for its detail chart.
    return get_naver_chart_rows(ticker, asof, count=100)


def candidate_table(candidates, asof=None, *, extra_levels=None, stale=False):
    """Snapshot-only prices; missing values mean 'verify', NEVER invented buys."""
    extra_levels = extra_levels or {}
    records = []
    for i, item in enumerate(candidates, 1):
        signal = item.get("early_signal", {})
        if not (signal.get("valid") and signal.get("candidate")):
            continue
        levels = checked_levels(signal, asof or signal.get("asof", ""),
                                extra_levels.get(str(item.get("ticker", ""))))
        records.append({
            "순위": i,
            "종목": item.get("name", ""),
            "코드": item.get("ticker", ""),
            "시장": item.get("market", ""),
            "신호": f'{signal.get("signal_count", 0)}/5',
            "1차 관찰가": (f'{int(levels["watch_low"]):,}~{int(levels["watch_high"]):,}원'
                         if levels else "—"),
            "돌파 확인가": (f'{int(levels["breakout_ref"]):,}원 이상'
                          if levels else "—"),
            "손절 참고가": krw(levels["stop_ref"]) if levels else "—",
            "최종 매수 판단": decision_text(signal, levels, stale=stale),
            "RSI(14)": signal.get("rsi14"),
            "MACD 히스토그램": signal.get("macd_hist"),
            "거래량/20일": signal.get("volume_ratio"),
            "지수대비 5일(%p)": signal.get("excess5_pct"),
            "20일선 이격(%)": signal.get("gap20_pct")
        })
    return pd.DataFrame(records)


def show_charts(rows, period):
    df = pd.DataFrame(rows).copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.tail(period)
    x = alt.X("date:T", title="거래일", axis=alt.Axis(format="%m/%d"))
    direction_color = alt.condition(
        alt.datum.rise, alt.value("#31c8a0"), alt.value("#ea776d"))
    candles = alt.Chart(df)
    tooltip = [alt.Tooltip("date:T", title="거래일", format="%Y-%m-%d"),
               alt.Tooltip("open:Q", title="시가", format=",.0f"),
               alt.Tooltip("high:Q", title="고가", format=",.0f"),
               alt.Tooltip("low:Q", title="저가", format=",.0f"),
               alt.Tooltip("close:Q", title="종가", format=",.0f")]
    whiskers = candles.mark_rule().encode(
        x=x, y=alt.Y("low:Q", title="주가(원)", scale=alt.Scale(zero=False)),
        y2="high:Q", color=direction_color, tooltip=tooltip)
    bodies = candles.mark_bar(size=5).encode(
        x=x, y=alt.Y("open:Q", scale=alt.Scale(zero=False)),
        y2="close:Q", color=direction_color, tooltip=tooltip)
    ma = df.melt(id_vars=["date"], value_vars=["ma5", "ma20", "ma60"],
                 var_name="구분", value_name="값")
    averages = alt.Chart(ma).mark_line(strokeWidth=1.8).encode(
        x=x, y=alt.Y("값:Q", title="주가(원)", scale=alt.Scale(zero=False)),
        color=alt.Color("구분:N", scale=alt.Scale(
            domain=["ma5", "ma20", "ma60"],
            range=["#F2C14E", "#46A8E9", "#B28BF0"]),
            title="이동평균선"),
        tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"),
                 alt.Tooltip("구분:N"), alt.Tooltip("값:Q", format=",.0f")])
    st.altair_chart(alt.layer(whiskers, bodies, averages).properties(
        height=330, title="일봉 캔들 · 5 / 20 / 60일 이동평균선").interactive(),
        use_container_width=True)

    volume = alt.Chart(df).mark_bar().encode(
        x=x, y=alt.Y("volume:Q", title="거래량"),
        color=direction_color,
        tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"),
                 alt.Tooltip("volume:Q", title="거래량", format=",.0f")])
    st.altair_chart(volume.properties(height=120, title="거래량"),
                    use_container_width=True)

    rsi_line = alt.Chart(df).mark_line(strokeWidth=2, color="#F2C14E").encode(
        x=x, y=alt.Y("rsi14:Q", title="RSI(14)", scale=alt.Scale(domain=[0, 100])),
        tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"),
                 alt.Tooltip("rsi14:Q", format=".2f")])
    levels = alt.Chart(pd.DataFrame({"rsi14": [30, 70]})).mark_rule(
        color="#758597", strokeDash=[4, 4]).encode(y="rsi14:Q")
    st.altair_chart(alt.layer(rsi_line, levels).properties(
        height=160, title="RSI(14) · 참고선 30 / 70").interactive(),
        use_container_width=True)

    hist = alt.Chart(df).mark_bar(opacity=.65).encode(
        x=x, y=alt.Y("hist:Q", title="MACD"),
        color=alt.condition(alt.datum.hist >= 0, alt.value("#31c8a0"),
                            alt.value("#ea776d")),
        tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"),
                 alt.Tooltip("hist:Q", format=".3f")])
    lines = df.melt(id_vars=["date"], value_vars=["macd", "signal"],
                    var_name="선", value_name="지표값")
    macd = alt.Chart(lines).mark_line(strokeWidth=2).encode(
        x=x, y=alt.Y("지표값:Q", title="MACD"),
        color=alt.Color("선:N", scale=alt.Scale(
            domain=["macd", "signal"], range=["#46A8E9", "#F2C14E"]),
            title="MACD / 시그널"),
        tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"),
                 alt.Tooltip("선:N"), alt.Tooltip("지표값:Q", format=".3f")])
    st.altair_chart(alt.layer(hist, macd).properties(
        height=200, title="MACD(12,26,9) · 히스토그램").interactive(),
        use_container_width=True)


def render_early_market_dashboard():
    st.title("🔎 전 시장 선행 탐지")
    st.caption("KOSPI·KOSDAQ | RSI·MACD | 확정 일봉 기준 상승 초기 관찰 종목")
    st.info("관찰 전용 기술적 신호입니다. 기존 5-of-7의 실시간 매수판정이나 카카오 알림과는 별개입니다.")
    try:
        snapshot = load_early_snapshot()
    except Exception:
        st.error("GitHub의 최신 종목 검색 자료를 불러오지 못했습니다. 연결 상태를 확인해 주세요.")
        return
    if not snapshot.get("complete"):
        st.warning("전 시장 검색이 완료되지 않아 지난 결과 대신 평가를 보류합니다.")
        return
    report = snapshot.get("early_detector")
    if not isinstance(report, dict) or report.get("version") != "early_market_v1":
        st.warning("RSI·MACD 후보 자료가 아직 생성되지 않았습니다. 다음 확정 종가 전 시장 검색 후 표시됩니다.")
        return
    if not report.get("ready"):
        st.warning("선행 탐지 평가 보류 · " + str(report.get("reason") or "자료 확인 필요"))
        return
    asof = str(snapshot.get("asof", ""))
    if report.get("candidate_count", 0) < len(report.get("candidates", [])):
        st.error("저장된 후보 수가 일치하지 않아 표시를 보류합니다.")
        return
    stale = True
    try:
        age = (datetime.now(timezone(timedelta(hours=9))).date()
               - datetime.strptime(asof, "%Y-%m-%d").date()).days
        stale = age < 0 or age > 7
        if stale:
            st.warning(f"검색 기준일과 오늘 날짜가 {abs(age)}일 차이 납니다. 현재 매수 여부 판단에 사용하지 마세요.")
    except ValueError:
        st.warning("검색 기준일 형식 확인 필요")

    left, middle, right = st.columns(3)
    left.metric("평가 가능 종목", f'{report.get("evaluated_count", 0):,}개')
    middle.metric("상승 관찰 후보", f'{report.get("candidate_count", 0):,}개')
    right.metric("확정 종가 기준일", asof)

    candidates = [row for row in report.get("candidates", [])
                  if row.get("early_signal", {}).get("candidate") and
                  row.get("early_signal", {}).get("valid")]
    if not candidates:
        st.info("이번 기준일에 선정 조건을 충족한 종목이 없습니다.")
        return

    st.subheader("📋 선행 탐지 후보 목록")
    filter_a, filter_b = st.columns([2, 1])
    search = filter_a.text_input("종목명 또는 6자리 코드 검색", placeholder="예: 아모레퍼시픽, 090430").strip()
    market_filter = filter_b.selectbox("시장", ["전체", "KOSPI", "KOSDAQ"],
                                       key="early_market_type")
    matching = [row for row in candidates
                if (market_filter == "전체" or row.get("market") == market_filter)
                and (not search or search.lower() in
                     f'{row.get("name","")} {row.get("ticker","")}'.lower())]
    if not matching:
        st.info("검색 조건에 맞는 후보가 없습니다.")
        return
    # Legacy scan snapshots have no prior-20-close high or structural stop.
    # On user request only, fetch up to 30 existing candidate charts in parallel.
    old = st.session_state.get("early_manual_level_refs", {})
    if old.get("asof") != asof:
        old = {"asof": asof, "levels": {}, "errors": []}
    manual = old["levels"]
    need_levels = [row for row in candidates
                   if not checked_levels(row["early_signal"], asof,
                                         manual.get(str(row.get("ticker", ""))))]
    if need_levels:
        st.caption("일부 이전 검색 결과에는 가격 참고선이 없습니다. 아래 버튼으로 당시 확정 일봉을 확인할 수 있습니다.")
        if st.button("이전 후보 가격대 계산", key="early_rebuild_levels"):
            failures = []
            def load_one(row):
                return (row, get_naver_chart_rows(str(row["ticker"]), asof, count=100))
            with st.spinner("확정 일봉 가격대를 확인하는 중..."):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = {pool.submit(load_one, row): row for row in need_levels[:30]}
                    for fut in as_completed(futures):
                        row = futures[fut]
                        try:
                            chosen, bars = fut.result()
                            levels = compute_price_levels(bars, asof)
                            if not checked_levels(chosen["early_signal"], asof, levels):
                                raise ValueError("저장된 신호와 종가 불일치")
                            manual[str(row["ticker"])] = levels
                        except Exception:
                            failures.append(str(row.get("ticker", "")))
            old = {"asof": asof, "levels": manual, "errors": failures}
            st.session_state["early_manual_level_refs"] = old
            if failures:
                st.warning(f"{len(failures)}개 종목은 가격을 검증하지 못했습니다. 해당 가격은 표시하지 않습니다.")
    st.dataframe(candidate_table(matching, asof, extra_levels=manual, stale=stale),
                 hide_index=True, use_container_width=True,
                 height=min(580, 38 * (len(matching) + 1) + 4))
    st.caption("매수 참고선은 저장된 확정 일봉 기준입니다. 돌파 확인가는 직전 20거래일 최고 종가이며 거래량·실시간 HY 5-of-7 재확인이 필요합니다.")
    st.caption("신호 5개는 20일선 회복 · RSI 50 상향 · MACD 개선 · 거래량 증가 · 지수 초과수익입니다. 기존 5-of-7 매수신호와 다릅니다.")

    st.subheader("📈 개별 종목 상세 그래프")
    labels = {f'{row["name"]} ({row["ticker"]}, {row["market"]})': row
              for row in matching}
    selected = st.selectbox("차트를 확인할 종목", list(labels), key="early_market_selected")
    chosen = labels[selected]
    signal = chosen["early_signal"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("종가(기준일)", f'{signal.get("close", 0):,.0f}원')
    m2.metric("RSI(14)", f'{signal.get("rsi14", 0):.2f}')
    m3.metric("충족 신호", f'{signal.get("signal_count", 0)}/5')
    m4.metric("상대강도 5일", f'{signal.get("excess5_pct", 0):+.2f}%p')
    bar_len = st.radio("차트 표시기간", ["1개월", "3개월", "약 5개월"],
                       horizontal=True, index=2, key="early_market_chart_span")
    days = {"1개월": 22, "3개월": 63, "약 5개월": 100}[bar_len]
    try:
        with st.spinner("선택 종목의 확정 일봉 차트를 불러오는 중..."):
            records = load_early_chart(chosen["ticker"], asof)
    except Exception:
        st.warning("선택 종목의 차트를 확보하지 못했습니다. 기존 선정 결과는 유지되며, 최신 데이터나 공급처를 확인해 주세요.")
        return
    # A selected chart may supply missing legacy price references on demand.
    levels = checked_levels(signal, asof, manual.get(str(chosen["ticker"])))
    if levels is None:
        try:
            derived = compute_price_levels(records, asof)
            levels = checked_levels(signal, asof, derived)
        except (ValueError, TypeError, KeyError, OverflowError):
            levels = None
    l1, l2, l3, l4 = st.columns(4)
    l1.metric("1차 관찰가", (f'{int(levels["watch_low"]):,}~{int(levels["watch_high"]):,}원'
                            if levels else "확인 필요"))
    l2.metric("돌파 확인가", (krw(levels["breakout_ref"]) if levels else "확인 필요"))
    l3.metric("손절 참고가", (krw(levels["stop_ref"]) if levels else "확인 필요"))
    l4.metric("최종 매수 판단", decision_text(signal, levels, stale=stale))
    st.caption("가격대는 확정 일봉 참고치로, 실시간 매수 승인·주문 신호가 아닙니다. 지지 확인 없이 눌림목 가격만으로 매수하지 마세요.")
    # Snapshot comparisons are approximate due to vendor revisions and rounding.
    st.caption(f'{chosen["name"]} · {chosen["ticker"]} · {asof}까지 확정된 일봉 · Naver 원시 OHLCV')
    show_charts(records, days)
    # Optional completed-day trend assessment, completely separate from RSI/MACD signals.
    from minervini_ui import render_minervini_panel
    render_minervini_panel([chosen], asof=asof, key='minervini_early')
    with st.expander("해당 종목의 선행 신호 상세"):
        names = {"ma20_recovery":"20일선 회복", "rsi50_cross":"RSI 50 상향",
                 "macd_hist_rising":"MACD 히스토그램 상승",
                 "volume_1_5x":"거래량 1.5배",
                 "index_outperformance_5d":"지수 대비 5일 초과수익"}
        for key, name in names.items():
            st.write(("✅ " if signal.get("signals", {}).get(key) else "➖ ") + name)
        st.caption(f'기준일 거래량/20일 평균 {signal.get("volume_ratio", 0):.2f}배 · 20일선 이격 {signal.get("gap20_pct", 0):+.2f}%')
    st.caption("차트는 네이버가 제공한 원시 주가입니다. 수정주가·배당·권리변동을 완전히 반영하지 않을 수 있으며 투자 수익을 보장하지 않습니다.")
