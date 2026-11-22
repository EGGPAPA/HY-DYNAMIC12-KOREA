"""HY research: compare exact 5/8, 6/8, 7/8, 8/8 trend-condition groups.

Historical look-ahead-free month-end selection, next-session OPEN as entry,
3m (63 sessions) and 6m (126 sessions) outcomes. No trading decisions or orders.

Important: RS percentile is ranked only among this limited CURRENT-stock sample.
It is NOT a full-KRX / point-in-time RS percentile, and the cohort contains
survivorship bias. Report as an exploratory pilot, not a validated full-market test.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import json
import math
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from early_market_charts import get_naver_chart_rows
from market_update_data import fetch_market_universe
from minervini_trend import evaluate_trend, assign_market_rs

ASOF_END="2026-11-20"
WINDOW_START="2022-01-01"
SIGNAL_THROUGH="2026-04-30"
SESSIONS={"3m":63, "6m":126}


def select_sample(rows, per_market=180):
    counts=Counter()
    selected=[]
    seen=set()
    for x in rows:
        code=str(x.get("종목코드",""))
        market=x.get("시장")
        name=str(x.get("종목명",""))
        if (market not in ("KOSPI","KOSDAQ") or counts[market]>=per_market
            or not code.isdigit() or len(code)!=6 or code in seen):
            continue
        if ("스팩" in name or "리츠" in name or "우선주" in name
                or name.endswith(("우","우B","2우B","3우B"))):
            continue
        selected.append({"ticker":code,"name":name,"market":market})
        seen.add(code)
        counts[market]+=1
    return selected,dict(counts)


def download(row):
    try:
        bars=get_naver_chart_rows(row["ticker"],ASOF_END,count=1500)
        return row,bars,""
    except Exception as exc:
        return row,[],str(exc)[:140]


def monthly_anchors(histories):
    by_month=defaultdict(Counter)
    for row,bars in histories.values():
        last={}
        for x in bars:
            d=x["date"]
            if WINDOW_START<=d<=SIGNAL_THROUGH:
                last[d[:7]]=max(last.get(d[:7],""),d)
        for month,day in last.items():
            by_month[month][day]+=1
    return {m:counter.most_common(1)[0][0] for m,counter in sorted(by_month.items())}


def price_history_good(bars, idx):
    """Past-only corporate action diagnostic; no forward prices in eligibility."""
    closes=[float(x["close"]) for x in bars[max(0,idx-64):idx+1]]
    if len(closes)<60:
        return False
    if any(not math.isfinite(x) or x<=0 for x in closes):
        return False
    return not any(abs(closes[i]/closes[i-1]-1)>.40 for i in range(1,len(closes)))


def outcomes(bars, idx):
    """No future input used for the historical group; forward OHLC only for evaluation."""
    entry_idx=idx+1
    if entry_idx+max(SESSIONS.values())>=len(bars):
        return None
    entry=float(bars[entry_idx]["open"])
    if not math.isfinite(entry) or entry<=0:return None
    out={"entry_date":bars[entry_idx]["date"],"entry_price":entry}
    for name,days in SESSIONS.items():
        forward=bars[entry_idx:entry_idx+days]
        final=float(bars[entry_idx+days]["close"])
        if len(forward)!=days:return None
        highs=[float(x["high"]) for x in forward]
        lows=[float(x["low"]) for x in forward]
        if not all(math.isfinite(x) and x>0 for x in highs+lows+[final]):
            return None
        # Note: maximum favorable excursion is an opportunity, not realized return.
        peak=max(highs)
        trough=min(lows)
        out[name]={
            "return_pct":100*(final/entry-1),
            "max_favorable_pct":100*(peak/entry-1),
            "max_adverse_pct":100*(trough/entry-1),
            "hit20":peak>=1.20*entry,
            "hit50":peak>=1.50*entry,
            "hit100":peak>=2.00*entry,
            "down10":trough<=.90*entry,
        }
    return out


def summarize(events):
    if not events:
        return {"events":0}
    values=[x["return_pct"] for x in events]
    favourable=[x["max_favorable_pct"] for x in events]
    adverse=[x["max_adverse_pct"] for x in events]
    n=len(values)
    return {
        "events":n,
        "unique_tickers":len(set(x["ticker"] for x in events)),
        "mean_return_pct":round(statistics.mean(values),2),
        "median_return_pct":round(statistics.median(values),2),
        "positive_return_pct":round(100*sum(v>0 for v in values)/n,1),
        "hit20_pct":round(100*sum(x["hit20"] for x in events)/n,1),
        "hit50_pct":round(100*sum(x["hit50"] for x in events)/n,1),
        "hit100_pct":round(100*sum(x["hit100"] for x in events)/n,1),
        "down10_pct":round(100*sum(x["down10"] for x in events)/n,1),
        "mean_max_favorable_pct":round(statistics.mean(favourable),2),
        "mean_max_adverse_pct":round(statistics.mean(adverse),2),
        "worst_return_pct":round(min(values),2),
        "best_return_pct":round(max(values),2),
    }


def run(per_market=180,workers=6):
    market=fetch_market_universe()
    if len(market)<1800:
        raise RuntimeError("Incomplete present-day KOSPI/KOSDAQ universe")
    selected,by_market=select_sample(market,per_market)
    if any(by_market.get(m,0)<per_market for m in ("KOSPI","KOSDAQ")):
        raise RuntimeError("Sample from both markets incomplete")
    raw={}
    errors={}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(download,row) for row in selected]
        for f in as_completed(futures):
            row,bars,error=f.result()
            if error: errors[row["ticker"]]=error
            else: raw[row["ticker"]]=(row,bars)
    if len(raw)<int(len(selected)*.70):
        raise RuntimeError("Historical OHLCV coverage too low")
    anchors=monthly_anchors(raw)
    index={ticker:{bar["date"]:i for i,bar in enumerate(bars)}
           for ticker,(row,bars) in raw.items()}
    records={"3m":{str(n):[] for n in range(5,9)},
             "6m":{str(n):[] for n in range(5,9)}}
    months=[]
    held_counts=Counter()
    for month,asof in anchors.items():
        # First stage: rank only stocks using bars observable by asof.
        candidates={}
        for ticker,(row,bars) in raw.items():
            ix=index[ticker].get(asof)
            if ix is None or ix<253:continue
            if not price_history_good(bars,ix):
                held_counts["recent_corporate_action_or_bad_close"]+=1
                continue
            report=evaluate_trend(bars[:ix+1],asof=asof)
            if report["valid"]:
                candidates[ticker]=(row,bars,ix,report)
        # A ranked sample is not the same as all-market IBD RS Rating.
        rs=assign_market_rs(
            {code:args[3] for code,args in candidates.items()},
            universe_size=len(selected),minimum_count=max(50,int(.55*len(selected))),
            minimum_coverage=.70,
        )
        if not rs["ready"]:
            held_counts["skipped_months_no_sample_rs"]+=1
            months.append({"month":month,"asof":asof,"eligible":len(candidates),
                           "accepted":False,"reason":rs.get("reason","")})
            continue
        group_counts=Counter()
        for ticker,(row,bars,ix,report) in candidates.items():
            # Repeat the exact independent trend condition evaluator with
            # the contemporaneous *sample* RS percentile.
            score=evaluate_trend(bars[:ix+1],asof=asof,
                                 rs_percentile=rs["ranks"][ticker])
            if not score["valid"] or score["pending"]!=0:
                held_counts["condition_pending"]+=1
                continue
            num=score["passed"]
            if num<5:continue
            if num>8:raise AssertionError("More than 8 checks")
            out=outcomes(bars,ix)
            if out is None:
                held_counts["unmatured_forward_or_missing_history"]+=1
                continue
            group_counts[str(num)]+=1
            for horizon in SESSIONS:
                records[horizon][str(num)].append({
                    "ticker":ticker,"name":row["name"],"market":row["market"],
                    "signal_date":asof,"rs_percentile_sample":rs["ranks"][ticker],
                    **out[horizon],
                })
        months.append({"month":month,"asof":asof,"eligible":len(candidates),
                       "accepted":True,"group_counts":dict(group_counts)})
    grouped={h:{g:summarize(rows) for g,rows in levels.items()}
             for h,levels in records.items()}
    # The same stock across months is correlated: event count != independent N.
    return {
        "version":"hy_minervini_5to8_forward_study_v1",
        "status":"EXPLORATORY_SAMPLE_NOT_FULL_KRX",
        "period":{"signals_from":WINDOW_START,"signals_through":SIGNAL_THROUGH,
                  "price_data_through":ASOF_END},
        "method":"Monthly common finalized trading date; 8 original trend conditions with a SAMPLE-ONLY RS rank",
        "data_source":"Naver price data fetched using present-day market-cap ordered KOSPI/KOSDAQ listing",
        "market_universe_today":len(market),
        "selected_sample_size":len(selected),
        "selected_by_market":by_market,
        "history_valid_stocks":len(raw),
        "history_errors":len(errors),
        "history_error_examples":dict(list(errors.items())[:10]),
        "monthly_dates_attempted":len(anchors),
        "monthly_dates_ranked":sum(x["accepted"] for x in months),
        "held_reasons":dict(held_counts),
        "groups":grouped,
        "monthly_coverage":months,
        "top_6month_8of8":sorted(records["6m"]["8"],key=lambda x:x["max_favorable_pct"],reverse=True)[:12],
        "sample_events":{h:{g:events[:15] for g,events in groups.items()} for h,groups in records.items()},
        "caveats":[
            "RS 70+ is a percentile of this present-day sample; NOT full-KRX/I BD official RS",
            "Current listed market-cap ordered cohort is affected by survivorship and lookback selection bias",
            "Monthly repeated records for the same stock are not independent",
            "3/6-month final return uses entry next-session OPEN and close at +63/+126 sessions",
            "Maximum favorable gain is a possible peak, not realized trade profit",
            "Maximum adverse is relative to the entry open, NOT funded-portfolio MDD",
            "This is a stock-selection study without the HY 5-of-7 intraday gate",
            "Historical OHLC may be vendor-revised; split/rights data quality uncertain",
            "No trading costs are applied to point-in-time forward returns",
            "No automatically placed orders or edits to live HY"
        ]
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--per-market",type=int,default=180)
    p.add_argument("--workers",type=int,default=6)
    p.add_argument("--output",default="hy_minervini_5to8_forward_study.json")
    a=p.parse_args()
    if not 30<=a.per_market<=400 or not 1<=a.workers<=8:
        p.error("Sample/workers outside bounded limits")
    result=run(a.per_market,a.workers)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    print("FORWARD_SUMMARY "+json.dumps({
        "status":result["status"],"period":result["period"],
        "selected_sample_size":result["selected_sample_size"],
        "history_valid_stocks":result["history_valid_stocks"],
        "monthly_dates_ranked":result["monthly_dates_ranked"],
        "groups":result["groups"],"held_reasons":result["held_reasons"]
        },ensure_ascii=False))
    if result["monthly_dates_ranked"]<10:
        raise SystemExit("Insufficient dates for study")


if __name__=="__main__":
    main()
