from datetime import date, timedelta

import pandas as pd
import streamlit as st
import yfinance as yf

import korea_backtest_ui as bt

st.set_page_config(page_title="HY DYNAMIC12 하이브리드", page_icon="🧩", layout="wide")

ETF_MAP = {
    "TIGER 코리아TOP10": "292150.KS",
    "KODEX 200": "069500.KS",
}


def _series(raw):
    if raw is None or raw.empty:
        return pd.Series(dtype=float)
    s = raw["Close"]
    if isinstance(s, pd.DataFrame):
        s = s