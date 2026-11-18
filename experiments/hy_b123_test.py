"""Experiment: isolate B1 market filter, B2 relative-strength ranking, B3 ATR risk sizing.
B0/B1/B2/B3 use identical current-stock universe and trade friction; 5y EOD.
NOT adjusted or survivorship-free; results are exploratory, never used for orders.
"""
from __future__ import annotations
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import io
import json
import math
import time
import pandas as pd
import numpy as np
import hy_full_market_b as base

ETF={"KOSPI":"survivor median KOSPI","KOSDAQ":"survivor median KOSDAQ"}  # labels only, no ETF data
INITIAL=10_000_000.
FEE=.0015
STOP=.06
TRADING_RISK=.005
MAX_ALLOC=.5
MAX_POS=2
MAX_BUY_MONTH=2
MAX_HOLD=60
MIN_VALUE=2_000_000_000
TEST_START=base.START
TEST_END=base.END
LATE_START=base.HOLDOUT
WORKERS=24

def market_map_from_quotes():
    raw=base.get(base.BASE+"quotes.csv").content
    table=pd.read_csv(io.BytesIO(raw),dtype={"종목코드":str},low_memory=False)
    if not {"종목코드","mrktCtg"}.issubset(table.columns):
        raise RuntimeError("Missing market mapping fields")
    table["code"]=table["종목코드"].astype(str).str.extract(r"(\d{6})",expand=False)
    table=table[table.mrktCtg.isin(["KOSPI","KOSDAQ"])].dropna(subset=["code"])
    return dict(zip(table.code,table.mrktCtg))

def one_ticker(item):
    return base.download_history(item)

def get_market_proxies(hist,code_to_market):
    """Cross-sectional market proxies, with current-stock survivorship caveat:
    B1 risk-on: >50% of stocks above their SMA200 and breadth improving over 20 sessions.
    B2 benchmark: equal-weight mean daily return, winsorized +/-10% per stock.
    None of these are official KOSPI/KOSDAQ index histories.
    """
    out={}
    for market in ("KOSPI","KOSDAQ"):
        returns=[]
        breadth_items=[]
        for code,f in hist.items():
            if code_to_market.get(code)!=market:continue
            daily=f.close.pct_change(fill_method=None)
            daily=daily.where(daily.abs()<=.35).clip(-.10,.10)
            returns.append(daily.rename(code))
            sma200=f.close.rolling(200,min_periods=200).mean()
            breadth_items.append((f.close>sma200).where(sma200.notna()).rename(code))
        if len(returns)<100:raise RuntimeError("too few stocks for market proxy")
        frame=pd.concat(returns,axis=1,sort=True).sort_index()
        enough=frame.count(axis=1)>=30
        meanret=frame.mean(axis=1,skipna=True).where(enough).dropna()
        index=(1+meanret).cumprod()*100
        breadth_mat=pd.concat(breadth_items,axis=1,sort=True).sort_index()
        breadth=breadth_mat.mean(axis=1,skipna=True).reindex(index.index)
        regime=(breadth>.50)&(breadth>breadth.shift(20))
        out[market]={"close":index,"regime":regime.fillna(False),"breadth":breadth}
    print("market_proxies", {k:{"method":"equal-weight clipped index and 200d breadth",
        "rows":len(v["close"]),"start":str(v["close"].index.min().date()),
        "end":str(v["close"].index.max().date()),
        "breadth_mean":round(float(v["breadth"].loc[TEST_START:TEST_END].mean()),3),
        "breadth_max":round(float(v["breadth"].loc[TEST_START:TEST_END].max()),3),
        "risk_on_fraction":round(float(v["regime"].loc[TEST_START:TEST_END].mean()),3)}
        for k,v in out.items()},flush=True)
    return out

