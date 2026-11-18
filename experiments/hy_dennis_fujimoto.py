"""HY: Richard Dennis (Turtle 55) vs Fujimoto Shigeru (RSI30) exploratory replay.
5-year, long-only cash market, current-survivor Korean stocks, RAW non-adjusted OHLCV.
Shigeru F is ONLY a technical proxy: no point-in-time financials, order book or 5-min bars.
Dennis T is a daily-bar, next-open variant: not the original futures intraday executions.
Neither model is connected to production HY, brokerage or alerts.
"""
from __future__ import annotations
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import io, json, math, time
import numpy as np
import pandas as pd
import hy_full_market_b as b

START=b.START
END=b.END
RECENT=b.HOLDOUT
INIT=10_000_000.
FEE=.0015
MAX_POS=2
MONTHLY_NEW=2
MAX_STOCK_FRAC=.50
MIN_DAILY_VALUE=2_000_000_000
WORKERS=24
NAMES=("B0","T55","T55_PYRAMID","F_RSI30","F_RSI_MACD","F_RSI_STOP")

def get_list():
    df=pd.read_csv(io.BytesIO(b.get(b.BASE+"quotes.csv").content),
                   dtype={"종목코드":str},low_memory=False)
    df=df[df["mrktCtg"].isin(["KOSPI","KOSDAQ"])].copy()
    df["code"]=df["종목코드"].astype(str).str.extract(r"(\d{6})",expand=False)
    df=df.dropna(subset=["code"]).drop_duplicates("code")
    if len(df)<1800:raise RuntimeError("not enough tickers")
    return list(df.code), dict(Counter(df.mrktCtg))

def get_history(codes):
    hist={};bad=Counter();suspicious={}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        tasks=[pool.submit(b.download_history,code) for code in codes]
        for i,task in enumerate(as_completed(tasks),1):
            code,frame,error=task.result()
            if frame is None:bad[str(error)[:100]]+=1
            else:
                hist[code]=frame
                z=b.audit_jumps(frame)
                if z:suspicious[code]=z
            if i%550==0 or i==len(codes):
                print("DOWNLOAD",i,len(codes),"ok",len(hist),"failed",sum(bad.values()),flush=True)
    if len(hist)<.85*len(codes):raise RuntimeError("Data coverage failed")
    return hist,bad,suspicious

