import streamlit as st

import korea_holdings_ui as holdings_ui
from korea_live_price import get_live_price, kis_ready, price_source_label

st.set_page_config(page_title="HY DYNAMIC12 KOREA 보유종목", page_icon="💼", layout="wide")
st.title("💼 한국 보유종목 관리")
st.caption("실제 체결내역을 누적하고, 평균매수가·현재가·평가손익·현재 수익률을 자동 계산합니다.")

# 보유종목 화면의 현재가 함수는 한국투자증권 KIS 실전 현재가를 최우선 사용합니다.
# KIS 호출이 실패하거나 키가 없을 때만 Yahoo Finance로 자동 폴백합니다.
holdings_ui.get_current_price = get_live_price

s1, s2 = st.columns([1.4, 1])
s1.info(f"📡 시세원: {price_source_label()}")
if s2.button("🔄 현재가 즉시 갱신", use_container_width=True):
    get_live_price.clear()
    st.rerun()

if not kis_ready():
    st.warning("실시간에 가까운 한국투자증권 현재가를 사용하려면 Streamlit Secrets에 KIS_APP_KEY와 KIS_APP_SECRET을 등록하세요. 지금은 Yahoo Finance로 자동 대체됩니다.")

holdings_ui.render_holdings_tab()
