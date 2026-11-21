"""Read-only RSI+MACD post-filter pilot on the five VERIFIED historical B trades.

B = historical Minervini 8/8 from public daily posts AND HY daily-close proxy,
not a reconstruction of the HY live 5-of-7 persistence gate.
This script does not search/tune indicator thresholds against outcomes.
Trade entry/exit logic comes unchanged from the preceding B backtest.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from early_market_charts import get_naver_chart_rows
from early_market_detector import _rsi, _ema
from scripts.backtest_minervini_hy_daily_proxy import (
    simulate_trade, archive_price_matches, stats
)

# The entire B cohort from the previous corrected 2026-08-03~2026-11-20
# daily-proxy study. NO winners/losers omitted.
# Original B reports: https://github.com/EGGPAPA/HY-DYNAMIC12-KOREA/actions/runs/44945337203
B_BASELINE = (
    ("025890", "한국주강", "2026-09-22", 2690, -7.279),
    ("037760", "쎄니트", "2026-10-01", 2450, 3.703),
    ("094800", "맵스리얼티", "2026-09-08", 7240, 3.391),
    ("143240", "사람인", "2026-10-13", 17770, -7.279),
    ("453340", "현대그린푸드", "2026-09-15", 19350, 4.500),
)


def indicator_at(bars, asof):
    """Only use OHLCV closed at or before asof; same RSI/EMA as HY early detector."""
    past = [row for row in bars if row["date"] <= asof]
    if len(past) < 60 or past[-1]["date"] != asof:
        raise ValueError("At least 60 confirmed historical sessions ending at signal date required")
    prices = [float(row["close"]) for row in past]
    if not all(math.isfinite(p) and p > 0 for p in prices):
        raise ValueError("Invalid close history")
    rsi = _rsi(prices, 14)[-1]
    fast = _ema(prices, 12)
    slow = _ema(prices, 26)
    macd = [f - s for f, s in zip(fast, slow)]
    signal = _ema(macd, 9)
    hist = [m - s for m, s in zip(macd, signal)]
    if rsi is None or not math.isfinite(rsi) or not all(math.isfinite(x) for x in
        (macd[-1],signal[-1],hist[-1],hist[-2])):
        raise ValueError("Missing RSI or MACD data")
    rsi_ok = 50.0 <= rsi <= 75.0
    macd_ok = macd[-1] > signal[-1] and hist[-1] > hist[-2] and hist[-1] > 0
    return dict(rsi14=round(rsi, 3),
                macd_line=round(macd[-1], 5),
                macd_signal=round(signal[-1], 5),
                macd_hist=round(hist[-1], 5),
                macd_hist_prev=round(hist[-2], 5),
                rsi_pass=rsi_ok,macd_pass=macd_ok,
                both_pass=rsi_ok and macd_ok)


def run():
    verified = []
    excluded = []
    for code, name, asof, published, prior_pnl in B_BASELINE:
        try:
            bars = get_naver_chart_rows(code, "2026-11-20", count=850)
            signal_rows = [b for b in bars if b["date"] <= asof]
            if not signal_rows or not archive_price_matches(published,signal_rows[-1]["close"]):
                raise ValueError("Historical close differs from the archived signal-date close")
            v = indicator_at(bars, asof)
            t = simulate_trade(bars, asof)
            if t is None:
                raise ValueError("No complete 20-session future path")
            if abs(t["pnl_pct"]-prior_pnl)>0.12:
                raise ValueError(
                    f"Provider's later historical bars changed previous result: {t['pnl_pct']}, expected {prior_pnl}")
            verified.append({
                "ticker":code,"name":name,"signal_date":asof,
                "pnl_pct":t["pnl_pct"],**v,
            })
        except Exception as exc:
            excluded.append({"ticker":code,"name":name,"reason":str(exc)[:200]})
    groups = {}
    for label, predicate in (
        ("B_HY_daily_close_proxy",lambda x:True),
        ("B_plus_RSI_50_to_75",lambda x:x["rsi_pass"]),
        ("B_plus_MACD_positive_and_rising",lambda x:x["macd_pass"]),
        ("B_plus_RSI_and_MACD",lambda x:x["both_pass"]),
    ):
        picked = [x for x in verified if predicate(x)]
        groups[label] = {
            **stats(picked),
            "tickers":[x["ticker"] for x in picked],
        }
    return {
        "period":"2026-08-03~2026-11-20",
        "status":"COMPARABLE" if len(excluded)==0 and len(verified)==len(B_BASELINE) else "INCOMPLETE",
        "source":"Previously observed 5 HY daily-close proxy B trades, same Naver bars",
        "indicators":{"RSI":"Wilder RSI(14), between 50 and 75 inclusive",
                      "MACD":"EMA(12)-EMA(26), above EMA(9) signal; histogram positive and rising"},
        "trade_rules":"Next-day open, 7% stop, +10/15/20 thirds, 20 sessions, 0.30% roundtrip",
        "verified_count":len(verified),"excluded":excluded,
        "groups":groups,"trades":verified,
        "limitations":[
            "Five selected trades only; extremely low statistical power",
            "Historical Minervini list comes from third party, with RS method different from HY",
            "HY conditions are daily-close proxies, not true intraday 5-of-7 persistence",
            "RSI and MACD tests are daily-close only, not intraday indicator states",
            "No minute bars; time-normalized intraday volume remains untested",
            "No portfolio CAGR, equity curve, or maximum drawdown calculated",
            "Do not interpret filtered win rate as evidence of out-of-sample profitability",
        ],
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",default="minervini_hy_rsi_macd_pilot.json")
    args=p.parse_args()
    result=run()
    with open(args.output,"w",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print("PILOT_RESULT "+json.dumps(result,ensure_ascii=False))
    if result["status"]!="COMPARABLE":
        raise SystemExit("Pilot incomplete due to provider revisions or missing bars")


if __name__=="__main__":
    main()
