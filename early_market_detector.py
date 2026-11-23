"""Read-only early-momentum research screen for completed KRX daily bars.

Signals are descriptive, not forecasts, orders, or entry alerts. Do not confuse
this detector with the existing 5-of-7 or KIS live-entry decisions.
"""
from math import isfinite
from statistics import mean

from early_entry_levels import compute_price_levels

VERSION = "early_market_v1"
MIN_BARS = 85
MIN_VALUE20 = 2_000_000_000


def _ema(values, period):
    alpha = 2.0 / (period + 1)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1 - alpha) * result[-1])
    return result


def _rsi(closes, period=14):
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    avg_gain, avg_loss = mean(gains[:period]), mean(losses[:period])
    def value():
        if avg_gain == 0 and avg_loss == 0:
            return 50.0
        if avg_loss == 0:
            return 100.0
        return 100 - 100 / (1 + avg_gain / avg_loss)
    result = [None] * period + [value()]
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result.append(value())
    return result


def _validated(rows, asof):
    if not isinstance(rows, (list, tuple)):
        raise ValueError("일봉 형식 오류")
    items = sorted((r for r in rows if str(r.get("date", "")) <= asof),
                   key=lambda r: r["date"])
    if len(items) < MIN_BARS or items[-1]["date"] != asof:
        raise ValueError("최신 확정 일봉 또는 85거래일 자료 부족")
    dates = [str(r["date"]) for r in items]
    if len(set(dates)) != len(dates):
        raise ValueError("중복 거래일")
    closes, volumes = [], []
    for row in items:
        close, vol = float(row["close"]), float(row["volume"])
        if not isfinite(close) or not isfinite(vol) or close <= 0 or vol < 0:
            raise ValueError("가격 또는 거래량 오류")
        closes.append(close)
        volumes.append(vol)
    if volumes[-1] == 0:
        raise ValueError("거래정지 또는 거래량 확인 필요")
    if any(abs(closes[i] / closes[i - 1] - 1) > .40
           for i in range(len(closes) - 80, len(closes))):
        raise ValueError("권리변동 의심 · 수정주가 검증 필요")
    return dates, closes, volumes


def analyze_early(bars, benchmark_bars, asof):
    """Evaluate using only data available at the completed asof session."""
    held = {"valid": False, "candidate": False, "reason": "검증할 자료 없음",
            "version": VERSION, "asof": asof}
    try:
        dates, c, v = _validated(bars, asof)
        _, benchmark_closes, _ = _validated(benchmark_bars, asof)
        reference = {str(r["date"]): float(r["close"]) for r in benchmark_bars
                     if str(r.get("date", "")) <= asof}
        if any(day not in reference for day in dates[-6:]):
            raise ValueError("지수와 종목 거래일 불일치")
        bench_ret5 = 100 * (reference[dates[-1]] / reference[dates[-6]] - 1)
        ret5 = 100 * (c[-1] / c[-6] - 1)
        ma20 = mean(c[-20:])
        ma20_series = [mean(c[i - 19:i + 1]) for i in range(19, len(c))]
        recently_recovered = any(
            c[i - 1] <= ma20_series[i - 20] and c[i] > ma20_series[i - 19]
            for i in range(len(c) - 5, len(c)))
        rsi = _rsi(c)
        rsi_cross = any(rsi[i - 1] <= 50 < rsi[i]
                        for i in range(len(rsi) - 5, len(rsi)))
        fast, slow = _ema(c, 12), _ema(c, 26)
        macd = [a - b for a, b in zip(fast, slow)]
        signal = _ema(macd, 9)
        hist = [a - b for a, b in zip(macd, signal)]
        hist_rising = hist[-3] < hist[-2] < hist[-1]
        avg_volume = mean(v[-21:-1])
        if avg_volume <= 0:
            raise ValueError("과거 거래량 기준값 없음")
        volume_ratio = v[-1] / avg_volume
        mean_value20 = mean(c[i] * v[i] for i in range(len(c) - 20, len(c)))
        gap20 = 100 * (c[-1] / ma20 - 1)
        excess5 = ret5 - bench_ret5
        signals = {
            "ma20_recovery": recently_recovered,
            "rsi50_cross": rsi_cross,
            "macd_hist_rising": hist_rising,
            "volume_1_5x": volume_ratio >= 1.5,
            "index_outperformance_5d": excess5 > 0,
        }
        count = sum(signals.values())
        candidate = (c[-1] >= 1000 and mean_value20 >= MIN_VALUE20
                     and 0 <= gap20 <= 10 and ret5 <= 18
                     and volume_ratio >= 1.2 and count >= 3
                     and (rsi_cross or hist_rising))
        result = {"valid": True, "candidate": bool(candidate), "reason": "",
                  "version": VERSION, "asof": asof,
                  "signals": signals, "signal_count": count,
                  "close": round(c[-1], 2), "rsi14": round(rsi[-1], 2),
                  "macd_hist": round(hist[-1], 4),
                  "volume_ratio": round(volume_ratio, 3),
                  "excess5_pct": round(excess5, 2),
                  "gap20_pct": round(gap20, 2),
                  "mean_value20": round(mean_value20)}
        if candidate:
            # Reuse the already-fetched 100 completed sessions. This does not
            # request another price feed or change the original five signals.
            # If a reference cannot be verified, leave it absent and hold.
            try:
                completed = sorted((row for row in bars
                                    if str(row.get("date", "")) <= asof),
                                   key=lambda row: row["date"])
                result["entry_levels"] = compute_price_levels(completed, asof)
            except (ValueError, TypeError, KeyError, OverflowError):
                pass
        return result
    except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, OverflowError) as exc:
        return {**held, "reason": str(exc)[:100]}


def rank_early(rows, limit=30):
    """Sort only verified candidates. No candidate is a buy instruction."""
    chosen = [r for r in rows if r.get("early_signal", {}).get("candidate")]
    return sorted(chosen, key=lambda r: (
        -r["early_signal"]["signal_count"],
        -r["early_signal"]["excess5_pct"],
        -r["early_signal"]["volume_ratio"],
        r["ticker"]))[:limit]
