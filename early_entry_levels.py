"""Read-only, closed-daily-bar reference prices for HY early-market watchlist.

This is NOT a buy signal or the intraday HY 5-of-7 entry gate.
No network access or positions/orders. Persist reference prices with their as-of
date so old levels can never be silently substituted for fresh live quotes.
"""
from __future__ import annotations

from datetime import date
from math import ceil, floor, isfinite


VERSION = "early_price_levels_v1"


def _krx_tick(price):
    """KRX ordinary-stock order tick ladder in KRW (informational rounding only)."""
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1000


def round_reference(price, *, up=False):
    """Conservative tick alignment for informational levels, not broker quotes."""
    price=float(price)
    if not isfinite(price) or price<=0:
        raise ValueError("가격 오류")
    tick=_krx_tick(price)
    if up:
        candidate=ceil(price/tick-1e-10)*tick
        # A rounded value may cross into a higher tick band.
        if candidate % _krx_tick(candidate):
            candidate=ceil(candidate/_krx_tick(candidate))*_krx_tick(candidate)
    else:
        candidate=floor(price/tick+1e-10)*tick
    return int(candidate)


def compute_price_levels(bars, asof):
    """Use only bars closed through asof; requires 65+ sorted dated sessions.

    First watch range: SMA20 .. SMA20*1.03
    Breakout reference: maximum CLOSING price of the 20 prior sessions, as HY
    uses (not the 20-day intraday high).
    Stop reference: same structural HY formula as rise_live_analysis.py,
    evaluated at the last confirmed closing price, rounded DOWN to a price tick.
    All are research references: intraday KIS quote and persisted gate NOT tested.
    """
    date.fromisoformat(str(asof))
    if not isinstance(bars,(tuple,list)) or len(bars)<65:
        raise ValueError("손절·돌파 참고선 계산에 필요한 일봉 부족")
    dates=[str(row["date"]) for row in bars]
    if dates!=sorted(set(dates)) or dates[-1]!=asof:
        raise ValueError("확정 종가 기준일 불일치/중복")
    # Only at/before asof accepted; never slice a later day's data into a signal.
    prices=[float(row["close"]) for row in bars]
    volumes=[float(row["volume"]) for row in bars]
    if not all(isfinite(c) and c>0 for c in prices):
        raise ValueError("종가 데이터 오류")
    if not all(isfinite(v) and v>=0 for v in volumes) or volumes[-1]<=0:
        raise ValueError("거래량 확인 필요")
    if any(abs(prices[i]/prices[i-1]-1)>.40
           for i in range(max(1,len(prices)-80),len(prices))):
        raise ValueError("권리변동 의심 · 수정주가 확인 필요")

    price=prices[-1]
    ma20=sum(prices[-20:])/20
    previous20_close_high=max(prices[-21:-1])
    trailing10_close_low=min(prices[-10:])
    raw_stop=min(price*.97,max(ma20*.96,trailing10_close_low*.98))
    lower=round_reference(ma20)
    upper=round_reference(ma20*1.03,up=True)
    breakout=round_reference(previous20_close_high,up=True)
    stop=round_reference(raw_stop)
    if not (0<stop<price):
        raise ValueError("손절 참고가 계산 오류")
    return {
        "version":VERSION,
        "asof":asof,
        "reference_close":price,
        "ma20":round(ma20,4),
        "watch_low":lower,
        "watch_high":upper,
        "breakout_ref":breakout,
        "stop_ref":stop,
        "risk_at_close_pct":round((price-stop)/price*100,3),
        "definition":"SMA20~SMA20+3%; prior 20 closes max; existing HY structural stop",
        "basis":"confirmed_daily_close",
    }


def checked_levels(signal, asof, supplemental=None):
    """Never treat missing, stale, or revised-price levels as valid."""
    lvl=supplemental or signal.get("entry_levels")
    if not isinstance(lvl,dict) or lvl.get("version")!=VERSION or lvl.get("asof")!=asof:
        return None
    try:
        close=float(signal.get("close"))
        ref=float(lvl.get("reference_close"))
        low=float(lvl.get("watch_low"))
        high=float(lvl.get("watch_high"))
        breakout=float(lvl.get("breakout_ref"))
        stop=float(lvl.get("stop_ref"))
        risk=float(lvl.get("risk_at_close_pct"))
        if not all(isfinite(x) for x in (close,ref,low,high,breakout,stop,risk)):
            return None
        if close<=0 or ref<=0 or abs(close/ref-1)>.005:
            return None
        if not (0<low<=high and breakout>0 and 0<stop<ref and risk>=0):
            return None
    except (ValueError,TypeError,OverflowError):
        return None
    return lvl


def decision_text(signal, levels, *, stale=False):
    """A watchlist may NEVER advertise an entry as an executed/tradable buy."""
    if stale:
        return "기준일 경과 · 보류"
    if levels is None:
        return "가격 자료 확인 · 보류"
    if float(levels["risk_at_close_pct"])>7:
        return "손절 위험 초과 · 보류"
    price=float(signal["close"])
    if price>float(levels["watch_high"]):
        return "눌림 대기 · HY 재확인"
    if price>=float(levels["breakout_ref"]):
        return "돌파 참고 · HY 재확인"
    return "관찰 대기 · HY 재확인"


def krw(value):
    if value is None:
        return "—"
    return f"{int(value):,}원"