def decorate(code,f):
    c=f.close.astype(float);h=f.high.astype(float);lo=f.low.astype(float)
    delta=c.diff()
    gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=gain.div(loss.replace(0,np.nan))
    rsi=(100-100/(1+rs)).where(loss.ne(0),100)
    rsi=rsi.where(~((gain==0)&(loss==0)),50)
    macd=c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean()
    signal=macd.ewm(span=9,adjust=False).mean()
    hist=macd-signal
    ma20=c.rolling(20,min_periods=20).mean()
    ma60=c.rolling(60,min_periods=60).mean()
    ma200=c.rolling(200,min_periods=200).mean()
    prev=c.shift(1)
    tr=pd.concat([h-lo,(h-prev).abs(),(lo-prev).abs()],axis=1).max(axis=1)
    n=tr.ewm(alpha=1/20,adjust=False,min_periods=20).mean()
    top55=h.shift(1).rolling(55,min_periods=55).max()
    lower20=lo.shift(1).rolling(20,min_periods=20).min()
    traded=(c*f.volume).rolling(20,min_periods=20).mean()
    liquid=(traded>=MIN_DAILY_VALUE)
    # All technical inputs from current or earlier completed daily bars.
    b0=((c>ma200)&(ma20>ma60)&rsi.between(50,70)&(rsi>rsi.shift(1))&
        (macd>signal)&((macd-signal)>(macd-signal).shift(1))&liquid)
    turtle=(c>top55)&(c.shift(1)<=top55.shift(1))&liquid
    # RSI 30 crossing below: entry t+1 at open, no prediction.
    frsi=(rsi<=30)&(rsi.shift(1)>30)&liquid
    # Alternative: oversold RSI <=30 in previous 5 sessions, recovering to 30..45;
    # histogram crosses upward zero. This is a hypothetical stricter rule, not
    # Fujimoto's personal discretionary judgment.
    recent_low=rsi.shift(1).rolling(5,min_periods=1).min()
    fmacd=(recent_low<=30)&rsi.between(30,45)&(rsi>rsi.shift(1))&(
        hist>0)&(hist.shift(1)<=0)&liquid
    rank_b0=c.pct_change(20).clip(-.5,1)*60+c.pct_change(60).clip(-.5,2)*25+(rsi-50)*.15
    # Turtle rank by breakout margin relative to ATR + recent liquidity
    t_rank=(c-top55)/n+np.log1p(traded/1e9)*.15
    # More deeply oversold and more liquid gets slightly higher precedence.
    f_rank=(40-rsi)+np.log1p(traded/1e9)*.4
    events=[]
    wanted=(f.index>=START)&(f.index<=END)
    for label,mask,rank in (("B0",b0,rank_b0),("T55",turtle,t_rank),
                            ("F_RSI30",frsi,f_rank),("F_RSI_MACD",fmacd,f_rank)):
        indexes=f.index[(mask&wanted).fillna(False)]
        for dt in indexes:
            nn=float(n.loc[dt]);score=float(rank.loc[dt])
            if math.isfinite(nn) and nn>0 and math.isfinite(score):
                events.append((dt,label,code,score,nn,float(c.loc[dt])))
    f=f.copy()
    f["n20"]=n
    f["prior20low"]=lower20
    f["rsi14"]=rsi
    return events,f

def prepare(hist):
    days=defaultdict(lambda:defaultdict(list));facts={};ct=Counter()
    for code,f in hist.items():
        events,frame=decorate(code,f)
        facts[code]=frame
        for d,label,c,rank,n,cp in events:
            days[d][label].append((c,rank,n,cp))
            ct[label]+=1
    for d,groups in days.items():
        for label,items in groups.items():items.sort(key=lambda x:(-x[1],x[0]))
    return facts,days,ct

