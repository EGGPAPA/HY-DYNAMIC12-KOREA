"""Read-only completed-day Naver chart history for HY early market candidates.

No orders, credentials, alerts or changes to saved HY state.
Chart RSI/MACD deliberately reuse early_market_detector's Wilder RSI / EMA
so their values are aligned with the daily screening signal.
"""
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
import math

from early_market_detector import _ema, _rsi

MIN_BARS = 85


def parse_ohlcv(raw, asof):
    """Parse Naver daily OHLCV, discard future/unclosed bars and reject bad rows."""
    if not isinstance(asof, str) or len(asof) != 10:
        raise ValueError("확정 종가 기준일 확인 필요")
    if isinstance(raw, bytes):
        raw = raw.decode("euc-kr")
    root = ET.fromstring(raw)
    out = []
    for node in root.iter("item"):
        values = node.attrib.get("data", "").split("|")
        if len(values) != 6:
            continue
        stamp, op, high, low, close, volume = values
        if len(stamp) != 8 or not stamp.isdigit():
            continue
        date = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"
        if date > asof:
            continue
        try:
            datetime.strptime(date, "%Y-%m-%d")
            op, high, low, close, volume = map(float, (op, high, low, close, volume))
        except (ValueError, TypeError):
            continue
        if not all(math.isfinite(x) for x in (op, high, low, close, volume)):
            continue
        if min(op, high, low, close) <= 0 or volume < 0 or high < max(op, close, low) or low > min(op, close, high):
            continue
        out.append({"date": date, "open": op, "high": high, "low": low,
                    "close": close, "volume": volume})
    out.sort(key=lambda row: row["date"])
    if len({row["date"] for row in out}) != len(out):
        raise ValueError("주가 데이터 중복 거래일")
    if len(out) < MIN_BARS or out[-1]["date"] != asof:
        raise ValueError("차트 확정 거래일 불일치 또는 일봉 부족")
    if out[-1]["volume"] <= 0:
        raise ValueError("최근 거래량 없음")
    return out


def chart_rows(bars):
    """Only completed historical data; includes computed indicators for charting."""
    if len(bars) < MIN_BARS:
        raise ValueError("RSI/MACD에 필요한 일봉 부족")
    closes = [float(row["close"]) for row in bars]
    rsi = _rsi(closes)
    fast = _ema(closes, 12)
    slow = _ema(closes, 26)
    macd = [f - s for f, s in zip(fast, slow)]
    signal = _ema(macd, 9)
    result = []
    for i, row in enumerate(bars):
        r = dict(row)
        for length in (5, 20, 60):
            r[f"ma{length}"] = sum(closes[i-length+1:i+1]) / length if i >= length-1 else None
        r["rsi14"] = rsi[i]
        r["macd"] = macd[i]
        r["signal"] = signal[i]
        r["hist"] = macd[i]-signal[i]
        r["rise"] = r["close"] >= r["open"]
        result.append(r)
    return result


def get_naver_chart_rows(ticker, asof, count=240):
    """Fetch only the user-selected ticker, never the entire universe on page load."""
    code = str(ticker)
    if len(code) != 6 or not code.isdigit():
        raise ValueError("6자리 종목코드만 허용")
    query = urlencode({"symbol": code, "timeframe": "day", "count": int(count),
                       "requestType": "0"})
    url = "https://fchart.stock.naver.com/sise.nhn?" + query
    request = Request(url, headers={"User-Agent": "HY-convergence-observer/1.0",
                                   "Accept": "application/json"})
    with urlopen(request, timeout=15) as response:
        raw = response.read()
    return chart_rows(parse_ohlcv(raw, asof))
