import pandas as pd
import streamlit as st
from nav_labels import inject_sidebar_labels

from korea_holdings_ui import render_holdings_tab

st.set_page_config(page_title="HY DYNAMIC12 KOREA 보유종목", page_icon="💼", layout="wide")
inject_sidebar_labels()
st.title("💼 한국 보유종목 관리")
st.caption("실제 체결내역을 누적하고, 평균매수가·현재가·평가손익·현재 수익률을 자동 계산합니다.")

# 보유종목 화면의 원화 금액은 모두 123,123원 형식으로 표시합니다.
_original_dataframe = st.dataframe


def _won(value, signed=False):
    if pd.isna(value):
        return "-"
    try:
        number = float(value)
        if signed:
            return f"{number:+,.0f}원"
        return f"{number:,.0f}원"
    except (TypeError, ValueError):
        return value


def _formatted_dataframe(data=None, *args, **kwargs):
    if isinstance(data, pd.DataFrame):
        data = data.copy()
        money_columns = [
            "평균매수가(원)", "총매수금액(원)", "현재가(원)", "평가금액(원)",
            "평가손익(원)", "손절(-3%)(원)", "1차익절(+15%)(원)",
            "2차익절(+20%)(원)", "3차익절(+25%)(원)", "체결가(원)",
            "매수금액(원)", "누적평균가(원)",
        ]
        for col in money_columns:
            if col in data.columns:
                data[col] = data[col].map(lambda x, c=col: _won(x, signed=(c == "평가손익(원)")))
        column_config = kwargs.get("column_config")
        if isinstance(column_config, dict):
            column_config = dict(column_config)
            for col in money_columns:
                column_config.pop(col, None)
            kwargs["column_config"] = column_config
    return _original_dataframe(data, *args, **kwargs)


st.dataframe = _formatted_dataframe
render_holdings_tab()

