"""Daily closed-bar scanner. Only --publish writes to GitHub; never places orders."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ma_convergence import (KST, DAILY_ADD_LIMIT, THRESHOLD_PCT, MIN_DAILY_VALUE,
    closed_date_limit, ordinary_stock, actively_trading, analyze_bars,
    candidate_sort_key, merge_candidates)

REPO = os.environ.get("GITHUB_REPOSITORY", "EGGPAPA/HY-DYNAMIC12-KOREA")
WATCH_PATH = "rise_timing_watchlist.json"
STATE_PATH = "data/ma_convergence_daily.json"
STATE_BRANCH = "monitor-state"


def request(url, *, payload=None, token=None):
    headers = {"User-Agent": "HY-convergence-observer/1.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="GET" if data is None else "PUT")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == 2:
                raise
        time.sleep(1 + attempt * 2)


def github_read(path, branch, default=None):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}?ref={branch}"
    try:
        doc = json.loads(request(url, token=os.environ.get("GITHUB_TOKEN")))
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and default is not None:
            return default, None
        raise
    return json.loads(base64.b64decode(doc["content"])), doc["sha"]


def github_write(path, branch, value, sha, message):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required for --publish")
    payload = {"message": message, "branch": branch,
               "content": base64.b64encode(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode()).decode()}
    if sha:
        payload["sha"] = sha
    request(f"https://api.github.com/repos/{REPO}/contents/{path}", payload=payload, token=token)


def fetch_universe():
    stocks = {}
    metadata = {}
    for market in ("KOSPI", "KOSDAQ"):
        raw_seen = set()
        total = None
        for page in range(1, 81):
            url = f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100"
            doc = json.loads(request(url))
            if not isinstance(doc.get("stocks"), list) or not isinstance(doc.get("totalCount"), int):
                raise RuntimeError(f"{market}: universe schema changed")
            if total is None:
                total = doc["totalCount"]
            rows = doc["stocks"]
            for row in rows:
                code = str(row.get("itemCode", ""))
                raw_seen.add(code)
                if ordinary_stock(row):
                    stocks[code] = {"ticker": code, "name": row["stockName"], "market": market,
                                    "tradable": actively_trading(row)}
            if page * 100 >= total:
                break
            if not rows:
                raise RuntimeError(f"{market}: unexpected empty universe page")
            time.sleep(0.15)
        if total is None or total < 500 or len(raw_seen) < total * 0.98:
            raise RuntimeError(f"{market}: incomplete universe; keeping previous snapshot")
        metadata[market] = {"listed_including_funds": total, "unique_received": len(raw_seen)}
    if len(stocks) < 1800:
        raise RuntimeError("Unexpectedly small stock universe")
    return stocks, metadata


def fetch_bars(code):
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=100&requestType=0"
    raw = request(url)
    # ElementTree cannot parse an EUC-KR byte stream directly.
    root = ET.fromstring(raw.decode("euc-kr"))
    bars = []
    for node in root.iter("item"):
        date, op, high, low, close, volume = node.attrib["data"].split("|")
        bars.append({"date": f"{date[:4]}-{date[4:6]}-{date[6:8]}", "close": float(close), "volume": float(volume)})
    if not bars:
        raise ValueError("Empty price history")
    return bars


def reference_date(limit):
    dates = [max(x["date"] for x in fetch_bars(code) if x["date"] <= limit) for code in ("005930", "000660")]
    if dates[0] != dates[1] or (datetime.fromisoformat(limit) - datetime.fromisoformat(dates[0])).days > 10:
        raise RuntimeError("Reference trading date is inconsistent or stale")
    return dates[0]


def scan_one(row, asof):
    if row.get("tradable") is False:
        return {**row, "eligible": False, "reason": "거래정지·거래상태 확인", "date": asof}
    time.sleep(0.3)  # At most four bounded workers; avoid aggressive full-market requests.
    return {**row, **analyze_bars(fetch_bars(row["ticker"]), asof)}


def publish(snapshot, previous):
    # Commit a durable pending plan BEFORE changing the watchlist. A retry after an
    # interrupted run can finish that exact plan without adding another day's quota.
    pending = previous.get("pending")
    if pending:
        plan = pending
    else:
        rows, _ = github_read(WATCH_PATH, "main")
        excluded = set(previous.get("auto_added_ever", [])) | set(previous.get("known_watchlist_codes", []))
        _, added = merge_candidates(rows, snapshot["candidates"], excluded)
        plan = {"asof": snapshot["asof"], "tickers": added}
    snapshot["pending"] = plan
    snapshot["auto_added_ever"] = previous.get("auto_added_ever", [])
    for row in snapshot["candidates"]:
        if row["ticker"] in plan["tickers"]:
            snapshot["items"][row["ticker"]] = row
    _, state_sha = github_read(STATE_PATH, STATE_BRANCH, {})
    github_write(STATE_PATH, STATE_BRANCH, snapshot, state_sha, f"Prepare daily convergence {snapshot['asof']}")
    plan_rows = [x for x in snapshot["candidates"] if x["ticker"] in plan["tickers"]]
    for attempt in range(4):
        rows, sha = github_read(WATCH_PATH, "main")
        merged, added = merge_candidates(rows, plan_rows, (), limit=DAILY_ADD_LIMIT)
        if not added:
            break
        try:
            github_write(WATCH_PATH, "main", merged, sha, f"Add {len(added)} daily MA convergence observations ({plan['asof']})")
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in (409, 422) or attempt == 3:
                raise
            time.sleep(1)
    else:
        raise RuntimeError("Watchlist merge conflict")
    verified, _ = github_read(WATCH_PATH, "main")
    codes = {str(x.get("ticker", "")).zfill(6) for x in verified}
    if not set(plan["tickers"]).issubset(codes):
        raise RuntimeError("Watchlist read-back failed; pending plan retained")
    snapshot["added_this_run"] = plan["tickers"]
    snapshot["auto_added_ever"] = sorted(set(snapshot["auto_added_ever"]) | set(plan["tickers"]))
    snapshot["known_watchlist_codes"] = sorted(set(previous.get("known_watchlist_codes", [])) | codes)
    snapshot["pending"] = None
    snapshot["complete"] = True
    _, sha = github_read(STATE_PATH, STATE_BRANCH, {})
    github_write(STATE_PATH, STATE_BRANCH, snapshot, sha, f"Complete daily convergence {snapshot['asof']}")
    print(f"Published {snapshot['asof']}: added {len(plan['tickers'])}, watchlist {len(verified)}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    asof = reference_date(closed_date_limit())
    previous, _ = github_read(STATE_PATH, STATE_BRANCH, {})
    if args.publish and previous.get("pending"):
        publish(previous, previous)
        return
    if args.publish and previous.get("complete") and previous.get("asof", "") >= asof:
        print(f"No new closed trading day ({asof}); preserved watchlist", flush=True)
        return
    universe, coverage = fetch_universe()
    watchlist, _ = github_read(WATCH_PATH, "main")
    jobs = dict(universe)
    for row in watchlist:
        code = str(row.get("ticker", "")).zfill(6)
        if code not in jobs:
            jobs[code] = {**row, "ticker": code}
    items, errors = {}, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {pool.submit(scan_one, row, asof): code for code, row in jobs.items()}
        for n, task in enumerate(as_completed(tasks), 1):
            code = tasks[task]
            try:
                items[code] = task.result()
            except Exception as exc:
                errors.append({"ticker": code, "error": type(exc).__name__})
                items[code] = {"ticker": code, "eligible": False, "reason": "자료 수신 실패", "date": asof}
            if n % 250 == 0:
                print(f"Scanned {n}/{len(jobs)}; request failures {len(errors)}", flush=True)
    eligible = sum(bool(x.get("eligible")) for code, x in items.items() if code in universe)
    if len(errors) > len(jobs) * 0.05 or eligible < len(universe) * 0.80:
        raise RuntimeError(f"Coverage insufficient ({eligible}/{len(universe)}, errors={len(errors)}); no publish")
    candidates = sorted([x for code, x in items.items() if code in universe and x.get("candidate")], key=candidate_sort_key)
    snapshot = {"version": 1, "asof": asof, "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
                "source": "Naver daily OHLCV", "threshold_pct": THRESHOLD_PCT,
                "definition": "100 * (max(SMA5,SMA20,SMA60)-min(SMA5,SMA20,SMA60)) / mean(SMA5,SMA20,SMA60)",
                "min_price": 1000, "min_mean_value20": MIN_DAILY_VALUE, "daily_add_limit": DAILY_ADD_LIMIT,
                "universe_count": len(universe), "calculated_count": eligible, "coverage": coverage,
                "candidate_count": len(candidates), "candidates": candidates,
                # Persist only current watchlist + shortlisted new rows, keeping the contents API under 1 MB.
                "items": {x["ticker"]: items[x["ticker"]] for x in watchlist if x["ticker"] in items},
                "errors": errors, "complete": False}
    excluded = set(previous.get("auto_added_ever", [])) | set(previous.get("known_watchlist_codes", []))
    _, new_codes = merge_candidates(watchlist, candidates, excluded)
    for code in new_codes:
        snapshot["items"][code] = items[code]
    print(json.dumps({k: snapshot[k] for k in ("asof", "universe_count", "calculated_count", "candidate_count")}), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if args.publish:
        publish(snapshot, previous)
    else:
        print("Dry run only; no remote files changed", flush=True)


if __name__ == "__main__":
    main()
