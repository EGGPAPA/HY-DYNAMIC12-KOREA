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

def early_candidates(snapshot, saved_rows):
    """Derive A from the same completed leader snapshot and saved ordinary stocks."""
    from rise_leaders import leader_snapshot_ready, leader_candidates
    if not leader_snapshot_ready(snapshot):
        return []
    saved = {str(r.get("ticker", "")).zfill(6) for r in saved_rows}
    leaders = {r["ticker"] for r in leader_candidates(snapshot, saved_rows)}
    candidates = []
    for code, row in snapshot.get("items", {}).items():
        if (code not in saved or code in leaders or row.get("ticker") != code
                or row.get("date") != snapshot["asof"] or row.get("strength_version") != 1):
            continue
        benchmark = snapshot["benchmarks"].get(row.get("market"), {})
        ret20 = _number(row, "ret20_pct")
        benchmark20 = _number(benchmark, "ret20_pct")
        if ret20 is None or benchmark20 is None:
            continue
        candidate = dict(row, excess20_pct=ret20 - benchmark20)
        if classify_candidate(candidate) == "A":
            candidate["watch_group"] = "A"
            candidate["early_score"] = round(min(100, max(0,
                40 + min(25, candidate["excess20_pct"] * 2)
                + min(20, max(0, (_number(row, "volume_ratio") or 1) - 1) * 20)
                + min(15, max(0, (_number(row, "ma20") or 0) /
                    (_number(row, "ma20_prev5") or 1) - 1) * 500))), 2)
            candidates.append(candidate)
    candidates.sort(key=lambda r: (-r["early_score"], -r["mean_value20"], r["ticker"]))
    return candidates[:EARLY_LIMIT]
