"""Two-stage read-only screen: Minervini 8/8 then the unchanged HY 5-of-7 gate.

Never counts an out-of-session or stale reference evaluation as a live signal.
"""
from rise_live_analysis import entry_signal, MANDATORY_KEYS, AUXILIARY_KEYS


def minervini_roster(snapshot, context):
    """Return ALL market-wide 8/8 tickers, not just the 20-stock HY watchlist."""
    if not isinstance(snapshot, dict) or not snapshot.get("complete") or not snapshot.get("rs_ready"):
        return [], "미너비니 전 시장 8/8 확정 스냅샷 없음"
    if not isinstance(context, dict) or context.get("error"):
        return [], "KIS 거래일 검증 실패"
    if not (context.get("intraday") and context.get("basis") == "quote"):
        return [], "장중 실시간 HY 5-of-7 평가 불가 · 정규장에 다시 확인"
    prev = str(context.get("previous", ""))
    if str(snapshot.get("asof", "")).replace("-", "") != prev:
        return [], "미너비니 스냅샷이 직전 확정 거래일과 다름 · 재검색 필요"
    rows = []
    for ticker, item in (snapshot.get("items") or {}).items():
        if (isinstance(item, dict) and len(ticker) == 6 and ticker.isdigit()
                and item.get("asof") == snapshot["asof"]
                and item.get("all_pass") is True and item.get("passed") == 8
                and item.get("pending") == 0):
            rows.append({"ticker": ticker, "name": str(item.get("name") or ticker),
                         "market": str(item.get("market") or "")})
    rows.sort(key=lambda x: x["ticker"])
    return rows, ""


def combined_report(roster, evaluated, context):
    """Use exactly HY's entry_signal. Unchecked stocks are not classified as failures."""
    if not (context.get("intraday") and context.get("basis") == "quote" and not context.get("error")):
        return {"valid": False, "reason": "장중 실시간 시세 검증 불가",
                "candidates": [], "evaluated": [], "pending": [], "failed": []}
    known = {r["ticker"] for r in roster}
    observed = {r.get("ticker"): r for r in evaluated if r.get("ticker") in known}
    candidates, unsuccessful, pending = [], [], []
    for row in roster:
        x = observed.get(row["ticker"])
        if not x or not x.get("valid"):
            pending.append({**row, "reason": (x or {}).get("reason") or "실시간 자료 없음"})
            continue
        checks = x.get("checks") or {}
        count = sum(checks.get(k) is True for k in (*MANDATORY_KEYS, *AUXILIARY_KEYS))
        item = {**row, "checks": count, "mandatory": x.get("mandatory_count", 0),
                "auxiliary": x.get("auxiliary_count", 0), "price": x.get("price"),
                "volume_ratio": x.get("volume_ratio"), "action": x.get("action")}
        if entry_signal(x):
            candidates.append(item)
        else:
            unsuccessful.append(item)
    return {"valid": True, "reason": "", "screened_count": len(roster),
            "qualified_count": len(candidates),
            "candidates": candidates, "evaluated": evaluated,
            "pending": pending, "failed": unsuccessful}
