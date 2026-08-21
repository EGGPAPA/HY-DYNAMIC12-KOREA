import os
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"


def _secret(name, default=""):
    try:
        value = st.secrets.get(name, default)
        if value:
            return str(value).strip()
    except Exception:
        pass
    return os.getenv(name, default).strip()


def kis_ready():
    return bool(_secret("KIS_APP_KEY") and _secret("KIS_APP_SECRET"))


@st.cache_resource(show_spinner=False)
def _token_cache():
    return {"token": None, "expires_at": datetime.min}


def get_kis_access_token():
    if not kis_ready():
        return None

    cache = _token_cache()
    if cache.get("token") and datetime.now() < cache.get("expires_at", datetime.min):
        return cache["token"]

    try:
        r = requests.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": _secret("KIS_APP_KEY"),
                "appsecret": _secret("KIS_APP_SECRET"),
            },
            timeout=10,
        )
        data = r.json()
        token = data.get("access_token")
        if not r.ok or not token:
            return None

        expires_in = int(data.get("expires_in", 86400) or 86400)
        cache["token"] = token
        cache["expires_at"] = datetime.now() + timedelta(seconds=max(300, expires_in - 300))
        return token
    except Exception:
        return None


def get_kis_price(code):
    token = get_kis_access_token()
    if not token:
        return None

    try:
        r = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers={
                "authorization": f"Bearer {token}",
                "appkey": _secret("KIS_APP_KEY"),
                "appsecret": _secret("KIS_APP_SECRET"),
                "tr_id": "FHKST01010100",
                "custtype": "P",
                "content-type": "application/json; charset=utf-8",
            },
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": str(code).zfill(6)},
            timeout=10,
        )
        data = r.json()
        if not r.ok or str(data.get("rt_cd", "")) != "0":
            return None
        output = data.get("output") or {}
        price = output.get("stck_prpr")
        if price is not None and float(price) > 0:
            return float(price)
    except Exception:
        pass
    return None


def _yf_symbol(code, market):
    return f"{str(code).zfill(6)}.{ 'KQ' if str(market).upper() == 'KOSDAQ' else 'KS' }"


def get_yahoo_price(code, market):
    symbol = _yf_symbol(code, market)
    try:
        ticker = yf.Ticker(symbol)
        try:
            fi = ticker.fast_info
            for key in ("last_price", "regular_market_price", "previous_close"):
                try:
                    value = fi.get(key) if hasattr(fi, "get") else getattr(fi, key, None)
                    if value is not None and float(value) > 0:
                        return float(value)
                except Exception:
                    pass
        except Exception:
            pass

        h = ticker.history(period="5d", interval="1d", auto_adjust=False)
        if h is not None and not h.empty and "Close" in h.columns:
            s = pd.to_numeric(h["Close"], errors="coerce").dropna()
            if not s.empty:
                return float(s.iloc[-1])
    except Exception:
        pass
    return None


@st.cache_data(ttl=10, show_spinner=False)
def get_live_price(code, market):
    """KIS 현재가를 우선 사용하고 실패 시 Yahoo Finance로 폴백합니다."""
    price = get_kis_price(code)
    if price is not None:
        return price
    return get_yahoo_price(code, market)


def price_source_label():
    return "한국투자증권 KIS 실전 현재가 (10초 캐시)" if kis_ready() else "Yahoo Finance 폴백 (KIS 키 미설정)"
