from io import StringIO
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

SEOUL = ZoneInfo("Asia/Seoul")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}


def _get_html(url, params=None):
    r = requests.get(url, params=params or {}, headers=HEADERS, timeout=10)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = "euc-kr"
    return r.text


def _clean_num(v):
    if v is None or pd.isna(v):
        return None
    s = str(v).replace(",", "").replace("+", "").strip()
    try:
        return float(s)
    except Exception:
        return None


def get_market_cap_ranking_html(max_pages=40):
    """Read Naver Finance KOSPI/KOSDAQ market-cap tables and build a combined ranking."""
    rows = []
    try:
        for sosok, market in [(0, "KOSPI"), (1, "KOSDAQ")]:
            for page in range(1, max_pages + 1):
                html = _get_html(
                    "https://finance.naver.com/sise/sise_market_sum.naver",
                    {"sosok": sosok, "page": page},
                )
                tables = pd.read_html(StringIO(html))
                picked = None
                for t in tables:
                    cols = [str(c) for c in t.columns]
                    if "종목명" in cols and "시가총액" in cols:
                        picked = t
                        break
                if picked is None or picked.empty:
                    break
                t = picked.copy()
                t = t.dropna(subset=["종목명"])
                if t.empty:
                    break
                for _, r in t.iterrows():
                    name = str(r.get("종목명", "")).strip()
                    cap = _clean_num(r.get("시가총액"))
                    if not name or cap is None:
                        continue
                    rows.append({"종목명": name, "시장": market, "시가총액": cap})
    except Exception:
        return pd.DataFrame(), None

    if not rows:
        return pd.DataFrame(), None
    df = pd.DataFrame(rows).drop_duplicates(subset=["종목명", "시장"])
    df = df.sort_values("시가총액", ascending=False).reset_index(drop=True)
    df["현재순위"] = range(1, len(df) + 1)
    return df, datetime.now(SEOUL).strftime("%Y%m%d")


def get_stock_flow_html(code):
    """Read the latest foreign/institution net-buy row from Naver Finance HTML."""
    try:
        html = _get_html("https://finance.naver.com/item/frgn.naver", {"code": str(code).zfill(6)})
        tables = pd.read_html(StringIO(html))
    except Exception:
        return None

    for t in tables:
        try:
            # Flatten possible MultiIndex columns.
            if isinstance(t.columns, pd.MultiIndex):
                t.columns = [" ".join(str(x) for x in c if str(x) != "nan").strip() for c in t.columns]
            else:
                t.columns = [str(c).strip() for c in t.columns]

            inst_col = next((c for c in t.columns if "기관" in c), None)
            foreign_cols = [c for c in t.columns if "외국인" in c]
            foreign_col = next((c for c in foreign_cols if "보유" not in c and "율" not in c), None)
            date_col = next((c for c in t.columns if "날짜" in c or "일자" in c), None)
            if not inst_col or not foreign_col:
                continue

            x = t.copy()
            if date_col:
                x = x[x[date_col].notna()]
            if x.empty:
                continue

            for _, r in x.iterrows():
                inst = _clean_num(r.get(inst_col))
                foreign = _clean_num(r.get(foreign_col))
                if inst is None and foreign is None:
                    continue
                return {
                    "외국인순매수": float(foreign or 0),
                    "기관순매수": float(inst or 0),
                }
        except Exception:
            continue
    return None


def get_flow_map_html(codes):
    out = {}
    for code in codes:
        code = str(code).zfill(6)
        row = get_stock_flow_html(code)
        if row:
            out[code] = row
    return out, datetime.now(SEOUL).strftime("%Y%m%d") if out else None
