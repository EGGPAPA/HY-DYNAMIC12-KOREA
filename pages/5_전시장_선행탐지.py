"""HY 사이드바 전 시장 선행 탐지 · 읽기 전용 독립 화면."""
import streamlit as st
from nav_labels import inject_sidebar_labels
from theme_styles import inject_theme
from early_market_dashboard import render_early_market_dashboard

st.set_page_config(page_title="HY 전 시장 선행 탐지", page_icon="🔎", layout="wide")
inject_theme()
inject_sidebar_labels()
render_early_market_dashboard()