def replay(facts,signals,mode,lo,hi):
    sessions=sorted({d for frame in facts.values() for d in frame.index if lo<=d<=hi})
    if not sessions:raise RuntimeError("empty sessions")
    positions={};cash=INIT;lastclose={};records=[];nav=[]
    months=Counter();years=Counter();filled_codes=set();signal_days=0
    gross_invested=0.;avg_exposure_sum=0.
    label=("B0" if mode=="B0" else "T55" if mode.startswith("T55") else
           "F_RSI30" if mode!="F_RSI_MACD" else "F_RSI_MACD")

    def equity():
        return cash+sum(p["qty"]*lastclose.get(c,p["last_price"]) for c,p in positions.items())
    def sell(c,amount,price,day,reason):
        nonlocal cash
        p=positions[c]
        q=min(int(amount),p["qty"])
        if q<1:return
        receive=q*price*(1-FEE);cash+=receive;p["proceeds"]+=receive;p["qty"]-=q
        p["last_price"]=float(price)
        if not p["qty"]:
            profit=p["proceeds"]-p["cost"]
            records.append({"code":c,"entry":str(p["start"].date()),"exit":str(day.date()),
                "pnl":profit,"return_pct":100*(p["proceeds"]/p["cost"]-1),
                "days":p["age"],"reason":reason,"units":p["units"]})
            del positions[c]
    def add(c,price,shares,day):
        nonlocal cash,gross_invested
        p=positions[c]
        affordable=int(cash/(price*(1+FEE)))
        q=min(int(shares),affordable)
        if q<1:return False
        cost=q*price*(1+FEE)
        cash-=cost;p["cost"]+=cost;p["qty"]+=q;p["units"]+=1
        p["last_add"]=price;p["last_price"]=price
        gross_invested+=cost
        return True

    for idx,day in enumerate(sessions):
        prev=sessions[idx-1] if idx else None
        seen=signals.get(prev,{})
        potential=seen.get(label,[])
        if potential:signal_days+=1
        for code,rank,n,signalprice in potential:
            key=day.strftime("%Y-%m")
            if len(positions)>=MAX_POS or months[key]>=MONTHLY_NEW:break
            if code in positions:continue
            f=facts[code]
            if day not in f.index:continue
            price=float(f.loc[day,"open"])
            if price<=0 or not math.isfinite(price):continue
            total_eq=equity()
            stock_cap=min(cash,total_eq*MAX_STOCK_FRAC)
            if mode.startswith("T55"):
                # 1% account risk per original 2N at position initiation;
                # no leverage, one of max 2 positions, add 0.5N up to four units.
                unit_shares=int(total_eq*.01/(2*n))
                qty=min(unit_shares,int(stock_cap/(price*(1+FEE))))
                stop=max(.01,price-2*n)
            elif mode=="B0":
                qty=int(stock_cap/(price*(1+FEE)))
                stop=price*.94
            else:
                # 1:2:6 cash committed if all 3 tranches executed:
                # stage1=1/9 cap, stage2=2/9, stage3=6/9.
                qty=int(stock_cap/9/(price*(1+FEE)))
                stop=0
            if qty<1:continue
            cost=qty*price*(1+FEE)
            if cost>cash:continue
            cash-=cost;gross_invested+=cost
            filled_codes.add(code);years[str(day.year)]+=1;months[key]+=1
            positions[code]={"qty":qty,"units":1,"start":day,"entry":price,
                "last_price":price,"last_add":price,"n_entry":n,"n_add":n,
                "stop":stop,"cost":cost,"proceeds":0.,"age":0,
                "capacity":total_eq*MAX_STOCK_FRAC,"tranche1_qty":qty,
                "stage":1,"take":set(),"peak":price}

        # EOD bar execution using only today range and prior known signals.
        # Stops/exits are prioritized ahead of same-day additions (pessimistic).
        for code in list(positions):
            f=facts[code]
            if day not in f.index:continue
            bar=f.loc[day];p=positions[code]
            p["age"]+=1
            openprice=float(bar.open);low=float(bar.low);high=float(bar.high)
            close=float(bar.close);lastclose[code]=close;p["last_price"]=close
            if mode.startswith("T55"):
                channel=float(bar.prior20low) if pd.notna(bar.prior20low) else 0.
                stop=max(p["stop"],channel)
                if low<=stop:
                    sell(code,p["qty"],min(openprice,stop),day,"20d-channel-or-2N")
                    continue
                if mode=="T55_PYRAMID":
                    # conservative max 4 units, each 0.5N beyond last fill;
                    # same-day stop-vs-add unresolved -> stop checked above.
                    for _ in range(3):
                        if code not in positions or positions[code]["units"]>=4:break
                        p=positions[code]
                        nextentry=p["last_add"]+.5*p["n_add"]
                        if high<nextentry:break
                        add_px=max(openprice,nextentry)
                        total_eq=equity()
                        space=max(0,p["capacity"]-p["cost"])
                        qty=min(int(total_eq*.01/(2*p["n_add"])),
                                int(space/(add_px*(1+FEE))))
                        if qty<=0:break
                        if add(code,add_px,qty,day):
                            p["stop"]=max(p["stop"],add_px-2*p["n_add"])
                        else:break
            elif mode=="B0":
                if low<=p["stop"]:
                    sell(code,p["qty"],min(openprice,p["stop"]),day,"fixed6")
                    continue
                for target in (.10,.15,.20):
                    if code not in positions:break
                    p=positions[code]
                    if target in p["take"] or high<p["entry"]*(1+target):continue
                    q=p["qty"] if target==.20 else max(1,int((p["cost"]/(p["entry"]*(1+FEE)))/3))
                    sell(code,q,p["entry"]*(1+target),day,f"take{target}")
                    if code in positions:positions[code]["take"].add(target)
                if code in positions and p["age"]>=60:
                    sell(code,p["qty"],close,day,"max60")
            else:
                if mode=="F_RSI_STOP" and low<=p["entry"]*.85:
                    sell(code,p["qty"],min(openprice,p["entry"]*.85),day,"emergency-stop15")
                    continue
                avg_cost=p["cost"]/(p["qty"]+sum([])) if False else None
                # Avg cost includes fees for all lots less all prior proceeds;
                # here no partial sells, use total invested cost / current shares.
                per_share_cost=p["cost"]/p["qty"]
                target=per_share_cost*1.03
                if high>=target:
                    sell(code,p["qty"],max(openprice,target),day,"plus3")
                    continue
                if float(bar.rsi14)>=70 and p["age"]>=1:
                    sell(code,p["qty"],close,day,"rsi70")
                    continue
                # No stop in Fujimoto base proxy; -15% emergency in F_RSI_STOP.
                # Buy only after checking profit/stop: do not invent same-bar
                # favorable entry and profit exits.
                stage=p["stage"]
                thresholds={1:p["entry"]*.95,2:p["entry"]*.90}
                if stage<3 and low<=thresholds[stage]:
                    add_px=min(openprice,thresholds[stage])
                    target_budget=p["capacity"]*({1:2,2:6}[stage])/9
                    qty=int(target_budget/(add_px*(1+FEE)))
                    if qty>=1 and add(code,add_px,qty,day):
                        p["stage"]=stage+1
                if code in positions and p["age"]>=180:
                    sell(code,p["qty"],close,day,"max180")
        navval=equity()
        avg_exposure_sum+=sum(p["qty"]*lastclose.get(code,p["last_price"]) for code,p in positions.items())/max(navval,.01)
        nav.append((day,navval))

    day=sessions[-1]
    for code in list(positions):
        sell(code,positions[code]["qty"],lastclose.get(code,positions[code]["last_price"]),day,"end")
    values=np.array([v for d,v in nav],dtype=float)
    draw=values/np.maximum.accumulate(values)-1
    wins=[v["pnl"] for v in records if v["pnl"]>0]
    losses=[-v["pnl"] for v in records if v["pnl"]<0]
    years_count=max((sessions[-1]-sessions[0]).days/365.25,.01)
    final=cash
    yearly={};ye={}
    for day,asset in nav:ye[str(day.year)]=asset
    last=INIT
    for year,asset in sorted(ye.items()):
        yearly[year]=round(100*(asset/last-1),2);last=asset
    return {"period":[str(sessions[0].date()),str(sessions[-1].date())],
      "return_pct":round(100*(final/INIT-1),3),
      "cagr_pct":round(100*((final/INIT)**(1/years_count)-1),3),
      "mdd_pct":round(100*float(draw.min()),3),
      "trades":len(records),
      "win_pct":round(100*len(wins)/len(records),2) if records else None,
      "profit_factor":round(sum(wins)/sum(losses),3) if losses else None,
      "avg_win_loss":round(float(np.mean(wins)/np.mean(losses)),3) if wins and losses else None,
      "median_trade_return_pct":round(float(np.median([x["return_pct"] for x in records])),3) if records else None,
      "avg_exposure_pct":round(100*avg_exposure_sum/len(sessions),2),
      "median_holding_days":round(float(np.median([x["days"] for x in records])),1) if records else None,
      "signal_days":signal_days,"unique_buys":len(filled_codes),
      "buy_year_counts":dict(years),"yearly_return_pct":yearly,
      "top_three_trades":sorted(records,key=lambda x:x["pnl"],reverse=True)[:3],
      "worst_three_trades":sorted(records,key=lambda x:x["pnl"])[:3]}

