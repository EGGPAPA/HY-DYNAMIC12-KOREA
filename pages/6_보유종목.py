import streamlit as st
from nav_labels import inject_sidebar_labels

from korea_holdings_ui import render_holdings_tab

st.set_page_config(page_title="HY DYNAMIC12 KOREA 보유종목", page_icon="💼", layout="wide")
inject_sidebar_labels()
st.title("💼 한국 보유종목 관리")
st.caption("실제 체결내역을 누적하고, 평균매수가·현재가·평가손익·현재 수익률을 자동 계산합니다.")

# st.dataframe 전역 변경은 Streamlit 재실행 시 재귀 호출을 만들 수 있으므로 사용하지 않습니다.
render_holdings_tab()
