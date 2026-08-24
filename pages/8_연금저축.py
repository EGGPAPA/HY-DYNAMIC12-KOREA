import streamlit as st
from pension_manager_ui import render_pension_manager_tab
from theme_styles import inject_theme

st.set_page_config(page_title="HY DYNAMIC12 · 연금저축", page_icon="🏦", layout="wide")
inject_theme()
st.title("🏦 HY DYNAMIC12 · 연금저축 관리")
render_pension_manager_tab()
