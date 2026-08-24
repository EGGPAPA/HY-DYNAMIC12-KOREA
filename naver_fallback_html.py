import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo

import pandas as pd
import requests

SEOUL = ZoneInfo("Asia/Seoul")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}


def _get_html(url, params=None, timeout=10):
    r = requests.get(url, params=params or {}, headers=HEADERS, timeout=timeout)
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


def _name_code_map(html):
    pairs = re.findall(
        r'href=["\']?/item/main\.naver\?code=(\d{6})["\']?[^>]*>([^<]+)</a>',
        html,
        flags=re.I,
    )
    return {re.sub(r"\s+", " ", name).strip(): code for code, name in pairs if name.strip()}


def get_full_universe_html(max_pages=45):
    """Return KOSPI/KOSDAQ stock code, name, market from Naver market-cap pages."""
    rows = []
    try:
        for sosok, market in [(0, "KOSPI"), (1, "KOSDAQ")]:
            empty_streak = 0
            for page in range(1, max_pages + 1):
                html = _get_html(
                    "https://finance.naver.com/sise/sise_market_sum.naver",
                    {"sosok": sosok, "page": page},
                )
                code_map = _name_code_map(html)
                if not code_map:
                    empty_streak += 1
                    if empty_streak >= 2:
                        break
                    continue
                empty_streak = 0
                for name, code in code_map.items():
                    rows.append({"종목코드": code, "종목명": name, "시장": market})
    except Exception:
        return pd.DataFrame(), None

    if not rows:
        return pd.DataFrame(), None
    df = pd.DataFrame(rows).drop_duplicates(subset=["종목코드"]).reset_index(drop=True)
    return df[["종목코드", "종목명", "시장"]], datetime.now(SEOUL).strftime("%Y%m%d")


def get_market_cap_ranking_html(max_pages=45):
    rows = []
    try:
        for sosok, market in [(0, "KOSPI"), (1, "KOSDAQ")]:
            empty_streak = 0
            for page in range(1, max_pages + 1):
                html = _get_html(
                    "https://finance.naver.com/sise/sise_market_sum.naver",
                    {"sosok": sosok, "page": page},
                )
                code_map = _name_code_map(html)
                tables = pd.read_html(StringIO(html))
                picked = next(
                    (
                        t
                        for t in tables
                        if "종목명" in [str(c) for c in t.columns]
                        and "시가총액" in [str(c) for c in t.columns]
                    ),
                    None,
                )
                if picked is None or picked.empty:
                    empty_streak += 1
                    if empty_streak >= 2:
                        break
                    continue
                t = picked.copy().dropna(subset=["종목명"])
                if t.empty:
                    empty_streak += 1
                    continue
                empty_streak = 0
                for _, r in t.iterrows():
                    name = re.sub(r"\s+", " ", str(r.get("종목명", ""))).strip()
                    cap = _clean_num(r.get("시가총액"))
                    code = code_map.get(name)
                    if name and code and cap is not None:
                        rows.append(
                            {
                                "종목코드": code,
                                "종목명": name,
                                "시장": market,
                                "시가총액": cap,
                            }
                        )
    except Exception:
        return pd.DataFrame(), None

    if not rows:
        return pd.DataFrame(), None
    df = pd.DataFrame(rows).drop_duplicates(subset=["종목코드"])
    df = df.sort_values("시가총액", ascending=False).reset_index(drop=True)
    df["현재순위"] = range(1, len(df) + 1)
    return df, datetime.now(SEOUL).strftime("%Y%m%d")


def get_stock_flow_html(code):
    try:
        html = _get_html(
            "https://finance.naver.com/item/frgn.naver",
            {"code": str(code).zfill(6)},
            timeout=8,
        )
        tables = pd.read_html(StringIO(html))
    except Exception:
        return None

    for t in tables:
        try:
            if isinstance(t.columns, pd.MultiIndex):
                t.columns = [
                    " ".join(str(x) for x in c if str(x) != "nan").strip()
                    for c in t.columns
                ]
            else:
                t.columns = [str(c).strip() for c in t.columns]

            inst_col = next((c for c in t.columns if "기관" in c), None)
            foreign_col = next(
                (
                    c
                    for c in t.columns
                    if "외국인" in c and "보유" not in c and "율" not in c
                ),
                None,
            )
            date_col = next((c for c in t.columns if "날짜" in c or "일자" in c), None)
            if not inst_col or not foreign_col:
                continue

            x = t.copy()
            if date_col:
                x = x[x[date_col].notna()]
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


def get_flow_map_html(codes, max_workers=12):
    """Fetch latest investor flow concurrently for a limited candidate set."""
    uniq = []
    seen = set()
    for code in codes:
        c = str(code).zfill(6)
        if c not in seen:
            uniq.append(c)
            seen.add(c)

    out = {}
    if not uniq:
        return out, None

    workers = max(2, min(int(max_workers), 16, len(uniq)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(get_stock_flow_html, code): code for code in uniq}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                row = fut.result()
            except Exception:
                row = None
            if row:
                out[code] = row

    return out, datetime.now(SEOUL).strftime("%Y%m%d") if out else None
