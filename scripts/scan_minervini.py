"""On-demand market-wide Minervini evaluation (no HY 5-of-7 mutations).

Usage:
 python scripts/scan_minervini.py --asof 2026-11-20 --output /tmp/minervini_market_scan.json

Read-only: output is a local JSON file, not automatically uploaded to GitHub.
Full-market RS ranks require at least 1000 valid stocks and 80% broad coverage.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from early_market_charts import get_naver_chart_rows
from market_update_data import fetch_market_universe
from minervini_trend import assign_market_rs, evaluate_trend


def apply_rank(report, rank):
    """Add a valid whole-market percentile without modifying original report."""
    out = {**report, "checks": {**report["checks"]}, "metrics": {**report["metrics"]}}
    out["checks"]["rs_rank_70"] = rank >= 70
    out["metrics"]["rs_percentile"] = rank
    out["passed"] = sum(v is True for v in out["checks"].values())
    out["failed"] = sum(v is False for v in out["checks"].values())
    out["pending"] = sum(v is None for v in out["checks"].values())
    out["all_pass"] = out["passed"] == 8
    out["reason"] = ""
    return out


def run(asof, *, workers=3, limit=None):
    universe = [x for x in fetch_market_universe()
                if str(x.get("종목코드", "")).isdigit()
                and len(str(x["종목코드"])) == 6]
    if len(universe) < 1800:
        raise ValueError("전 시장 종목목록 부족: 평가를 실행하지 않습니다")
    n_universe = len(universe)
    if limit is not None:
        universe = universe[:limit]

    def fetch_one(row):
        ticker = row["종목코드"]
        try:
            bars = get_naver_chart_rows(ticker, asof, count=350)
            report = evaluate_trend(bars, asof=asof)
        except Exception as exc:
            report = {"valid": False, "asof": asof, "reason": str(exc)}
        return ticker, row, report

    reports, stocks = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_one, r) for r in universe]
        for future in as_completed(futures):
            ticker, row, report = future.result()
            reports[ticker] = report
            stocks[ticker] = row
    rs = assign_market_rs(reports, universe_size=n_universe,
                          minimum_coverage=0.80, minimum_count=1000)
    if limit is not None:
        # A limited sample is never an official full-market ranking.
        rs = {"ready": False, "reason": "제한 표본: 전체시장 RS 산정 불가"}
    items = {}
    for ticker, report in reports.items():
        if not report.get("valid"):
            continue
        if rs["ready"]:
            report = apply_rank(report, rs["ranks"][ticker])
        items[ticker] = {
            "asof": asof, "name": stocks[ticker]["종목명"],
            "market": stocks[ticker]["시장"],
            "passed": report["passed"], "failed": report["failed"],
            "pending": report["pending"], "all_pass": report["all_pass"],
            "rs_percentile": report["metrics"]["rs_percentile"],
        }
    return {
        "version": "minervini_market_v1", "asof": asof,
        "complete": bool(rs["ready"] and limit is None),
        "rs_ready": bool(rs["ready"] and limit is None),
        "source": "Naver confirmed daily OHLCV",
        "universe_count": n_universe,
        "evaluated_count": len(reports),
        "eligible_count": len(items),
        "eight_of_eight_count": sum(x["all_pass"] for x in items.values())
            if rs["ready"] else None,
        "seven_of_seven_count": sum(x["passed"] == 7 and x["pending"] == 1
                                    for x in items.values()) if not rs["ready"] else None,
        "reason": rs["reason"], "items": items,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asof", required=True)
    parser.add_argument("--output", default="minervini_market_scan.json")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None,
                        help="debug only; never marks result complete")
    args = parser.parse_args()
    if not 1 <= args.workers <= 6:
        parser.error("workers must be between 1 and 6")
    snapshot = run(args.asof, workers=args.workers, limit=args.limit)
    Path(args.output).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(json.dumps({k:v for k,v in snapshot.items() if k!="items"},
                     ensure_ascii=False))
    if not snapshot["complete"]:
        print("WARNING: incomplete market data; no 8/8 total available",
              file=sys.stderr)


if __name__ == "__main__":
    main()
