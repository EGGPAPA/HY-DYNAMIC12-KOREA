"""Read-only HY panel. Consumes the completed daily scanner snapshot only."""
import pandas as pd
import streamlit as st


def render_early_market_panel(snapshot):
    st.caption("시험 기능 · 일일 확정 종가 기준 · 매수 권고/실시간 알림 아님")
    if not isinstance(snapshot, dict) or not snapshot.get("complete"):
        st.info("완료된 전 시장 검색 자료가 없어 선행 신호 평가를 보류합니다.")
        return
    record = snapshot.get("early_detector", {})
    if record.get("version") != "early_market_v1":
        st.info("선행 탐지기는 신규 일일 검색을 완료한 뒤 표시됩니다. 기존 관찰목록은 유지됩니다.")
        return
    asof = snapshot.get("asof", "")
    if not record.get("ready"):
        st.warning("선행 탐지 보류 · " + record.get("reason", "지수 기준 자료 확인 필요"))
        return
    candidates = record.get("candidates", [])
    st.caption(f"기준일: {asof} · 평가 가능 {record.get('evaluated_count', 0):,}종목 · "
               f"조건 충족 {record.get('candidate_count', 0):,}종목 · 상위 {len(candidates)}개 표시")
    if not candidates:
        st.info("설정된 조건을 충족한 초기 관찰 후보가 없습니다.")
        return
    view = []
    for item in candidates:
        signal = item.get("early_signal", {})
        if not signal.get("valid") or not signal.get("candidate"):
            continue
        view.append({
            "종목": item.get("name", ""),
            "코드": item.get("ticker", ""),
            "시장": item.get("market", ""),
            "신호(5개)": signal.get("signal_count"),
            "RSI(14)": signal.get("rsi14"),
            "MACD 히스토그램": signal.get("macd_hist"),
            "거래량/20일": signal.get("volume_ratio"),
            "지수 대비 5일(%p)": signal.get("excess5_pct"),
            "20일선 이격(%)": signal.get("gap20_pct"),
        })
    if view:
        st.dataframe(pd.DataFrame(view), hide_index=True, use_container_width=True)
    st.caption("수급·업종 동반상승·실적 검증은 아직 포함되지 않았습니다. "
               "전 시장 후보를 기존 A/B 관찰목록에 자동 추가하거나 카카오 알림을 전송하지 않습니다.")
