"""HY five-stage INTEGRATED RESEARCH PILOT, not an operational strategy.

Data-integrity gate fails unless historical as-listed universe, delisted outcomes,
corporate-action adjustments, and actual quote snapshots exist. This experiment
still evaluates a *current listed* 360-stock sample and displays the missing gates.

Timeline: classify Minervini 5..8 on previous month-end, require the UNMODIFIED
HY mandatory4 + (stage or score) on next month's confirmed DAILY CLOSE, buy the
following OPEN. Compare two exit styles in cash-constrained portfolios. Absolutely
no intraday live persistence or full-market claims.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from math import floor, isfinite
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
import sys
import statistics

import pandas as pd

ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from scripts.backtest_minervini_5to8_forward import (
    select_sample, price_history_good, monthly_anchors, outcomes, download,
)
from market_update_data import fetch_market_universe
from minervini_trend import evaluate_trend, assign_market_rs
from rise_live_analysis import evaluate_current, MANDATORY_KEYS, KST

INITIAL_CASH=50_000_000.
SIDE_FEE=.0015
HARD_STOP=.07
TRADE_RISK_FRACTION=.005
MAX_POSITIONS=5
EVAL_START="2023-01-02"
EVAL_END="2026-11-20"
SIGNAL_END="2026-04-30"
STRATEGIES=("fixed","runner")
GROUPS=(5,6,7,8)


def hy_daily_signal(report):
    """Existing daily HY checks, never claiming actual persistence was met."""
    chk=report.get("checks") or {}
    return bool(report.get("valid")
                and not str(report.get("label","")).startswith("🔴")
                and all(chk.get(k) is True for k in MANDATORY_KEYS)
                and (chk.get("stage") is True or chk.get("score") is True))


def point_in_time_data_gate():
    """Explicit proof checklist: current Naver chart sample is NOT a PIT market."""
    checks={
        "as_listed_full_KRX_universe":False,
        "delisted_and_recovery_prices":False,
        "corporate_actions_fully_reconciled":False,
        "historical_financials_by_disclosure_date":False,
        "historical_KIS_quote_and_persistence_receipts":False,
    }
    return {"eligible_for_production":all(checks.values()),"checks":checks,
            "note":"Missing evidence is a FAIL, not an assumed pass. This is a survivor-biased pilot."}


def month_after(month):
    y,m=map(int,month.split("-"))
    return f"{y+1}-01" if m==12 else f"{y}-{m+1:02d}"


def signal_after_month_end(raw, anchors, start=EVAL_START, signal_end=SIGNAL_END):
    """Generate only earliest next-month HY daily proxy signal per stock/cohort."""
    indices={code:{b["date"]:i for i,b in enumerate(bars)}
             for code,(r,bars) in raw.items()}
    selected_size=len(raw)
    by_group=Counter()
    by_month=defaultdict(Counter)
    by_year=defaultdict(lambda:defaultdict(Counter))
    hits={}
    ranking_failures=0
    evaluations=0
    hy_checked_days=0
    scorecard=[]
    for month,asof in anchors.items():
        if asof<"2022-12-01" or asof>SIGNAL_END:continue
        next_month=month_after(month)
        preliminary={}
        for code,(row,bars) in raw.items():
            i=indices[code].get(asof)
            if i is None or i<253 or not price_history_good(bars,i):continue
            x=evaluate_trend(bars[:i+1],asof=asof)
            if x.get("valid"):preliminary[code]=(row,bars,i,x)
        rs=assign_market_rs(
            {code:a[3] for code,a in preliminary.items()},
            universe_size=selected_size,minimum_count=max(50,int(selected_size*.55)),
            minimum_coverage=.70)
        if not rs["ready"]:
            ranking_failures+=1
            scorecard.append({"month":month,"accepted":False,
                              "eligible":len(preliminary),"reason":rs["reason"]})
            continue
        valid_groups=Counter()
        for code,(row,bars,index,base) in preliminary.items():
            rank=rs["ranks"][code]
            res=evaluate_trend(bars[:index+1],asof=asof,rs_percentile=rank)
            if not res["valid"] or res["pending"]!=0:continue
            group=res["passed"]
            if group not in GROUPS:continue
            valid_groups[group]+=1
            by_group[group]+=1
            by_month[month][group]+=1
            by_year[month[:4]][group]["classified"]+=1
            for k in range(index+1,len(bars)-1):
                day=bars[k]["date"]
                if day[:7]!=next_month:break
                if day<start or day>signal_end:continue
                if k<65 or float(bars[k]["volume"])<=0:continue
                prev=bars[k-20:k]
                prior_high=max(float(t["close"]) for t in prev)
                avg_vol=sum(float(t["volume"]) for t in prev)/20
                if avg_vol<=0 or float(bars[k]["close"])<prior_high or float(bars[k]["volume"])<avg_vol*1.5:
                    continue
                evaluations+=1
                # Past-data-only window, no future quote or next bar in evaluator.
                hist=pd.DataFrame(
                    {"Close":[float(b["close"]) for b in bars[:k+1]],
                     "Volume":[float(b["volume"]) for b in bars[:k+1]]},
                     index=pd.to_datetime([b["date"] for b in bars[:k+1]]))
                when=pd.Timestamp(day)
                ctx={"basis":"close","intraday":False,
                     "day":day.replace("-",""),
                     "previous":bars[k-1]["date"].replace("-",""),
                     "mode":"historical close proxy"}
                result=evaluate_current(row,hist,None,ctx,
                    datetime(when.year,when.month,when.day,16,20,tzinfo=KST))
                if not hy_daily_signal(result):continue
                hy_checked_days+=1
                entry_bar=bars[k+1]
                if entry_bar["date"]>EVAL_END:break
                # EOD of day k is the last info available for next OPEN k+1.
                event={"ticker":code,"name":row["name"],"market":row["market"],
                       "group":group,"cohort_month":month,"cohort_asof":asof,
                       "signal_date":day,"entry_date":entry_bar["date"],
                       "entry_index":k+1,"sample_rs":rank,
                       "hy_score":result.get("score",0),
                       "distance_pct":abs(float(result.get("gap",0) or 0))}
                # For a month only one signal per stock, so no same-month re-entry.
                hits[(code,month)]=event
                by_year[month[:4]][group]["hy_signals"]+=1
                break
        scorecard.append({"month":month,"accepted":True,"eligible":len(preliminary),
                          "group_counts":dict(valid_groups)})
    events=sorted(hits.values(),key=lambda x:(x["entry_date"],-x["hy_score"],-x["sample_rs"],x["ticker"]))
    return events,{"months_scored":sum(x["accepted"] for x in scorecard),
                   "months_unranked":ranking_failures,
                   "monthly_cohort_counts":{m:dict(c) for m,c in by_month.items()},
                   "year_group_counts":{y:{str(g):dict(v) for g,v in grp.items()} for y,grp in by_year.items()},
                   "total_cohort_stock_months":sum(by_group.values()),
                   "hy_precheck_evaluations":evaluations,
                   "hy_proxy_signal_stock_months":hy_checked_days,
                   "scorecard":scorecard}


def period_summary(events,raw):
    """Separate selection quality (returns after month-end) from HY portfolio P&L."""
    from collections import defaultdict
    rows=defaultdict(list)
    annual=defaultdict(lambda:defaultdict(list))
    for event in events:
        ticker=event["ticker"]
        bars=raw[ticker][1]
        index=next((i for i,b in enumerate(bars) if b["date"]==event["cohort_asof"]),None)
        if index is None:continue
        outcome=outcomes(bars,index)
        if outcome is None:continue
        for h in ("3m","6m"):
            d=outcome[h]
            rows[(event["group"],h)].append(d)
            annual[(event["cohort_month"][:4],h)][event["group"]].append(d)
    def aggregate(items):
        if not items:return {"events":0}
        n=len(items)
        return {
            "events":n,
            "mean_close_return_pct":round(statistics.mean(x["return_pct"] for x in items),3),
            "median_close_return_pct":round(statistics.median(x["return_pct"] for x in items),3),
            "hit50_pct":round(100*sum(x["hit50"] for x in items)/n,2),
            "experienced_minus10_pct":round(100*sum(x["down10"] for x in items)/n,2),
        }
    return {"by_group":{str(g):{h:aggregate(rows[(g,h)]) for h in ("3m","6m")}
                        for g in GROUPS},
            "by_year":{f"{y}_{h}":{str(g):aggregate(v) for g,v in groups.items()}
                       for (y,h),groups in sorted(annual.items())}}


def trade_close(position, shares, price, date, reason, cash):
    """Record sell with fee, allocate realized proceeds; no unknown future prices."""
    shares=min(int(shares),position["shares"])
    if shares<=0:return cash
    amount=shares*float(price)*(1-SIDE_FEE)
    position["proceeds"]+=amount
    position["shares"]-=shares
    position["fills"].append({"date":date,"reason":reason,"shares":shares,
                              "fill_price":round(float(price),3)})
    return cash+amount


def portfolio(events,raw,group,variant,initial=INITIAL_CASH):
    """Cash funded portfolio, next session open, initial cash only can finance buys.

    Daily trailing stop uses only PREVIOUS confirmed closes; same-day stop prioritized
    over target and gap below stop filled at the open. No leverage or forced entry.
    """
    trade_universe={code:b for code,(r,b) in raw.items()}
    lookups={code:{b["date"]:i for i,b in enumerate(bars)} for code,bars in trade_universe.items()}
    dates=sorted({b["date"] for bars in trade_universe.values()
                  for b in bars if EVAL_START<=b["date"]<=EVAL_END})
    orders=defaultdict(list)
    for e in events:
        if e["group"]==group and EVAL_START<=e["entry_date"]<=EVAL_END:
            orders[e["entry_date"]].append(e)
    cash=float(initial)
    positions={}
    closed=[]
    daily=[]
    stats=Counter()
    last_mark={}
    for day in dates:
        # At OPEN, no intraday proceeds are pre-funded into fresh purchases.
        prev_equity=cash+sum(p["shares"]*last_mark.get(code,p["entry"]) for code,p in positions.items())
        candidates=sorted(orders.get(day,[]),
                          key=lambda e:(-e["hy_score"],-e["sample_rs"],e["distance_pct"],e["ticker"]))
        for e in candidates:
            code=e["ticker"]
            if code in positions:stats["duplicate_open_position"]+=1;continue
            if len(positions)>=MAX_POSITIONS:stats["no_position_slot"]+=1;continue
            bar_index=lookups[code].get(day)
            if bar_index is None or bar_index<=0:stats["missing_entry_day"]+=1;continue
            bar=trade_universe[code][bar_index]
            op=float(bar["open"])
            if op<=0 or not isfinite(op) or float(bar["volume"])<=0:
                stats["bad_or_halted_open"]+=1;continue
            # Maximum planned loss: 0.5% of pre-session equity
            budget=min(cash,prev_equity/MAX_POSITIONS,prev_equity*TRADE_RISK_FRACTION/HARD_STOP)
            qty=floor(budget/(op*(1+SIDE_FEE)))
            if qty<=0:stats["not_enough_cash_or_risk_budget"]+=1;continue
            invested=qty*op*(1+SIDE_FEE)
            cash-=invested
            positions[code]={"ticker":code,"name":e["name"],"market":e["market"],
                             "group":group,"variant":variant,"signal":e["signal_date"],
                             "entry_date":day,"entry":op,"initial_qty":qty,
                             "shares":qty,"initial_invested":invested,"proceeds":0.,
                             "initial_stop":op*(1-HARD_STOP),"stop":op*(1-HARD_STOP),
                             "peak_close":op,"peak_close_prev":op,
                             "partial":False,"targets":set(),"held":0,
                             "fills":[],"risk_won":qty*op*HARD_STOP}
            stats["entered"]+=1
        for code,p in list(positions.items()):
            idx=lookups[code].get(day)
            if idx is None:
                stats["missing_price_during_hold"]+=1
                continue
            bar=trade_universe[code][idx]
            op,hi,lo,cl=(float(bar[k]) for k in ("open","high","low","close"))
            if not all(isfinite(v) and v>0 for v in (op,hi,lo,cl)):
                stats["bad_price_during_hold"]+=1
                continue
            p["held"]+=1
            previous_close=p["peak_close"]
            if variant=="runner" and p["partial"] and idx>20:
                prior_ma20=sum(float(x["close"]) for x in trade_universe[code][idx-20:idx])/20
                # Ratchet from values known BEFORE today's OHLC, never today's high.
                p["stop"]=max(p["stop"],p["entry"],
                              previous_close*.88,prior_ma20*.97)
            stop=p["stop"]
            # If both stop and target touched, take the less favourable STOP first.
            if lo<=stop:
                cash=trade_close(p,p["shares"],min(op,stop),day,"STOP",cash)
            else:
                if variant=="fixed":
                    for j,take in enumerate((.10,.15,.20)):
                        if j in p["targets"]:continue
                        if hi>=p["entry"]*(1+take):
                            q=(p["initial_qty"]//3 if j<2 else p["shares"])
                            cash=trade_close(p,q,p["entry"]*(1+take),day,f"TP_{take}",cash)
                            p["targets"].add(j)
                else:
                    if not p["partial"] and hi>=p["entry"]*(1+2*HARD_STOP):
                        q=max(1,floor(p["initial_qty"]*.25))
                        cash=trade_close(p,q,p["entry"]*(1+2*HARD_STOP),day,"2R_25pct",cash)
                        p["partial"]=True
            # Ratchet only after this completed session is known; no use earlier.
            p["peak_close"]=max(p["peak_close"],cl)
            last_mark[code]=cl
            max_days=20 if variant=="fixed" else 126
            if p["shares"] and p["held"]>=max_days:
                cash=trade_close(p,p["shares"],cl,day,"TIME",cash)
            if p["shares"]==0:
                pnl=p["proceeds"]-p["initial_invested"]
                closed.append({"ticker":code,"group":group,"variant":variant,
                               "signal_date":p["signal"],"entry_date":p["entry_date"],
                               "exit_date":day,"return_pct":round(100*pnl/p["initial_invested"],4),
                               "pnl_won":round(pnl,2),"risk_won":round(p["risk_won"],2),
                               "R_multiple":round(pnl/p["risk_won"],4) if p["risk_won"] else None,
                               "fills":p["fills"]})
                del positions[code]
        equity=cash+sum(p["shares"]*last_mark.get(code,p["entry"]) for code,p in positions.items())
        daily.append({"date":day,"equity":equity,"cash":cash,"positions":len(positions)})
    if daily:
        for code,p in list(positions.items()):
            day=daily[-1]["date"]
            price=last_mark.get(code,p["entry"])
            cash=trade_close(p,p["shares"],price,day,"STUDY_END",cash)
            pnl=p["proceeds"]-p["initial_invested"]
            closed.append({"ticker":code,"group":group,"variant":variant,
                           "signal_date":p["signal"],"entry_date":p["entry_date"],
                           "exit_date":day,"return_pct":round(100*pnl/p["initial_invested"],4),
                           "pnl_won":round(pnl,2),"risk_won":round(p["risk_won"],2),
                           "R_multiple":round(pnl/p["risk_won"],4) if p["risk_won"] else None,
                           "fills":p["fills"]})
        # Final equity includes liquidation at study end.
        daily[-1].update(equity=cash,cash=cash,positions=0)
    best=initial
    worst_drawdown=0.
    for d in daily:
        best=max(best,d["equity"])
        worst_drawdown=min(worst_drawdown,d["equity"]/best-1)
    years=(pd.Timestamp(EVAL_END)-pd.Timestamp(EVAL_START)).days/365.25
    ret=(cash/initial-1)*100
    cagr=((cash/initial)**(1/years)-1)*100 if cash>0 else -100
    profit=sum(max(0,t["pnl_won"]) for t in closed)
    losses=sum(-min(0,t["pnl_won"]) for t in closed)
    year_stats={}
    for year in sorted({x["date"][:4] for x in daily}):
        subset=[x for x in daily if x["date"].startswith(year)]
        if subset:
            before=next((x["equity"] for x in daily if x["date"]<subset[0]["date"]),initial)
            year_stats[year]={"return_pct":round(100*(subset[-1]["equity"]/before-1),3),
                              "trades_entered":sum(t["entry_date"].startswith(year) for t in closed)}
    report={
        "group":group,"exit_variant":variant,
        "initial_cash_won":initial,"ending_cash_won":round(cash,2),
        "total_return_pct":round(ret,3),"CAGR_pct":round(cagr,3),
        "MDD_pct":round(worst_drawdown*100,3),
        "completed_trades":len(closed),
        "win_rate_pct":round(100*sum(x["pnl_won"]>0 for x in closed)/len(closed),2) if closed else None,
        "mean_trade_pct":round(statistics.mean(x["return_pct"] for x in closed),3) if closed else None,
        "median_trade_pct":round(statistics.median(x["return_pct"] for x in closed),3) if closed else None,
        "profit_factor":round(profit/losses,3) if losses>0 else None,
        "avg_invested_pct":round(100*statistics.mean((x["equity"]-x["cash"])/x["equity"] for x in daily),2) if daily else None,
        "by_year":year_stats,"order_stats":dict(stats)
    }
    return report,closed,daily


def run(per_market=180,workers=6):
    listings=fetch_market_universe()
    if len(listings)<1800:raise RuntimeError("KRX current universe incomplete")
    chosen,counts=select_sample(listings,per_market)
    if any(counts.get(m,0)<per_market for m in ("KOSPI","KOSDAQ")):
        raise RuntimeError("Current stocks from both markets missing")
    raw={}
    failures={}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for f in as_completed([pool.submit(download,row) for row in chosen]):
            row,bars,error=f.result()
            if error:failures[row["ticker"]]=error
            else:raw[row["ticker"]]=(row,bars)
    if len(raw)<int(len(chosen)*.70):
        raise RuntimeError("OHLCV coverage under 70%; refuse to report pilot")
    anchors=monthly_anchors(raw)
    events,quality=signal_after_month_end(raw,anchors)
    forward=period_summary(events,raw)
    matrix=[]
    trade_examples={}
    for group in GROUPS:
        for variant in STRATEGIES:
            perf,trades,daily=portfolio(events,raw,group,variant)
            matrix.append(perf)
            trade_examples[f"{group}_{variant}"]=trades[:12]
    gate=point_in_time_data_gate()
    output={
        "study":"HY_INTEGRATED_FIVE_STAGE_PILOT",
        "valid_production_backtest":gate["eligible_for_production"],
        "status":"RESEARCH_PILOT_ONLY_NOT_A_FULL_MARKET_PROOF",
        "data_integrity":gate,
        "coverage":{"current_KRX_listed":len(listings),
                    "sample_selected":len(chosen),
                    "market_split":counts,
                    "history_loaded":len(raw),"history_failures":len(failures),
                    "failed_examples":dict(list(failures.items())[:10])},
        "design":{
            "universe":"current cap-ranked KOSPI & KOSDAQ, survivor bias",
            "minervini":"exact 5/8..8/8 on previous month-end with sample-only RS",
            "HY":"4 mandatory and stage-or-score from daily close, persistence unavailable",
            "signal":"earliest qualifying day next calendar month after cohort",
            "entry":"next session open, choose by HY score, then RS",
            "risk":"0.5% planned equity per trade, 7% initial stop, max 5 stocks, no leverage",
            "fixed":"+10/15/20% thirds, 20 sessions max",
            "runner":"at +2R sell 25%; remainder prev-close peak 12% / 20d MA stop; 126 sessions max",
            "fees":"0.15% per transaction side, gap stop at worse opening price",
            "biases":"full PIT listings, delisted returns, reconciled corporate actions, actual 10-60s quotes missing"},
        "stage2_stage3":{"monthly_yearly_counts":quality,
                         "horizon_3m_6m":forward},
        "stage4":{"HY_proxy_events":len(events),
                  "HY_daily_prechecks":quality["hy_precheck_evaluations"],
                  "actual_live_5of7_backtest":False,
                  "event_samples":events[:25]},
        "stage5":{"portfolio":matrix,"example_closed_trades":trade_examples},
        "explicit_result":"Do NOT interpret these portfolio metrics as live HY or full-market verified returns"
    }
    print("INTEGRATED_RESULT "+json.dumps({
       "status":output["status"],"valid_production_backtest":False,
       "coverage":output["coverage"],
       "stage2":{"months":quality["months_scored"],"monthly_cohorts":quality["total_cohort_stock_months"]},
       "stage3_years":list(forward["by_year"]),
       "stage4":{"signal_events":len(events),"HY_prechecks":quality["hy_precheck_evaluations"]},
       "stage5":matrix},ensure_ascii=False))
    return output


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--per-market",type=int,default=180)
    p.add_argument("--workers",type=int,default=6)
    p.add_argument("--output",default="hy_integrated_pilot.json")
    a=p.parse_args()
    if not 30<=a.per_market<=250 or not 1<=a.workers<=8:p.error("Out of bounded range")
    result=run(a.per_market,a.workers)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__=="__main__":
    main()
