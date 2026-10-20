"""Two-track descriptive leader watch classification.

This module does not place orders, modify holdings or infer unavailable flow data.
Missing evidence is never interpreted as a positive signal.
"""
from math import isfinite

EARLY_LIMIT = 20
CONTINUATION_LIMIT = 20


def _number(row, key):
    try:
        value = float(row[key])
        return value if isfinite(value) else None
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def classify_candidate(row):
    """Return A (early), B (continuation), or None using completed daily data.

    A allows pre-alignment but requires MA20 recovery, rising MA20, liquidity,
    positive benchmark-relative momentum and volume expansion.
    B uses the existing validated leader admission flag.
    Flow data is not available in the leader snapshot; it is not fabricated.
    """
    if row.get("ordinary") is not True or row.get("tradable") is not True or not row.get("eligible"):
        return None
    keys = ("close", "ma20", "ma60", "ma20_prev5", "mean_value20",
            "ret20_pct", "excess20_pct")
    vals = {k: _number(row, k) for k in keys}
    if any(v is None for v in vals.values()):
        return None
    p, m20, m60, old, liquidity, ret20, excess20 = (vals[k] for k in keys)
    if min(p, m20, m60, old, liquidity) <= 0 or p < 1000 or liquidity < 2_000_000_000:
        return None
    if row.get("leader_eligible") is True:
        return "B"
    volume_ratio = _number(row, "volume_ratio")
    if (volume_ratio is not None and volume_ratio >= 1.5
            and p > m20 and m20 > old and excess20 > 0
            and ret20 > -5 and p / m20 <= 1.12):
        return "A"
    return None


def split_leader_watch(rows):
    """Rank each group independently, deduplicate tickers, preserve input."""
    groups = {"A": [], "B": []}
    seen = set()
    for row in rows:
        code = str(row.get("ticker", "")).zfill(6)
        if code in seen or len(code) != 6 or not code.isdigit():
            continue
        seen.add(code)
        group = classify_candidate(row)
        if group:
            groups[group].append(dict(row, watch_group=group))
    for group, limit in (("A", EARLY_LIMIT), ("B", CONTINUATION_LIMIT)):
        groups[group].sort(key=lambda r: (
            -(_number(r, "leader_score") or 0),
            -(_number(r, "mean_value20") or 0),
            str(r["ticker"])))
        groups[group] = groups[group][:limit]
    return groups