def derive_candidates(code,frame,market,proxies):
    f=frame
    c=f.close
    ma20=c.rolling(20,min_periods=20).mean()
    ma60=c.rolling(60,min_periods=60).mean()
    ma200=c.rolling(200,min_periods=200).mean()
    diff=c.diff()
    gain=diff.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    loss=(-diff.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=gain.div(loss.replace(0,np.nan))
    rsi=(100-100/(1+rs)).where(loss.ne(0),100)
    rsi=rsi.where(~((gain==0)&(loss==0)),50)
    macd=c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean()
    sig=macd.ewm(span=9,adjust=False).mean()
    h=macd-sig
    volume20=(c*f.volume).rolling(20,min_periods=20).mean()
    cond=(c>ma200)&(ma20>ma60)&rsi.between(50,70)&(rsi>rsi.shift(1))&(
        macd>sig)&(h>h.shift(1))&(volume20>=MIN_VALUE)
    cond &= (c.index>=TEST_START)&(c.index<=TEST_END)
    old_base_rank=(c.pct_change(20).clip(-.5,1)*60 +
                   c.pct_change(60).clip(-.5,2)*25 + (rsi-50)*.15)
    # B2: rank 6m & 12m benchmark-excess returns, volatility adjusted.
    idx_price=proxies[market]["close"].reindex(f.index).ffill(limit=3)
    r6=c.pct_change(126)-idx_price.pct_change(126,fill_method=None)
    r12=c.pct_change(252)-idx_price.pct_change(252,fill_method=None)
    annual_vol=c.pct_change(fill_method=None).rolling(60,min_periods=60).std()*math.sqrt(252)
    rank_b2=(.5*r6+.5*r12)/(annual_vol+.05)
    # Strictly equal candidate population for the variants: 252 daily bars minimum.
    cond &= rank_b2.notna() & old_base_rank.notna()
    prev=c.shift(1)
    tr=pd.concat([
        f.high-f.low,
        (f.high-prev).abs(),
        (f.low-prev).abs()
    ],axis=1).max(axis=1)
    atr=tr.rolling(14,min_periods=14).mean()
    regime=proxies[market]["regime"].reindex(f.index).fillna(False)
    dates=f.index[cond.fillna(False)]
    return [(d,code,float(old_base_rank.loc[d]),float(rank_b2.loc[d]),
             bool(regime.loc[d]),float(atr.loc[d]),float(c.loc[d]))
            for d in dates if pd.notna(atr.loc[d]) and float(atr.loc[d])>0]

def prepare_cand_map(allowed,history,markets,proxies):
    cand=defaultdict(list);total=0;errors=[]
    for code in allowed:
        f=history[code]
        market=markets.get(code)
        if market not in ETF:continue
        try:
            items=derive_candidates(code,f,market,proxies)
            for item in items:
                cand[item[0]].append(item)
            total+=len(items)
        except Exception as e:
            errors.append((code,str(e)[:160]))
    if errors:print("candidate_errors",len(errors),errors[:10],flush=True)
    if total<1000:raise RuntimeError(f"Unexpectedly low B signal count: {total}")
    return cand,total

def replay(hist,cand,proxies,variant,start,end):
    # Use only ETF trading dates (no synthetic user-made calendar)
    days=sorted(set().union(*[set(x["close"].loc[start:end].index) for x in proxies.values()]))
    if not days:raise RuntimeError("No market sessions")
    positions={};cash=INITIAL;last={};ledger=[];equities=[]
    monthly=Counter(); yearly=Counter(); used_codes=set()
    signal_days=0; eligible_signals=0; filtered_market=0
    def sell(code,price,quantity,day):
        nonlocal cash
        p=positions[code]
        q=min(int(quantity),p["qty"])
        if q<=0:return
        proceeds=q*price*(1-FEE)
        p["proceeds"]+=proceeds;cash+=proceeds;p["qty"]-=q
        if p["qty"]==0:
            pnl=p["proceeds"]-p["cost"]
            ledger.append({"code":code,"entry":str(p["day"].date()),"exit":str(day.date()),
                "pnl":float(pnl),"return_pct":100*(p["proceeds"]/p["cost"]-1)})
            del positions[code]
    for i,day in enumerate(days):
        prevday=days[i-1] if i else None
        if prevday in cand:
            rankix=3 if variant=="B2" else 2
            possible=cand[prevday]
            eligible_signals+=len(possible)
            if variant=="B1":
                filtered_market+=sum(not item[4] for item in possible)
                possible=[it for it in possible if it[4]]
            if possible:signal_days+=1
            key=day.strftime("%Y-%m")
            for item in sorted(possible,key=lambda item:(-item[rankix],item[1])):
                code=item[1]
                if code not in hist or code in positions or len(positions)>=MAX_POS or monthly[key]>=MAX_BUY_MONTH:
                    continue
                f=hist[code]
                if day not in f.index:continue
                row=f.loc[day]
                price=float(row.open)
                if not math.isfinite(price) or price<=0:continue
                capital=cash+sum(p["qty"]*last.get(c,p["entry"]) for c,p in positions.items())
                max_budget=min(cash,INITIAL*MAX_ALLOC)
                stop_pct=STOP
                if variant=="B3":
                    signal_atr=item[5]
                    # ATR 2x, with 2.5% floor and 12% cap to limit numerical split spikes.
                    stop_pct=min(.12,max(.025,2*signal_atr/item[6]))
                    if stop_pct<=0:continue
                    # risk budget at the planned stop. Gaps can cause larger realized losses.
                    risk_qty=int((capital*TRADING_RISK)/(price*stop_pct))
                    max_budget=min(max_budget,risk_qty*price)
                q=int(max_budget/(price*(1+FEE)))
                if q<1:continue
                totalcost=q*price*(1+FEE)
                if totalcost>cash:continue
                cash-=totalcost
                positions[code]={"entry":price,"qty":q,"orig":q,"day":day,
                    "cost":totalcost,"proceeds":0.,"age":0,
                    "done":set(),"stop_pct":stop_pct,"peak":price,"atr_prev":item[5]}
                monthly[key]+=1;yearly[str(day.year)]+=1;used_codes.add(code)
        for code in list(positions):
            f=hist[code]
            if day not in f.index:continue
            row=f.loc[day];p=positions[code];p["age"]+=1
            last[code]=float(row.close)
            stop=p["entry"]*(1-p["stop_pct"])
            if variant=="B3":
                # Yesterday's peak and ATR only: no lookahead from today's high.
                stop=max(stop,p["peak"]-2*p["atr_prev"])
            if float(row.low)<=stop:
                # If open gaps below stop, fill at worse open; pessimistic same-bar collision
                px=min(float(row.open),stop)
                sell(code,px,p["qty"],day);continue
            for target in (.10,.15,.20):
                if code not in positions:break
                p=positions[code]
                if target in p["done"] or float(row.high)<p["entry"]*(1+target):continue
                qty=p["qty"] if target==.20 else max(1,int(p["orig"]/3))
                sell(code,p["entry"]*(1+target),qty,day)
                if code in positions:positions[code]["done"].add(target)
            if code in positions:
                p=positions[code]
                if p["age"]>=MAX_HOLD:
                    sell(code,float(row.close),p["qty"],day)
                elif variant=="B3":
                    p["peak"]=max(p["peak"],float(row.high))
                    # Compute current known ATR for tomorrow's trailing stop, not today's.
                    loc=f.index.get_loc(day)
                    if loc>=14:
                        prior=f.iloc[loc-14:loc+1]
                        prev_close=prior.close.shift(1)
                        true_range=pd.concat([prior.high-prior.low,
                            (prior.high-prev_close).abs(),(prior.low-prev_close).abs()],
                            axis=1).max(axis=1)
                        newatr=true_range.tail(14).mean()
                        if math.isfinite(newatr) and newatr>0:p["atr_prev"]=float(newatr)
        portfolio=cash+sum(p["qty"]*last.get(c,p["entry"]) for c,p in positions.items())
        equities.append((day,portfolio))
    final_date=days[-1]
    for code in list(positions):
        sell(code,last.get(code,positions[code]["entry"]),positions[code]["qty"],final_date)
    vals=np.array([e for d,e in equities],dtype=float)
    peak=np.maximum.accumulate(vals)
    wins=[t["pnl"] for t in ledger if t["pnl"]>0]
    losses=[-t["pnl"] for t in ledger if t["pnl"]<0]
    final=cash
    yrs=max(.001,(days[-1]-days[0]).days/365.25)
    yearend={}
    for d,value in equities:yearend[str(d.year)]=value
    prev=INITIAL;yearlyreturns={}
    for year,endval in sorted(yearend.items()):
        yearlyreturns[year]=round(100*(endval/prev-1),2);prev=endval
    return {"period":[str(days[0].date()),str(days[-1].date())],
        "return_pct":round(100*(final/INITIAL-1),3),
        "cagr_pct":round(100*((final/INITIAL)**(1/yrs)-1),3),
        "mdd_pct":round(100*(vals/peak-1).min(),3),
        "trades":len(ledger),
        "win_pct":round(100*len(wins)/len(ledger),2) if ledger else None,
        "profit_factor":round(sum(wins)/sum(losses),3) if losses else None,
        "avg_win_loss":round(np.mean(wins)/np.mean(losses),3) if wins and losses else None,
        "cash_final":round(final),
        "signal_days":signal_days,"eligible_signals":eligible_signals,
        "market_filtered_signals":filtered_market,
        "unique_buys":len(used_codes),"buys_year":dict(yearly),
        "yearly_returns_pct":yearlyreturns}

def main():
    t=time.monotonic()
    code_map=market_map_from_quotes()
    tickers=sorted(code_map)
    fetched={};fault=Counter();jumped={}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        jobs={pool.submit(one_ticker,c):c for c in tickers}
        for i,future in enumerate(as_completed(jobs),1):
            code,frame,err=future.result()
            if frame is None:fault[str(err)[:80]]+=1
            else:
                fetched[code]=frame
                j=base.audit_jumps(frame)
                if j:jumped[code]=j
            if i%500==0 or i==len(tickers):
                print("fetch_progress",i,len(tickers),"ok",len(fetched),
                      "missing",sum(fault.values()),"time_s",round(time.monotonic()-t),flush=True)
    print("all_codes",len(tickers),"usable",len(fetched),"jumped",len(jumped),flush=True)
    if len(fetched)<.85*len(tickers):raise RuntimeError("Market universe download incomplete")
    proxies=get_market_proxies(fetched,code_map)
    results={}
    for universe_name, hist in (("all_current_raw",fetched),
                                ("no_gt35pct_raw", {k:v for k,v in fetched.items() if k not in jumped})):
        print("derive",universe_name,len(hist),flush=True)
        cand,count=prepare_cand_map(hist.keys(),hist,code_map,proxies)
        comparisons={}
        for variant in ("B0","B1","B2","B3"):
            comparisons[variant]={"five_year":replay(hist,cand,proxies,variant,TEST_START,TEST_END),
                                  "late_period":replay(hist,cand,proxies,variant,LATE_START,TEST_END)}
            x=comparisons[variant]["five_year"]
            print("RESULT",universe_name,variant,x["return_pct"],x["mdd_pct"],x["trades"],flush=True)
        results[universe_name]={"ticker_count":len(hist),"signal_rows":count,"variants":comparisons}
    output={"purpose":"B1-B3 individual variants, no production deployment",
      "data_source":"aikstockdata.com; current survivors; raw nonadjusted historical OHLCV",
      "universe_market_counts":dict(Counter(code_map.values())),
      "downloaded":len(fetched),"failed":dict(fault),
      "discontinuous_gt35pct_tickers":len(jumped),
      "study_dates":[str(TEST_START.date()),str(TEST_END.date())],
      "market_proxies":{"B1":"breadth fraction above SMA200 for survivors; risk-on >50% and improving 20 days","B2":"daily equal-weight clipped return proxy for each market; not official indices"},
      "variants":{
        "B0":"B signal; B-only ret20/ret60/RSI ranking; -6% fixed stop; 10/15/20% take profit",
        "B1":"B0 + market breadth >50% above individual SMA200 and breadth rising over 20 sessions",
        "B2":"B0 + change rank to volatility-adjusted 126d and 252d excess vs same-market equal-weight return benchmark",
        "B3":"B0 + 2xATR14 initial/trailing stop, 2.5-12% clamp, position size risks <=0.5% of NAV at stop"},
      "fixed_conditions":{
        "liquidity":"20d avg traded value >=2bn KRW",
        "friction":"0.15% fee/slippage each side (0.3% roundtrip assumption)",
        "positions":MAX_POS,"new_buys_per_month":MAX_BUY_MONTH,
        "max_holding_trading_days":MAX_HOLD,
        "shared_candidates":"B signal with 252-day history, all variants share same possible signals"},
      "risks":[
        "historical delisted stocks missing (survivorship bias)",
        "unadjusted corporate action price series",
        "Market breadth and equal-weight indexes of surviving stocks are NOT official KOSPI/KOSDAQ and are biased",
        "preferred shares, SPACs and foreign listings not separately excluded",
        "same daily OHLC can touch stop and take-profit; stop prioritized",
        "daily OHLC not executable limit order data",
        "B3 smaller capital exposure can mechanically lower MDD",
        "parameters not formally walk-forward optimized"
      ],"results":results,"runtime_seconds":round(time.monotonic()-t,1)}
    path=Path("experiments/hy_b123_results.json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    # print only short result summary + artifacts avoid massive logs
    compact={n:{k:{"full":v["five_year"],"recent":v["late_period"]}
                for k,v in part["variants"].items()} for n,part in results.items()}
    print("FINAL_SUMMARY_JSON",json.dumps({
        "universe_counts":{n:p["ticker_count"] for n,p in results.items()},
        "signal_rows":{n:p["signal_rows"] for n,p in results.items()},
        "markets":output["universe_market_counts"],"elapsed":output["runtime_seconds"],
        "variants":compact
    },ensure_ascii=False),flush=True)

if __name__=="__main__":main()