def main():
    t=time.monotonic()
    tickers,markets=get_list()
    hist,failed,jumps=get_history(tickers)
    result={}
    for group,data in (("all_current",hist),("exclude_gt35pct",{k:v for k,v in hist.items() if k not in jumps})):
        print("GENERATING_SIGNALS",group,len(data),flush=True)
        facts,signals,counts=prepare(data)
        outcomes={}
        for name in NAMES:
            full=replay(facts,signals,name,START,END)
            recent=replay(facts,signals,name,RECENT,END)
            outcomes[name]={"five_year":full,"recent":recent}
            print("RESULT",group,name,"return",full["return_pct"],"MDD",full["mdd_pct"],
                "trades",full["trades"],"recent",recent["return_pct"],flush=True)
        result[group]={"codes":len(data),"signal_counts":dict(counts),"models":outcomes}
    output={"meta":{
      "names":{"B0":"HY RSI 50-70 MACD trend previous reference",
          "T55":"Dennis-style 55-day close breakout next open, 20-day lowest-low exits, ATR2N stop, no pyramid",
          "T55_PYRAMID":"T55 plus as price rises 0.5N incremental buys up to 4 units; shared updated stop",
          "F_RSI30":"Shigeru technical-only RSI14 cross below30, 1:2:6 averaging at -5/-10%, +3% cost exit, RSI70 exit, max180 days",
          "F_RSI_MACD":"F_RSI30 exits/adds; entry when RSI oversold in recent 5d & rises 30..45 as MACD histogram crosses above0",
          "F_RSI_STOP":"F_RSI30 plus -15% emergency stop"},
      "markets":markets,"source":"aikstockdata.com public daily OHLCV current KOSPI/KOSDAQ, unadjusted",
      "requested_period":[str(START.date()),str(END.date())],
      "heldout_from":str(RECENT.date()),
      "downloaded":len(hist),"failed":dict(failed),"extreme_jump_codes":len(jumps),
      "equity_initial":INIT,"fee_per_side":FEE,"positions":MAX_POS,"new_buy_limit_month":MONTHLY_NEW,
      "liquidity":"20d avg daily traded value >=2bn KRW",
      "caveats":[
        "Not a replica of either trader: Turtle originally traded diversified futures, with intraday entries and shorts; tested LONG-ONLY KOSPI/KOSDAQ",
        "Fujimoto fundamentally screens companies, checks 5-minute bars and order book; none of those are available in raw daily data",
        "No survivorship-free delisted historical constituents, dividend/split adjustments, or timestamped financial statements",
        "Price jumps and sample exclusions can bias results; raw data may give spurious fills",
        "No actual order fill, lot-size rules, taxes, borrowing, capital costs or realistic gap/limit-up queue effects",
        "Results are heuristic, not statistically validated on an untouched out-of-sample period",
        "Turtle N and risk scaling from a 20d Wilder average, transaction limit and stock allocation adapted to cash account",
        "Fujimoto 1:2:6 stage thresholds -5/-10 and profit target 3% are assumptions from published interviews, not a rigid disclosed system"
      ]},"results":result,"runtime_seconds":round(time.monotonic()-t,1)}
    p=Path("experiments/hy_dennis_fujimoto_results.json")
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    compact={g:{"codes":data["codes"],"signals":data["signal_counts"],
      "models":{name:{period:{k:run[k] for k in (
        "return_pct","cagr_pct","mdd_pct","trades","win_pct","profit_factor",
        "avg_win_loss","median_trade_return_pct","avg_exposure_pct","median_holding_days",
        "signal_days","unique_buys","yearly_return_pct")} for period,run in vals.items()}
      for name,vals in data["models"].items()}} for g,data in result.items()}
    print("FINAL_COMPARISON_JSON",json.dumps({"elapsed":output["runtime_seconds"],
        "history_count":len(hist),"excluded":len(jumps),"markets":markets,
        "data":compact},ensure_ascii=False),flush=True)

if __name__=="__main__":main()
