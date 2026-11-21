"""HY 5-of-7 standalone historical DAILY-CLOSE PROXY backtest.

Study only: reuses original rise_live_analysis.evaluate_current(), but does NOT
reconstruct the live 10-60s persistence condition or intraday volume snapshots.
Historical signals are evaluated as-of their close; entries occur NEXT session.
Source universe is current market-cap-ordered KOSPI/KOSDAQ stocks (survivor bias).
No HY monitor-state, watchlist, or brokerage write operations.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import sys

import pandas as pd

ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from early_market_charts import get_naver_chart_rows
from early_market_detector import _rsi, _ema
from market_update_data import fetch_market_universe
from rise_live_analysis import evaluate_current, KST, MANDATORY_KEYS, AUXILIARY_KEYS

START="2023-01-02"
END="2026-11-20"
STOP_RATE=.07
TARGETS=(.10,.15,.20)
SIDE_FEE=.0015
HOLD_BARS=20
HISTORY_COUNT=1250
STRATEGIES=("HY_daily","HY_daily_MACD","HY_daily_RSI_MACD")


def select_universe(market_rows, per_market):
    """First N current market-cap ordered stocks in *each* market, not all stocks."""
    picked=[]
    counts=Counter()
    seen=set()
    for row in market_rows:
        code=str(row.get("종목코드",""))
        market=row.get("시장")
        name=str(row.get("종목명",""))
        if (market not in ("KOSPI","KOSDAQ") or not code.isdigit()
                or len(code)!=6 or code in seen or counts[market]>=per_market):
            continue
        if ("스팩" in name or "리츠" in name or "우선주" in name
                or name.endswith(("우","우B","2우B","3우B"))):
            continue
        seen.add(code);counts[market]+=1
        picked.append({"ticker":code,"name":name,"market":market})
    return picked,dict(counts)


def fetch_one(row, end):
    try:
        bars=get_naver_chart_rows(row["ticker"],end,count=HISTORY_COUNT)
        # Corporate action price discontinuities > 40% are not reliable as trades
        return row,bars,""
    except Exception as exc:
        return row,None,str(exc)[:180]


def indicators(closes):
    """Exactly the Wilder RSI and EMA conventions from HY early_market_detector."""
    rsi=_rsi(closes,14)
    fast=_ema(closes,12);slow=_ema(closes,26)
    macd=[a-b for a,b in zip(fast,slow)]
    sig=_ema(macd,9)
    hist=[a-b for a,b in zip(macd,sig)]
    return rsi,hist


def pass_macd_hist(hist, index):
    return index>=1 and all(math.isfinite(float(hist[j])) for j in (index-1,index)) and hist[index]>0 and hist[index]>hist[index-1]


def pass_rsi(rsi, index):
    val=rsi[index]
    return val is not None and math.isfinite(float(val)) and 50.0<=val<=75.0


def daily_proxy(evaluated):
    """Only the genuine HY checks, except persistence (cannot derive from daily bars)."""
    checks=evaluated.get("checks") or {}
    return bool(evaluated.get("valid") and not str(evaluated.get("label","")).startswith("🔴")
                and all(checks.get(x) is True for x in MANDATORY_KEYS)
                and (checks.get("stage") is True or checks.get("score") is True))


def simulate_trade(bars,signal_index,horizon=HOLD_BARS):
    """Next open, stop-first, gap aware, one-third take profits; no intraday ordering claim."""
    start=signal_index+1
    if start+horizon>len(bars):return None
    buy=float(bars[start]["open"])
    if not math.isfinite(buy) or buy<=0:return None
    remaining=1.
    proceeds=0.
    sold_targets=set()
    lowest=buy
    filled=[]
    stop=buy*(1-STOP_RATE)
    for ix in range(start,start+horizon):
        b=bars[ix]
        op,hi,lo,cl=(float(b[k]) for k in ("open","high","low","close"))
        if min(op,hi,lo,cl)<=0 or not all(math.isfinite(v) for v in (op,hi,lo,cl)):
            return None
        lowest=min(lowest,lo)
        if lo<=stop:
            price=min(op,stop)
            proceeds+=remaining*price*(1-SIDE_FEE)
            filled.append(("STOP",str(b["date"]),round(price,2),remaining))
            remaining=0.
            break
        for j,target in enumerate(TARGETS):
            if j not in sold_targets and hi>=buy*(1+target):
                q=min(1/3,remaining)
                if q>1e-8:
                    exit_price=buy*(1+target)
                    proceeds+=q*exit_price*(1-SIDE_FEE)
                    sold_targets.add(j)
                    filled.append((f"TP{j+1}",str(b["date"]),round(exit_price,2),q))
                    remaining-=q
        if remaining<1e-9:
            remaining=0.
            break
        if ix==start+horizon-1:
            proceeds+=remaining*cl*(1-SIDE_FEE)
            filled.append(("TIME",str(b["date"]),round(cl,2),remaining))
            remaining=0.
    ret=(proceeds/(buy*(1+SIDE_FEE))-1)*100
    return {"entry_date":bars[start]["date"],"exit_date":filled[-1][1],
            "entry_price":round(buy,2),"pnl_pct":round(ret,4),
            "mae_pct":round((lowest/buy-1)*100,4),
            "stopped":any(x[0]=="STOP" for x in filled),
            "fills":[{"reason":r,"date":d,"price":p,"qty":round(q,6)} for r,d,p,q in filled]}


def evaluate_stock(row,bars,start,end):
    """All dates screened once; strategy trade calendars differ after filtering."""
    n=len(bars)
    closes=pd.Series([float(b["close"]) for b in bars],dtype="float64")
    volumes=pd.Series([float(b["volume"]) for b in bars],dtype="float64")
    high20=closes.shift(1).rolling(20,min_periods=20).max()
    vol20=volumes.shift(1).rolling(20,min_periods=20).mean()
    # Necessary, fast preliminary filters; exact seven HY checks run afterwards.
    pre=((closes>=high20)&(volumes>=1.5*vol20)&(vol20>0)&(volumes>0))
    invalid_jump=(closes.pct_change().abs()>.40).fillna(False)
    jump_recent=invalid_jump.rolling(65,min_periods=1).max()
    historical=pd.DataFrame({"Close":closes.values,"Volume":volumes.values},
                             index=pd.to_datetime([b["date"] for b in bars]))
    rsi,hist=indicators(closes.to_list())
    signal_ix={s:[] for s in STRATEGIES}
    examined=0
    hy_valid=0
    exact_hits=0
    skipped_jumps=0
    for i in range(66,n-HOLD_BARS):
        day=bars[i]["date"]
        if day<start or day>end or not bool(pre.iloc[i]):continue
        examined+=1
        if bool(jump_recent.iloc[i]):
            skipped_jumps+=1
            continue
        hist_cut=historical.iloc[:i+1]
        ctx={"basis":"close","intraday":False,"day":day.replace("-",""),
             "previous":bars[i-1]["date"].replace("-",""),
             "mode":"연구용 과거 확정 종가"}
        dt=pd.Timestamp(day)
        now=datetime(dt.year,dt.month,dt.day,16,20,tzinfo=KST)
        result=evaluate_current(row,hist_cut,None,ctx,now)
        hy_valid+=bool(result.get("valid"))
        if not daily_proxy(result):continue
        exact_hits+=1
        signal_ix["HY_daily"].append(i)
        if pass_macd_hist(hist,i):
            signal_ix["HY_daily_MACD"].append(i)
            if pass_rsi(rsi,i):
                signal_ix["HY_daily_RSI_MACD"].append(i)
    trades={s:[] for s in STRATEGIES}
    for strategy,inds in signal_ix.items():
        # Avoid overlapping positions within this stock. Across stocks, these
        # are independent hypothetical trades, NOT a funded portfolio.
        next_allowed=-1
        for ix in inds:
            if ix<=next_allowed:continue
            t=simulate_trade(bars,ix)
            if t is None:continue
            t.update({"ticker":row["ticker"],"name":row["name"],"market":row["market"],
                      "signal_date":bars[ix]["date"],"strategy":strategy})
            trades[strategy].append(t)
            exit_day=t["exit_date"]
            next_allowed=next((j for j in range(ix+1,len(bars))
                               if bars[j]["date"]==exit_day),len(bars)-1)
    return {"ticker":row["ticker"],"market":row["market"],
            "history_first":bars[0]["date"],"history_last":bars[-1]["date"],
            "bars":len(bars),"precheck_days":examined,
            "hy_evaluations":hy_valid,"hy_signal_stock_days":exact_hits,
            "corporate_action_excluded_prechecks":skipped_jumps,
            "raw_signal_counts":{s:len(v) for s,v in signal_ix.items()}},trades


def summary(trades):
    if not trades:
        return {"trades":0,"wins":0,"win_rate_pct":None,"mean_return_pct":None,
                "median_return_pct":None,"worst_return_pct":None,"stop_rate_pct":None,
                "mean_adverse_pct":None,"profit_factor":None}
    ps=pd.Series([t["pnl_pct"] for t in trades],dtype=float)
    gains=float(ps[ps>0].sum())
    losses=-float(ps[ps<0].sum())
    return {"trades":len(trades),"wins":int((ps>0).sum()),
            "win_rate_pct":round(float((ps>0).mean()*100),2),
            "mean_return_pct":round(float(ps.mean()),3),
            "median_return_pct":round(float(ps.median()),3),
            "worst_return_pct":round(float(ps.min()),3),
            "best_return_pct":round(float(ps.max()),3),
            "stop_rate_pct":round(100*sum(t["stopped"] for t in trades)/len(trades),2),
            "mean_adverse_pct":round(sum(t["mae_pct"] for t in trades)/len(trades),3),
            "profit_factor":round(gains/losses,3) if losses>0 else None,
            "per_year":{year:{"trades":len(group),"mean_return_pct":round(sum(t["pnl_pct"] for t in group)/len(group),3),
                               "win_rate_pct":round(100*sum(t["pnl_pct"]>0 for t in group)/len(group),1)}
                       for year,group in sorted(((year,[t for t in trades if t["signal_date"].startswith(year)])
                                                for year in sorted(set(t["signal_date"][:4] for t in trades))),
                                                key=lambda x:x[0])}}


def run(start=START,end=END,per_market=250,workers=6):
    market=fetch_market_universe()
    if len(market)<1800:raise RuntimeError("Full current stock universe unavailable")
    selected,distribution=select_universe(market,per_market)
    if any(distribution.get(m,0)<per_market for m in ("KOSPI","KOSDAQ")):
        raise RuntimeError("Incomplete sample; both exchanges required")
    histories={}
    errors={}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures={executor.submit(fetch_one,row,end):row for row in selected}
        for future in as_completed(futures):
            row,bars,err=future.result()
            if err:errors[row["ticker"]]=err
            else:histories[row["ticker"]]=(row,bars)
    results=[]
    trade_sets={s:[] for s in STRATEGIES}
    failures={}
    for code,(row,bars) in histories.items():
        try:
            counts,trades=evaluate_stock(row,bars,start,end)
            results.append(counts)
            for strategy in STRATEGIES:trade_sets[strategy].extend(trades[strategy])
        except Exception as exc:
            failures[code]=f"{type(exc).__name__}: {str(exc)[:160]}"
    data={"version":"hy_5of7_daily_proxy_v1",
          "period":{"start":start,"end":end,"market":"KOSPI+KOSDAQ"},
          "source":"Naver OHLCV / current market-cap universe, NOT KIS past intraday",
          "full_market_universe_as_of_scan":len(market),
          "selected_current_stocks":len(selected),"selection_counts":distribution,
          "history_success":len(histories),"history_failure":len(errors),
          "evaluation_success":len(results),"evaluation_failures":len(failures),
          "history_error_examples":dict(list(errors.items())[:12]),
          "evaluation_error_examples":dict(list(failures.items())[:12]),
          "precheck_stock_days":sum(x["precheck_days"] for x in results),
          "hy_confirmed_reference_evaluations":sum(x["hy_evaluations"] for x in results),
          "hy_daily_signal_stock_days":sum(x["hy_signal_stock_days"] for x in results),
          "suspected_corporate_action_prechecks_excluded":sum(x["corporate_action_excluded_prechecks"] for x in results),
          "signals_by_variant":{s:sum(x["raw_signal_counts"][s] for x in results) for s in STRATEGIES},
          "strategies":{s:summary(trade_sets[s]) for s in STRATEGIES},
          "sample_history_coverage":{"earliest":min((x["history_first"] for x in results),default=None),
                                     "latest":max((x["history_last"] for x in results),default=None)},
          "assumptions":{
              "timing":"close signal, buy next trading session open",
              "exit":"7% entry stop, +10/15/20% equal third take-profit, close at 20 sessions",
              "fee":"0.15% per side including simplified commission, taxes and slippage",
              "same_day_hit":"stop before target; gap through stop filled at worse open",
              "concurrent_holdings":"not capital constrained; disjoint within each stock only",
              "HY_seven":"all four mandatory + (stage or score); persistence unavailable",
              "MACD":"EMA(12,26,9) histogram positive and increasing",
              "RSI":"Wilder RSI(14) in [50,75]",
              "time_of_day_volume":"NOT TESTED; requires timestamped intraday volume",
              "benchmark_CAGR_or_portfolio_MDD":"NOT COMPUTED; no portfolio capital allocation",
              "survivorship":"present-day largest stocks, excludes delisted and historical changes"},
          "trades_by_strategy":trade_sets,"sampled_tickers":selected}
    print("RESULT_SUMMARY "+json.dumps({k:v for k,v in data.items()
           if k not in ("trades_by_strategy","sampled_tickers","history_error_examples","evaluation_error_examples")},
           ensure_ascii=False))
    return data


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--start",default=START)
    p.add_argument("--end",default=END)
    p.add_argument("--per-market",type=int,default=250)
    p.add_argument("--workers",type=int,default=6)
    p.add_argument("--output",default="hy_5of7_standalone_daily_result.json")
    args=p.parse_args()
    if not 1<=args.workers<=8 or not 20<=args.per_market<=500:p.error("Invalid bounded sample/workers")
    result=run(args.start,args.end,args.per_market,args.workers)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    if result["evaluation_success"]<int(.7*result["selected_current_stocks"]):
        raise SystemExit("Poor data coverage; reject backtest results")


if __name__=="__main__":
    main()
