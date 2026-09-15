"""Read-only Naver fallback. Never infer net purchases from holdings or units."""
import math
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

SEOUL = ZoneInfo("Asia/Seoul")
BASE = "https://m.stock.naver.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": BASE + "/"}


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace(",", "").strip()
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None
    number = float(text)
    return number if math.isfinite(number) else None


def _get_json(path, params=None):
    response = requests.get(BASE + path, params=params or {}, headers=HEADERS, timeout=(3, 8))
    response.raise_for_status()
    return response.json()


def _code(value):
    text = str(value).zfill(6)
    # New Korean listings can have alphanumeric six-character short codes.
    return text if re.fullmatch(r"[0-9A-Z]{6}", text) else None


def completed_daily_date(now=None):
    """Do not treat intraday investor totals as finalized daily flow."""
    now = now or datetime.now(SEOUL)
    day = now.date() if now.hour >= 20 else now.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _date(value):
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


def get_market_cap_ranking(page_size=100):
    """Rank the complete KOSPI + KOSDAQ stock universe using exact KRW values.

    Both markets and every page must succeed; partial ranks are misleading.
    ETF/ETN entries are not part of the stock market-cap ranking.
    """
    page_size = min(100, max(1, int(page_size)))
    rows, dates, all_seen = [], [], set()
    for market in ("KOSPI", "KOSDAQ"):
        page, seen, total, rescans = 1, {}, None, 0
        while True:
            payload = _get_json(f"/api/stocks/marketValue/{market}", {"page": page, "pageSize": page_size})
            batch = payload.get("stocks") if isinstance(payload, dict) else None
            count = payload.get("totalCount") if isinstance(payload, dict) else None
            if not isinstance(batch, list) or not batch or not isinstance(count, int) or count <= 0:
                raise ValueError(f"NAVER {market}: invalid market-cap response")
            if total is None:
                total = count
            if count != total or page > 100:
                raise ValueError(f"NAVER {market}: market-cap pagination changed")
            for item in batch:
                code = _code(item.get("itemCode"))
                if not code or code in all_seen:
                    raise ValueError(f"NAVER {market}: invalid or cross-market duplicate code")
                seen[code] = item
            if len(seen) > total:
                raise ValueError(f"NAVER {market}: market-cap universe changed")
            if page * page_size >= total:
                if len(seen) == total:
                    break
                if rescans >= 2:
                    raise ValueError(f"NAVER {market}: incomplete market-cap universe {len(seen)}/{total}")
                # Prices move between page requests; merge up to two more passes to recover
                # entries shifted across a boundary. Never publish partial ranks.
                page, rescans = 1, rescans + 1
                continue
            if len(batch) < page_size:
                raise ValueError(f"NAVER {market}: incomplete market-cap page")
            page += 1
        all_seen.update(seen)
        for code, item in seen.items():
            if item.get("stockEndType") != "stock":
                continue
            cap = _num(item.get("marketValueRaw"))
            try:
                day = datetime.fromisoformat(item["localTradedAt"]).date()
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"NAVER {market}: missing market-cap date") from None
            if cap is None or cap <= 0:
                raise ValueError(f"NAVER {market}: missing exact KRW market cap")
            rows.append((code, cap, day.strftime("%Y%m%d")))
            dates.append(day)
    if not rows:
        raise ValueError("NAVER: no stock market-cap data")
    today = datetime.now(SEOUL).date()
    newest = max(dates)
    if newest > today or (today - newest).days > 10:
        raise ValueError("NAVER: stale market-cap snapshot")
    frame = pd.DataFrame(rows, columns=["종목코드", "시가총액", "시총기준일"])
    frame = frame.sort_values(["시가총액", "종목코드"], ascending=[False, True]).reset_index(drop=True)
    frame["현재순위"] = range(1, len(frame) + 1)
    return frame, newest.strftime("%Y%m%d")


def get_stock_flow(code, as_of=None):
    code = _code(code)
    if not code:
        return None
    cutoff = as_of or completed_daily_date()
    try:
        payload = _get_json(f"/api/stock/{code}/integration")
        if _code(payload.get("itemCode")) != code:
            return None
        candidates = []
        for item in payload.get("dealTrendInfos", []):
            day = _date(item.get("bizdate"))
            foreign = _num(item.get("foreignerPureBuyQuant"))
            institution = _num(item.get("organPureBuyQuant"))
            if day is None or day > cutoff or (cutoff - day).days > 10:
                continue
            if foreign is None or institution is None:
                continue
            candidates.append((day, foreign, institution))
        if candidates:
            day, foreign, institution = max(candidates, key=lambda item: item[0])
            return {"외국인순매수": foreign, "기관순매수": institution,
                    "기준일": day.strftime("%Y%m%d"), "단위": "주"}
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        pass
    return None


def get_flow_map(codes):
    # Sequential requests also work on hosts with exhausted thread quotas.
    cutoff = completed_daily_date()
    result = {}
    for code in dict.fromkeys(filter(None, (_code(value) for value in codes))):
        row = get_stock_flow(code, as_of=cutoff)
        if row:
            result[code] = row
    if not result:
        return {}, None
    date = max(row["기준일"] for row in result.values())
    # Cross-sectional ranks must not mix different trading days.
    return {code: row for code, row in result.items() if row["기준일"] == date}, date
