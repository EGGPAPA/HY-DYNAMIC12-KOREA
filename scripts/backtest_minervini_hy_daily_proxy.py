"""Historical *daily-close proxy* study; NEVER claims to reconstruct live HY 5-of-7.

Data: dated third-party KRX Minervini 8/8 archive + verified Naver OHLCV.
The existing HY evaluate_current() calculates price/volume/breakout/risk/stage/
score without changing production code. The live persistence gate is missing,
and no intraday time-normalized volume results are claimed.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import json
import re
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen
import base64

import pandas as pd

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from early_market_charts import get_naver_chart_rows
from rise_live_analysis import evaluate_current, MANDATORY_KEYS, KST

API="https://api.github.com/repos/dalinaum/rs"
ROW=re.compile(r"^\|\[([0-9]{6})\]\([^)]*\)\|([^|]*)\|([0-9,]+)\|", re.M)
FEE_SIDE=.0015
HOLD_SESSIONS=20
STOP_RATE=.07
TARGETS=(.10,.15,.20)


def archive_price_matches(published_close, observed_close, tolerance=0.03):
    """Refuse a signal if later vendor adjustments changed its historical price scale."""
    from math import isfinite
    try:
        a,b=float(published_close),float(observed_close)
    except (TypeError,ValueError):
        return False
    return bool(isfinite(a) and isfinite(b) and a>0 and b>0
                and abs(b/a-1)<=tolerance)


def get_json(url):
    import os
    headers={"Accept":"application/vnd.github+json",
             "User-Agent":"HY-isolated-daily-proxy-backtest"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"]="Bearer "+os.environ["GITHUB_TOKEN"]
    for i in range(3):
        try:
            with urlopen(Request(url,headers=headers),timeout=30) as f:
                return json.load(f)
        except Exception:
            if i==2: raise
            time.sleep(1+i*2)


def archived_rosters(start,end):
    root=get_json(API+"/git/trees/main")["tree"]
    doc_sha=next(x["sha"] for x in root if x["path"]=="docs")
    posts_sha=next(x["sha"] for x in get_json(API+"/git/trees/"+doc_sha)["tree"]
                   if x["path"]=="_posts")
    entries=get_json(API+"/git/trees/"+posts_sha)["tree"]
    matches=[e for e in entries if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}-krx-trend-template\.markdown",e["path"])
        and start<=e["path"][:10]<=end]
    matches.sort(key=lambda x:x["path"])
    if not matches:raise RuntimeError("No dated Minervini archive posts")
    def read_post(entry):
        blob=get_json(API+"/git/blobs/"+entry["sha"])
        markdown=base64.b64decode(blob["content"]).decode("utf-8")
        found=ROW.findall(markdown)
        codes={code:float(price.replace(",","")) for code,_,price in found}
        if not codes:raise RuntimeError("Empty archive post "+entry["path"])
        return entry["path"][:10],codes
    rosters={}
    with ThreadPoolExecutor(max_workers=5) as ex:
        for job in as_completed([ex.submit(read_post,p) for p in matches]):
            day,codes=job.result()
            rosters[day]=codes
    return dict(sorted(rosters.items()))


def fetch_history(code,end):
    for attempt in range(2):
        try:
            # The asof guard excludes later bars; data are never fed to old
            # decision functions before their historical date.
            return code,get_naver_chart_rows(code,end,count=850),""
        except Exception as exc:
            if attempt==1:return code,[],str(exc)[:120]
            time.sleep(.4)
    return code,[],"unavailable"


def hy_close_proxy(code,history,day):
    """Exact HY calculated checks; NOT its real-time entry_signal (which requires quotes)."""
    date=pd.Timestamp(day)
    rows=history.loc[:date]
    if len(rows)<66 or rows.index[-1]!=date:return False,None
    ctx={"basis":"close","intraday":False,"day":day.replace("-",""),
         "previous":rows.index[-2].strftime("%Y%m%d"),"mode":"historical close reference"}
    now=datetime(date.year,date.month,date.day,16,20,tzinfo=KST)
    evaluated=evaluate_current({"ticker":code,"name":code,"market":"KOSPI"},
                               history,None,ctx,now)
    checks=evaluated.get("checks") or {}
    # Persistence is not reproducible from daily bars: auxiliary = stage OR score.
    passed=bool(evaluated.get("valid")
                and not str(evaluated.get("label","")).startswith("🔴")
                and all(checks.get(k) is True for k in MANDATORY_KEYS)
                and (checks.get("stage") is True or checks.get("score") is True))
    return passed,evaluated


def simulate_trade(bars,signal_date,horizon=HOLD_SESSIONS):
    dates=[x["date"] for x in bars]
    if signal_date not in dates:return None
    si=dates.index(signal_date)+1
    if si+horizon-1>=len(bars):return None
    buy=float(bars[si]["open"])
    if buy<=0:return None
    remaining=1.0
    receipts=0.0
    low_seen=buy
    filled=[]
    for i in range(si,si+horizon):
        b=bars[i]
        op,high,low,close=(float(b[k]) for k in ("open","high","low","close"))
        low_seen=min(low_seen,low)
        stop=buy*(1-STOP_RATE)
        if low<=stop:
            exit_price=op if op<stop else stop
            receipts+=remaining*exit_price*(1-FEE_SIDE)
            filled.append(("stop",exit_price,b["date"],remaining))
            remaining=0
            break
        # Conservative intraday ordering: if both reached, stop already won.
        for step,target in enumerate(TARGETS):
            if not any(t==f"tp{step+1}" for t,*_ in filled) and high>=buy*(1+target):
                qty=min(1/3,remaining)
                if qty>0:
                    receipts+=qty*buy*(1+target)*(1-FEE_SIDE)
                    filled.append((f"tp{step+1}",buy*(1+target),b["date"],qty))
                    remaining-=qty
        if remaining<=1e-10:
            remaining=0
            break
        if i==si+horizon-1:
            receipts+=remaining*close*(1-FEE_SIDE)
            filled.append(("time",close,b["date"],remaining))
            remaining=0
    ret=(receipts/(buy*(1+FEE_SIDE))-1)*100
    return {"signal_date":signal_date,"entry_date":bars[si]["date"],
            "exit_date":filled[-1][2],"entry":round(buy,2),
            "pnl_pct":round(ret,3),"max_adverse_pct":round((low_seen/buy-1)*100,3),
            "fills":filled}


def stats(trades):
    if not trades:return {"trades":0,"win_rate_pct":None,"mean_pct":None,
                          "median_pct":None,"worst_pct":None,"mean_adverse_pct":None}
    ps=pd.Series([t["pnl_pct"] for t in trades])
    dd=pd.Series([t["max_adverse_pct"] for t in trades])
    return {"trades":len(trades),"win_rate_pct":round(float((ps>0).mean()*100),2),
            "mean_pct":round(float(ps.mean()),3),"median_pct":round(float(ps.median()),3),
            "worst_pct":round(float(ps.min()),3),
            "mean_adverse_pct":round(float(dd.mean()),3)}


def run(start,end,workers=6,limit=None):
    posts=archived_rosters(start,end)
    tickers=sorted({t for tickers in posts.values() for t in tickers})
    if limit is not None:tickers=tickers[:limit]
    histories={}
    errors={}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures={ex.submit(fetch_history,ticker,end):ticker for ticker in tickers}
        for f in as_completed(futures):
            code,bars,err=f.result()
            if bars:histories[code]=bars
            else:errors[code]=err
    first_a={}
    first_b={}
    count_total=0
    price_mismatch_days=0
    price_mismatch_examples={}
    evaluated=0
    hy_pass_days=0
    skipped_incomplete=0
    for date,ticker_list in posts.items():
        for code in ticker_list:
            if code not in histories:continue
            count_total+=1
            bars=histories[code]
            if not any(r["date"]==date for r in bars):continue
            index=next(i for i,r in enumerate(bars) if r["date"]==date)
            archived_close=float(ticker_list[code])
            observed_close=float(bars[index]["close"])
            # Check retrospective price adjustments against the archived contemporaneous quote.
            # Splits/rights can make a present-day historical series incomparable to an old signal.
            if not archive_price_matches(archived_close,observed_close):
                price_mismatch_days+=1
                price_mismatch_examples.setdefault(code,{"date":date,"published":archived_close,
                                                        "later_history":observed_close})
                continue
            # no entry if 20-day fair-horizon future is unavailable
            if index+HOLD_SESSIONS>=len(bars):
                skipped_incomplete+=1
                continue
            if code not in first_a:first_a[code]=date
            # The original HY evaluator reads historical data only up to date.
            history=pd.DataFrame({"Close":[x["close"] for x in bars],
                                  "Volume":[x["volume"] for x in bars]},
                                  index=pd.to_datetime([x["date"] for x in bars]))
            passed,analysis=hy_close_proxy(code,history,date)
            evaluated+=int(analysis is not None and analysis.get("valid",False))
            if passed:
                hy_pass_days+=1
                if code not in first_b:first_b[code]=date
    trades_a=[]
    trades_b=[]
    for tag,signals,output in (("A",first_a,trades_a),("B",first_b,trades_b)):
        for code,signal in sorted(signals.items()):
            trade=simulate_trade(histories[code],signal)
            if trade is not None:output.append({"ticker":code,"strategy":tag,**trade})
    report={
        "study":"HISTORICAL DAILY-CLOSE PROXY / NOT LIVE HY",
        "period_start":start,"period_end":end,"historical_minervini_source":
            "dalinaum/rs dated KRX template posts; RS method differs from HY",
        "archive_posts":len(posts),"archive_distinct_tickers":len({c for lst in posts.values() for c in lst}),
        "tickers_requested":len(tickers),"histories_fetched":len(histories),
        "history_error_count":len(errors),"errors_sample":dict(list(errors.items())[:15]),
        "price_mismatch_stock_days":price_mismatch_days,
        "price_mismatch_tickers":len(price_mismatch_examples),
        "price_mismatch_examples":dict(list(price_mismatch_examples.items())[:12]),
        "observed_qualifier_stock_days":count_total,
        "history_valid_hy_evaluations":evaluated,
        "hy_proxy_passing_stock_days":hy_pass_days,
        "excluded_signals_without_20day_forward_data":skipped_incomplete,
        "A_minervini_only":stats(trades_a),
        "B_minervini_plus_hy_close_proxy":stats(trades_b),
        "example_B_trades":sorted(trades_b,key=lambda x:x["signal_date"])[:12],
        "assumptions":{"entry":"next session open","hold_days":HOLD_SESSIONS,
                       "stop_pct":STOP_RATE*100,"profit_targets_pct":[10,15,20],
                       "profit_target_each_fraction":"one-third",
                       "roundtrip_cost_pct":FEE_SIDE*2*100,
                       "signal_once_per_ticker_per_period":True,
                       "live_signal_persistence":"NOT AVAILABLE",
                       "time_normalized_intraday_volume":"NOT BACKTESTED",
                       "portfolio_MDD":"NOT BACKTESTED; overlapping event trades",
                       "provider_corporate_actions":"Not fully adjusted; survivorship bias possible"},
        "warning":"Both are selected from historical third-party 8/8 universe. "
                  "This does not estimate standalone HY performance across all KRX stocks; "
                  "daily-close proxy is not exact 5-of-7 intraday entry."
    }
    return report,trades_a,trades_b


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--start",default="2026-08-03")
    p.add_argument("--end",default="2026-11-20")
    p.add_argument("--workers",type=int,default=6)
    p.add_argument("--limit",type=int,default=None)
    p.add_argument("--output",default="minervini_hy_daily_proxy_result.json")
    args=p.parse_args()
    report,a,b=run(args.start,args.end,args.workers,args.limit)
    with open(args.output,"w",encoding="utf-8") as f:
        json.dump({"summary":report,"A_trades":a,"B_trades":b},f,ensure_ascii=False,indent=2)
    print("RESULT_JSON "+json.dumps({k:v for k,v in report.items() if k not in ("example_B_trades","errors_sample")},ensure_ascii=False))


if __name__=="__main__":
    main()
