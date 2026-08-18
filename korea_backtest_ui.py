from datetime import date, timedelta
import math

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

UNIVERSE = [
    ("005930", "삼성전자", "KOSPI"), ("000660", "SK하이닉스", "KOSPI"),
    ("035420", "NAVER", "KOSPI"), ("035720", "카카오", "KOSPI"),
    ("005380", "현대차", "KOSPI"), ("000270", "기아", "KOSPI"),
    ("207940", "삼성바이오로직스", "KOSPI"), ("068270", "셀트리온", "KOSPI"),
    ("373220", "LG에너지솔루션", "KOSPI"), ("006400", "삼성SDI", "KOSPI"),
    ("005490", "POSCO홀딩스", "KOSPI"), (