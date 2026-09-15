"""Descriptive, unvalidated 5/20/60-session SMA convergence screen.

No order placement or buy score: convergence can precede a fall as well as a rise.
The 120-session line is deliberately NOT part of this pattern.
"""
from datetime import datetime, timedelta, timezone
from statistics import mean
import math
import re

KST = timezone(timedelta(hours=9))
THRESHOLD_PCT = 3.0
MIN_DAILY_VALUE = 500_000_000
DAILY_ADD_LIMIT = 10


def closed_date_limit(now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    # Conservative cutoff also excludes the in-progress extended session.
    day = now.date()
    if (now.hour, now.minute) < (20, 15):
        day -= timedelta(days=1)
    return day.isoformat()


def ordinary_stock(row):
    name = str(row.get("stockName", ""))
    return (
        row.get("stockEndType") == "stock"
        and re.fullmatch(r"\d{6}", str(row.get("itemCode", ""))) is not None
        and not re.search(r"스팩|SPAC|우(?:선주|[BC])?$", name, re.I)
    )


def actively_trading(row):
    # Missing/unknown status is not permission to add a potentially halted stock.
    return (row.get("tradeStopType") or {}).get("name") == "TRADING"


def analyze_bars(bars, reference_date):
    rows = sorted((x for x in bars if x["date"] <= reference_date), key=lambda x: x["date"])
    if not rows:
        return {"eligible": False, "reason": "일봉 없음"}
    if len({x["date"] for x in rows}) != len(rows):
        raise ValueError("Duplicate daily dates")
    if any(not math.isfinite(float(x[k])) or float(x[k]) < 0 for x in rows for k in ("close", "volume")):
        raise ValueError("Invalid daily prices/volumes")
    if rows[-1]["date"] != reference_date:
        return {"eligible": False, "reason": "최신 거래일 자료 없음", "date": rows[-1]["date"]}
    if rows[-1]["volume"] <= 0:
        return {"eligible": False, "reason": "거래 없음·정지 확인", "date": reference_date}
    if len(rows) < 65 or any(x["close"] <= 0 for x in rows[-65:]):
        return {"eligible": False, "reason": "65거래일 자료 부족", "date": reference_date}
    closes = [float(x["close"]) for x in rows]
    def values_at(end):
        sample = closes[:end] if end is not None else closes
        return [mean(sample[-n:]) for n in (5, 20, 60)]
    def spread(values):
        return 100 * (max(values) - min(values)) / mean(values)
    averages = values_at(None)
    span = spread(averages)
    old_span = spread(values_at(-5))
    price = closes[-1]
    position = "세 선 아래" if price < min(averages) else ("세 선 위" if price > max(averages) else "세 선 사이")
    clustered = span <= THRESHOLD_PCT + 1e-10
    state = ("🟠 수렴·하방주의" if position == "세 선 아래" else "🔵 수렴 관찰") if clustered else "⚪ 수렴 해제"
    base_volume = mean(float(x["volume"]) for x in rows[-21:-1])
    value20 = mean(float(x["close"]) * float(x["volume"]) for x in rows[-20:])
    return {
        "eligible": True, "date": reference_date, "close": price,
        "ma5": averages[0], "ma20": averages[1], "ma60": averages[2],
        "span_pct": span, "span_5_sessions_ago_pct": old_span,
        "narrowing": span < old_span, "cluster_now": clustered,
        "position": position, "state": state,
        "volume_ratio": float(rows[-1]["volume"]) / base_volume if base_volume > 0 else None,
        "mean_value20": value20,
        "candidate": clustered and price >= 1000 and value20 >= MIN_DAILY_VALUE,
    }


def candidate_sort_key(row):
    # Pattern similarity, not expected-return ranking.
    return (not row.get("narrowing", False), row["span_pct"], -row["mean_value20"], row["ticker"])


def merge_candidates(existing, candidates, already_added=(), limit=DAILY_ADD_LIMIT):
    """Append without editing old rows. Remember auto-adds to respect later deletions."""
    result = list(existing)
    seen = {str(x.get("ticker", "")).zfill(6) for x in existing} | set(already_added)
    added = []
    for row in sorted(candidates, key=candidate_sort_key):
        code = row["ticker"]
        if code in seen or len(added) >= max(0, limit):
            continue
        result.append({"ticker": code, "name": row["name"], "market": row["market"],
                       "source": "ma_convergence_daily", "added_asof": row["date"]})
        seen.add(code)
        added.append(code)
    return result, added


def convergence_columns(snapshot, ticker):
    """Cheap presentation mapping: no market fetch on a 10-second UI refresh."""
    row = snapshot.get("items", {}).get(str(ticker).zfill(6), {})
    if not row.get("eligible"):
        return {"세 선 수렴": "⚪ " + row.get("reason", "일일 자료 대기"), "수렴 간격": "-",
                "수렴 기준일": row.get("date", snapshot.get("asof", "-"))}
    return {"세 선 수렴": row["state"], "수렴 간격": f"{row['span_pct']:.2f}%",
            "수렴 기준일": row["date"]}
