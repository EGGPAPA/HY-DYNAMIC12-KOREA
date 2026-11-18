"""HY profit-focused exploratory study: B0, B01, B02 and each + B03 ATR exit.
Non-adjusted current survivor KOSPI/KOSDAQ data; NOT executable signal/live orders.
Tests two entry variants and a standalone exit modification.
Input from hy_full_market_b.py. No production writes.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import io, json, math, time
import pandas as pd
import numpy as np
import hy_full_market_b as market

START=market.START
END=market.END
HOLDOUT=market.HOLDOUT
INITIAL=10_000_000.
FEE_ONE_WAY=.0015
MAX_POS=2
MAX_MONTHLY=2
POSITION_LIMIT=.5
FIXED_STOP=.06
MAX_HOLD=60
RUNNER_HOLD=120
MIN_AVG_VALUE=2_000_000_000
WORKERS=24

def universe():
    table=pd.read_csv(io.BytesIO(market.get(market.BASE+"quotes.csv").content),
                      dtype={"종목코드":str},low_memory=False)
    if not {"종목코드","mrktCtg"}.issubset(table.columns):
        raise RuntimeError("quote schema changed")
    table=table[table["mrktCtg"].isin(["KOSPI","KOSDAQ"])].copy()
    table["code"]=table["종목코드"].astype(str).str.extract(r"(\d{6})",expand=False)
    table=table.dropna(subset=["code"]).drop_duplicates("code")
    codes=table.code.tolist()
    if len(codes)<1800:raise RuntimeError("Universe count unexpectedly low")
    return codes,Counter(table["mrktCtg"].tolist())

def fetch_histories(codes):
    hist={};fail=Counter();jumps={}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        tasks=[ex.submit(market.download_history,c) for c in codes]
        for i,fu in enumerate(as_completed(tasks),1):
            c,f,e=fu.result()
            if f is None:fail[e]+=1
            else:
                hist[c]=f
                if market.audit_jumps(f):jumps[c]=market.audit_jumps(f)
            if i%600==0 or i==len(codes):
                print("download",i,"/",len(codes),"ok",len(hist),"failed",sum(fail.values()),flush=True)
    if len(hist)<.85*len(codes):raise RuntimeError("Downloaded OHLCV too incomplete")
    return hist,fail,jumps

def build_candidates(code,f):
    """Signals confirmed at close t; any buy uses t+1 open. Shared B0 candidate conditions."""
    c=f.close.astype(float); o=f.open.astype(float); v=f.volume.astype(float)
    ma20=c.rolling(20,min_periods=20).mean()
    ma60=c.rolling(60,min_periods=60).mean()
    ma200=c.rolling(200,min_periods=200).mean()
    diff=c.diff()
    gains=diff.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    losses=(-diff.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=gains/losses.replace(0,np.nan)
    rsi=(100-100/(1+rs)).where(losses.ne(0),100)
    rsi=rsi.where(~((gains==0)&(losses==0)),50)
    macd=c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean()
    macds=macd.ewm(span=9,adjust=False).mean()
    macdh=macd-macds
    liquid=((c*v).rolling(20,min_periods=20).mean()>=MIN_AVG_VALUE)
    basic=((c>ma200)&(ma20>ma60)&rsi.between(50,70)&
           (rsi>rsi.shift(1))&(macd>macds)&(macdh>macdh.shift(1))&liquid)
    enough=(f.index>=START)&(f.index<=END)
    basic &= enough

    # B01: pullback towards 20-day MA within 5 sessions then bullish recovery.
    # Low-to-MA20 absolute proximity <= 2.5%, close within 0-6% above MA20.
    # Close up versus yesterday, MA20 rising. No hindsight.
    near_ma=((f.low/ma20 - 1).abs()<=.025).rolling(5,min_periods=5).max()>0
    pullback=(near_ma&(c>ma20)&(c<=ma20*1.06)&
              (c>c.shift(1))&(ma20>ma20.shift(5)))

    # B02: close breaks previous 20 sessions' HIGH, volume expands >=1.5x
    # previous 20 sessions, keeping signal timestamp strictly after lookback.
    prior20_high=f.high.shift(1).rolling(20,min_periods=20).max()
    volbase=v.shift(1).rolling(20,min_periods=20).mean()
    breakout=(c>prior20_high)&(v>=1.5*volbase)&(volbase>0)

    prev=c.shift(1)
    tr=pd.concat([f.high-f.low,(f.high-prev).abs(),(f.low-prev).abs()],
                 axis=1).max(axis=1)
    atr=tr.rolling(14,min_periods=14).mean()
    # Both entry styles use this baseline B0 20/60-day momentum rank.
    rank=(c.pct_change(20).clip(-.5,1)*60+
          c.pct_change(60).clip(-.5,2)*25+(rsi-50)*.15)

    valid=basic&atr.notna()&rank.notna()
    selected=f.index[valid.fillna(False)]
    return [
        (d,code,float(rank.loc[d]),bool(pullback.loc[d]),bool(breakout.loc[d]),
         float(atr.loc[d]),float(c.loc[d]))
        for d in selected if math.isfinite(float(atr.loc[d])) and float(atr.loc[d])>0
    ]

def signals_to_days(hist):
    by_day=defaultdict(list);counts=Counter()
    for code,f in hist.items():
        items=build_candidates(code,f)
        for item in items:
            by_day[item[0]].append(item)
            counts["B0"]+=1
            if item[3]:counts["B01"]+=1
            if item[4]:counts["B02"]+=1
    for d in by_day:
        by_day[d].sort(key=lambda x:(-x[2],x[1]))
    if counts["B01"]<100 or counts["B02"]<100:raise RuntimeError(f"Too few signals {counts}")
    return by_day,dict(counts)

def simulate(hist,by_day,strategy,start,end):
    entry_type=strategy.replace("+B03","")
    b03="+B03" in strategy
    days=sorted({d for f in hist.values() for d in f.index if start<=d<=end})
    if not days:raise RuntimeError("No days")
    positions={}; cash=INITIAL; last={};ledger=[];nav=[]
    purchases=Counter();events=Counter();distinct=set();accepted_signal_days=0
    def close_position(code,price,q,day,reason):
        nonlocal cash
        p=positions[code];q=min(q,p["qty"])
        if not q:return
        proceeds=q*float(price)*(1-FEE_ONE_WAY)
        p["proceeds"]+=proceeds;cash+=proceeds;p["qty"]-=q
        if p["qty"]==0:
            ledger.append({"code":code,"entry":str(p["entryday"].date()),
               "exit":str(day.date()),"pnl":p["proceeds"]-p["cost"],
               "return_pct":100*(p["proceeds"]/p["cost"]-1),"close_reason":reason})
            del positions[code]
    for i,day in enumerate(days):
        previous=days[i-1] if i else None
        candidates=by_day.get(previous,[])
        if entry_type=="B01":candidates=[it for it in candidates if it[3]]
        if entry_type=="B02":candidates=[it for it in candidates if it[4]]
        if candidates:accepted_signal_days+=1
        month=day.strftime("%Y-%m")
        for d,code,rank,pull,breakout,atr,signal_close in candidates:
            if len(positions)>=MAX_POS or purchases[month]>=MAX_MONTHLY:break
            if code in positions:continue
            f=hist.get(code)
            if f is None or day not in f.index:continue
            bar=f.loc[day]
            price=float(bar.open)
            if price<=0 or not math.isfinite(price):continue
            maximum_budget=min(cash,INITIAL*POSITION_LIMIT)
            qty=int(maximum_budget/(price*(1+FEE_ONE_WAY)))
            if qty<=0:continue
            cost=qty*price*(1+FEE_ONE_WAY)
            cash-=cost
            purchases[month]+=1;events["entries"]+=1;distinct.add(code)
            positions[code]={"qty":qty,"init_qty":qty,"entry":price,"entryday":day,
                "proceeds":0.,"cost":cost,"age":0,"took10":False,
                "peak_prior":price,"atr_prior":atr,"runner_on":False}
        for code in list(positions):
            f=hist[code]
            if day not in f.index:continue
            bar=f.loc[day];p=positions[code]
            p["age"]+=1;last[code]=float(bar.close)
            stop=p["entry"]*(1-FIXED_STOP)
            # Runner stop becomes active after yesterday's peak exceeded +10%.
            if b03 and p["runner_on"]:
                stop=max(stop,p["peak_prior"]-2.5*p["atr_prior"])
            if float(bar.low)<=stop:
                close_position(code,min(float(bar.open),stop),p["qty"],day,"stop")
                continue
            if not b03:
                for take in (.10,.15,.20):
                    if code not in positions:break
                    p=positions[code]
                    if take in p.get("done",set()) or float(bar.high)<p["entry"]*(1+take):continue
                    q=p["qty"] if take==.20 else max(1,int(p["init_qty"]/3))
                    close_position(code,p["entry"]*(1+take),q,day,f"take_{take}")
                    if code in positions:p.setdefault("done",set()).add(take)
            else:
                # Once +10%, sell only 25%, let 75% trend-run to ATR stop or 120 sessions.
                if not p["took10"] and float(bar.high)>=p["entry"]*1.10:
                    qty=max(1,int(p["init_qty"]*.25))
                    close_position(code,p["entry"]*1.10,qty,day,"partial10")
                    if code in positions:p["took10"]=True
            if code in positions:
                p=positions[code]
                if p["age"] >= (RUNNER_HOLD if b03 else MAX_HOLD):
                    close_position(code,float(bar.close),p["qty"],day,"age")
                elif b03:
                    # Today high and today's ATR become eligible for *tomorrow's* stop.
                    p["peak_prior"]=max(p["peak_prior"],float(bar.high))
                    if p["peak_prior"]>=p["entry"]*1.10:p["runner_on"]=True
                    atidx=f.index.get_loc(day)
                    if atidx>=14:
                        last15=f.iloc[atidx-14:atidx+1]
                        prv=last15.close.shift(1)
                        tr=pd.concat([last15.high-last15.low,(last15.high-prv).abs(),
                                      (last15.low-prv).abs()],axis=1).max(axis=1)
                        atr_now=float(tr.tail(14).mean())
                        if math.isfinite(atr_now) and atr_now>0:p["atr_prior"]=atr_now
        for code,p in positions.items():
            if day in hist[code].index:last[code]=float(hist[code].loc[day,"close"])
        equity=cash+sum(p["qty"]*last.get(c,p["entry"]) for c,p in positions.items())
        nav.append((day,equity))
    day=days[-1]
    for code in list(positions):
        close_position(code,last.get(code,positions[code]["entry"]),positions[code]["qty"],day,"end")
    vals=np.array([v for d,v in nav],dtype=float)
    peaks=np.maximum.accumulate(vals)
    wins=[r["pnl"] for r in ledger if r["pnl"]>0]
    losses=[-r["pnl"] for r in ledger if r["pnl"]<0]
    yrs=max((days[-1]-days[0]).days/365.25,.01)
    final=cash
    years={};annual={}
    prior=INITIAL
    for d,value in nav:years[str(d.year)]=value
    for yr,value in sorted(years.items()):
        annual[yr]=round(100*(value/prior-1),2);prior=value
    return {"period":[str(days[0].date()),str(days[-1].date())],
        "return_pct":round(100*(final/INITIAL-1),3),
        "cagr_pct":round(100*((final/INITIAL)**(1/yrs)-1),3),
        "mdd_pct":round(100*(vals/peaks-1).min(),3),
        "trades":len(ledger),
        "win_pct":round(100*len(wins)/len(ledger),2) if ledger else None,
        "avg_win_loss":round(np.mean(wins)/np.mean(losses),3) if wins and losses else None,
        "profit_factor":round(sum(wins)/sum(losses),3) if losses else None,
        "signals_days":accepted_signal_days,"distinct_buys":len(distinct),
        "final_cash":round(final),
        "year_returns":annual,
        "best_trades":sorted(ledger,key=lambda r:r["pnl"],reverse=True)[:5],
        "worst_trades":sorted(ledger,key=lambda r:r["pnl"])[:5]}

def main():
    startclock=time.monotonic()
    codes,markets=universe()
    hist,fail,jumps=fetch_histories(codes)
    results={}
    variants=["B0","B01","B02","B0+B03","B01+B03","B02+B03"]
    for uname,h in (("all_survivors_raw",hist),
                     ("exclude_gt35pct", {c:f for c,f in hist.items() if c not in jumps})):
        print("prepare",uname,len(h),flush=True)
        days,count=signals_to_days(h)
        out={}
        for variant in variants:
            runs={"full":simulate(h,days,variant,START,END),
                  "recent":simulate(h,days,variant,HOLDOUT,END)}
            out[variant]=runs
            print("RESULT",uname,variant,
                  runs["full"]["return_pct"],runs["full"]["mdd_pct"],runs["full"]["trades"],
                  "recent",runs["recent"]["return_pct"],flush=True)
        results[uname]={"tickers":len(h),"candidate_signals":count,"models":out}
    result={
       "description":"Experimental B0,B01/B02 entry filters and B03 exit (5y raw current survivors)",
       "markets":dict(markets),"downloaded_histories":len(hist),
       "unavailable_histories":dict(fail),"excluded_jump_tickers":len(jumps),
       "start":str(START.date()),"end":str(END.date()),"recent_start":str(HOLDOUT.date()),
       "cost":"0.15% each direction", "capital":INITIAL,
       "entries":{
          "B0":"base RSI14 50-70 rising, MACD above signal and rising, close>SMA200, SMA20>SMA60, traded value>=2bn",
          "B01":"B0 + low within 2.5% of SMA20 in last 5 days + close 0..6% above SMA20 + close up + SMA20 rising",
          "B02":"B0 + close > prev 20d high + volume>=1.5x previous 20d avg",
       },
       "exits":{
          "baseline":"-6% initial stop, equal thirds at 10%,15%,20%; 60 trade days max",
          "B03":"-6% initial stop, 25% take at +10%, remaining ATR2.5 trailing active only after +10%, max 120 trading days",
       },
       "risk_warnings":[
           "current-survivor only, delisted stocks omitted; cannot claim historical all-market",
           "raw nonadjusted prices; corporate actions and spikes distort signals/execution",
           "exclusion of >35% jumps creates selection bias and may remove legitimate winners",
           "SPACs/preferred shares and nonstandard securities not separately filtered",
           "next-day open/close/high-low OHLC assumption, stop first if collision",
           "each strategy max 2 holdings, max 2 new buys/month and max 50% initial capital per entry",
           "no independent parameter optimization, real orders or slippage validation",
           "recent subsample not fully out-of-sample because rules reviewed from prior work"
       ],
       "results":results,
       "runtime_seconds":round(time.monotonic()-startclock,1)}
    path=Path("experiments/hy_b_entry_exit_results.json");path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    compact={name:{
        "tickers":g["tickers"],"candidate_signals":g["candidate_signals"],
        "models":{v:{period:{k:run[k] for k in [
            "return_pct","cagr_pct","mdd_pct","trades","win_pct",
            "avg_win_loss","profit_factor","signals_days","year_returns"
        ] if k in run} for period,run in values.items()}
           for v,values in g["models"].items()}
    } for name,g in results.items()}
    print("FINAL_COMPARISON_JSON",json.dumps({
         "market_counts":dict(markets),"histories":len(hist),"excluded":len(jumps),
         "runtime_seconds":result["runtime_seconds"],"results":compact
    },ensure_ascii=False),flush=True)

if __name__=="__main__":main()
