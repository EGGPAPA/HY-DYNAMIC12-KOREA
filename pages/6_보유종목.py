import streamlit as st

from korea_holdings_ui import render_holdings_tab

st.set_page_config(page_title="HY DYNAMIC12 KOREA 보유종목", page_icon="💼", layout="wide")
st.title("💼 한국 보유종목 관리")
st.caption("실제 체결내역을 누적하고, 평균매수가·현재가·평가손익·현재 수익률을 자동 계산합니다.")

# 시세원 안내와 현재가 즉시 갱신 버튼은 render_holdings_tab() 안에서 한 번만 표시합니다.
render_holdings_tab()
