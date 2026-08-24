import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from naver_fallback_html import get_flow_map_html, get_market_cap_ranking_html

SEOUL = ZoneInfo("Asia/Seoul")
BASE = "https://m.stock.naver.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("원", "").replace("주", "").strip()
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _get_json(path, params=None):
    r = requests.get(BASE + path, params=params or {}, headers=HEADERS, timeout=8)
    r.raise_for_status()
    return r.json()


def _code_from(d):
    for k in ("itemCode", "stockCode", "code", "symbolCode"):
        v = d.get(k)
        if v is not None:
            s = re.sub(r"\D", "", str(v))
            if len(s) == 6:
                return s
    return None


def get_market_cap_ranking(page_size=1000):
    """Try Naver mobile API first; if blocked, fall back to classic Finance HTML."""
    try:
        data = _get_json(
            "/api/domestic/market/stock/default",
            {"tradeType": "KRX", "marketType": "ALL", "orderType": "marketSum", "startIdx": 0, "pageSize": page_size},
        )
        rows, seen = [], set()
        for d in _walk(data):
            code = _code_from(d)
            if not code or code in seen:
                continue
            cap = None
            for k, v in d.items():
                lk = str(k).lower()
                if any(t in lk for t in ("marketsum", "marketvalue", "marketcap", "capitalization")):
                    cap = _num(v)
                    if cap is not None:
                        break
            if cap is not None:
                seen.add(code)
                rows.append((code, cap))
        if rows:
            df = pd.DataFrame(rows, columns=["종목코드", "시가총액"]).sort_values("시가총액", ascending=False).reset_index(drop=True)
            df["현재순위"] = range(1, len(df) + 1)
            return df, datetime.now(SEOUL).strftime("%Y%m%d")
    except Exception:
        pass

    return get_market_cap_ranking_html()


def _find_investor_value(d, kind):
    tokens = ("foreign", "foreigner", "frgn", "외국") if kind == "foreign" else ("institution", "organization", "org", "기관")
    preferred, fallback = [], []
    for k, v in d.items():
        lk = str(k).lower()
        if not any(t in lk for t in tokens):
            continue
        n = _num(v)
        if n is None:
            continue
        if any(t in lk for t in ("net", "pure", "buy", "purchase", "순매수")):
            preferred.append(n)
        else:
            fallback.append(n)
    return preferred[0] if preferred else (fallback[0] if fallback else None)


def get_stock_flow(code):
    try:
        data = _get_json(f"/api/domestic/detail/{code}/trend", {"tradeType": "KRX", "startIdx": 0, "pageSize": 5})
        for d in _walk(data):
            f = _find_investor_value(d, "foreign")
            i = _find_investor_value(d, "institution")
            if f is not None or i is not None:
                return {"외국인순매수": float(f or 0), "기관순매수": float(i or 0)}
    except Exception:
        return None
    return None


def get_flow_map(codes):
    """Try Naver mobile API; if it yields no usable rows, use classic Finance HTML."""
    out = {}
    for code in codes:
        code = str(code).zfill(6)
        row = get_stock_flow(code)
        if row:
            out[code] = row
    if out:
        return out, datetime.now(SEOUL).strftime("%Y%m%d")
    return get_flow_map_html(codes)
