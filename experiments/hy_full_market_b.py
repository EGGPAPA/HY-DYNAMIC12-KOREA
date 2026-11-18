"""HY B-model full CURRENT-universe exploratory daily backtest.
NOT a 5-year historical all-listed test: vendor excludes delisted, unadjusted prices.
Data: aikstockdata.com (FSC open-data source); noncommercial summary research only.
Read-only experiment. Never sends orders, watches, or modifies production.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict, Counter
from pathlib import Path
import csv, io, json, logging, math, os, time
import requests
import numpy as np
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from threading import local

BASE="https://aikstockdata.com/data/public/"
START=pd.Timestamp("2021-11-17")
END=pd.Timestamp("2026-11-17")
HOLDOUT=pd.Timestamp("2025-07-01")
INIT=10_000_000.
SIDE_FEE=.0015   # round-trip 0.3%
STOP=.06
MAX_POSITIONS=2
NEW_BUYS_PER_MONTH=2
MAX_HOLD_TRADING_DAYS=60
MIN_VALUE_20=2_000_000_000  # reported as an optional liquidity screen
WORKERS=24
_tls=local()

def session():
    s=getattr(_tls,"s",None)
    if s is None:
        s=requests.Session()
        # vendor may not implement Retry-After: bounded small backoff
        retry=Retry(total=2,backoff_factor=.4,status_forcelist=[429,500,502,503,504])
        s.mount("https://",HTTPAdapter(max_retries=retry,pool_maxsize=2))
        s.headers.update({"User-Agent":"HY-B-marketwide-backtest-noncommercial/1.0"})
        _tls.s=s
    return s

def get(url,timeout=22):
    res=session().get(url,timeout=timeout)
    res.raise_for_status()
    return res

def load_universe():
    data=get(BASE+"quotes.csv").content
    f=pd.read_csv(io.BytesIO(data),dtype={"종목코드":"string"},low_memory=False)
    print("universe_fields",list(f.columns),flush=True)
    codecol=next((x for x in ("종목코드","srtnCd","code") if x in f.columns),None)
    marketcol=next((x for x in ("시장","시장구분","mrktCtg","market") if x in f.columns),None)
    namecol=next((x for x in ("종목명","itmsNm","name") if x in f.columns),None)
    if not codecol or not marketcol:
        raise RuntimeError("Unknown quote schema: cannot safely filter exchanges")
    selected=f.loc[f[marketcol].astype(str).str.upper().str.contains("KOSPI|KOSDAQ|코스피|코스닥",regex=True)].copy()
    selected["code"]=selected[codecol].astype(str).str.extract(r"([0-9]{6})",expand=False)
    selected=selected[selected.code.notna()].drop_duplicates("code")
    markets=selected[marketcol].astype(str).value_counts().to_dict()
    names=dict(zip(selected.code,selected[namecol].astype(str))) if namecol else {}
    print("universe_rows",len(f),"kospi_kosdaq",len(selected),"markets",markets,flush=True)
    if len(selected)<1500:raise RuntimeError("Universe unexpectedly small: fail closed")
    return selected.code.tolist(),names,markets

def download_history(code):
    try:
        raw=get(BASE+"s/"+code+"_history.json").json()
        if not isinstance(raw,dict) or "rows" not in raw or "columns" not in raw:
            raise ValueError("history schema")
        f=pd.DataFrame(raw["rows"],columns=[str(c).lower() for c in raw["columns"]])
        need={"date","open","high","low","close","volume"}
        if not need.issubset(f.columns):raise ValueError("missing OHLCV columns")
        f=f[list(need)].copy()
        f["date"]=pd.to_datetime(f.date.astype(str),format="%Y%m%d",errors="coerce")
        for k in ("open","high","low","close","volume"):f[k]=pd.to_numeric(f[k],errors="coerce")
        f=f.dropna(subset=["date","open","high","low","close","volume"])
        f=f[(f.open>0)&(f.high>0)&(f.low>0)&(f.close>0)&(f.volume>0)]
        f=f.drop_duplicates("date").sort_values("date").set_index("date")
        f=f.loc[f.index<=END]
        if len(f)<220:return code,None,"short_history"
        if f.index.max()<HOLDOUT:return code,None,"stale_history"
        return code,f,None
    except Exception as exc:
        return code,None,str(exc)[:160]

def compute_signals(code,f,liq):
    c=f.close
    ma20=c.rolling(20,min_periods=20).mean()
    ma60=c.rolling(60,min_periods=60).mean()
    ma200=c.rolling(200,min_periods=200).mean()
    delta=c.diff()
    gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    # RSI extreme no losses = 100, flat=50
    rs=gain.div(loss.replace(0,np.nan))
    rsi=(100-100/(1+rs)).where(loss.ne(0),100)
    rsi=rsi.where(~((gain==0)&(loss==0)),50)
    macd=c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean()
    signal=macd.ewm(span=9,adjust=False).mean()
    hist=macd-signal
    avg_value=(c*f.volume).rolling(20,min_periods=20).mean()
    basic=(c>ma200)&(ma20>ma60)&rsi.between(50,70)&(rsi>rsi.shift(1))&(
        macd>signal)&(hist>hist.shift(1))
    basic &= (f.index>=START)&(f.index<=END)
    if liq:basic &= avg_value >= MIN_VALUE_20
    # independent B-only ranking: momentum + liquidity, NO 5-of-7 score
    ret20=c.pct_change(20)
    ret60=c.pct_change(60)
    score=(ret20.clip(-.5,1)*100*.6+ret60.clip(-.5,2)*100*.25+
           (rsi-50)*.15)
    dates=f.index[basic.fillna(False)]
    return [(d,code,float(score.loc[d])) for d in dates]

def audit_jumps(f):
    ret=f.close.pct_change()
    return [str(d.date()) for d in ret.index[(ret.abs()>.35)&(ret.index>=START)&(ret.index<=END)]]

def backtest(hist,signals,start_date,stop_date):
    active={code:f for code,f in hist.items() if f.index.max()>=start_date}
    dates=sorted({d for f in active.values() for d in f.index
                  if start_date<=d<=stop_date})
    if not dates:return {"error":"no_dates"}
    day_candidates=defaultdict(list)
    for code,items in signals.items():
        if code not in active:continue
        for d,c,score in items:
            if start_date<=d<stop_date and math.isfinite(score):
                day_candidates[d].append((c,score))
    for d,v in day_candidates.items():v.sort(key=lambda x:(-x[1],x[0]))
    cash=INIT;holdings={};realized=[];daily=[];last_closes={}
    month_buys=Counter();signal_stocks=set();trigger_days=0;year_entries=Counter()
    def exit_quantity(code,price,qty,day):
        nonlocal cash
        p=holdings[code];qty=min(qty,p["qty"])
        if qty<=0:return
        proceeds=qty*price*(1-SIDE_FEE)
        cash+=proceeds;p["realized"]+=proceeds;p["qty"]-=qty
        if p["qty"]==0:
            realized.append({"code":code,"entry":str(p["day"].date()),"exit":str(day.date()),
                "pnl":round(p["realized"]-p["original_cost"],1),
                "return_pct":round(100*(p["realized"]/p["original_cost"]-1),3)})
            del holdings[code]
    for n,day in enumerate(dates):
        previous=dates[n-1] if n else None
        for code,p in list(holdings.items()):
            f=active[code]
            if day in f.index:
                row=f.loc[day]
                last_closes[code]=float(row.close)
        # Signal day t only, buy on next global trading day t+1 open.
        if previous in day_candidates:
            candidates=day_candidates[previous]
            trigger_days+=1
            signal_stocks.update(c for c,_ in candidates)
            key=day.strftime("%Y-%m")
            for code,score in candidates:
                if code in holdings or len(holdings)>=MAX_POSITIONS or month_buys[key]>=NEW_BUYS_PER_MONTH:continue
                f=active[code]
                if day not in f.index:continue
                row=f.loc[day]
                price=float(row.open)
                amount=min(cash,INIT/MAX_POSITIONS)
                qty=int(amount/(price*(1+SIDE_FEE)))
                if qty<1:continue
                cost=price*qty*(1+SIDE_FEE)
                cash-=cost
                holdings[code]={"qty":qty,"orig":qty,"entry":price,"day":day,
                    "original_cost":cost,"realized":0.,"age":0,"done":set()}
                month_buys[key]+=1
                year_entries[str(day.year)]+=1
        # same-day stop is evaluated before profit for conservative OHLC collision
        for code in list(holdings):
            f=active[code]
            if day not in f.index:continue
            row=f.loc[day];p=holdings[code];p["age"]+=1
            last_closes[code]=float(row.close)
            stop=p["entry"]*(1-STOP)
            if float(row.low)<=stop:
                exit_quantity(code,min(float(row.open),stop),p["qty"],day)
                continue
            for target in (.10,.15,.20):
                if code not in holdings:break
                p=holdings[code]
                if target in p["done"] or float(row.high)<p["entry"]*(1+target):continue
                qty=p["qty"] if target==.20 else max(1,int(p["orig"]/3))
                exit_quantity(code,p["entry"]*(1+target),qty,day)
                if code in holdings:holdings[code]["done"].add(target)
            if code in holdings and holdings[code]["age"]>=MAX_HOLD_TRADING_DAYS:
                exit_quantity(code,float(row.close),holdings[code]["qty"],day)
        eq=cash+sum(p["qty"]*last_closes.get(c,p["entry"]) for c,p in holdings.items())
        daily.append(eq)
    lastday=dates[-1]
    for code in list(holdings):
        exit_quantity(code,last_closes.get(code,holdings[code]["entry"]),holdings[code]["qty"],lastday)
    peaks=np.maximum.accumulate(np.array(daily))
    drawdowns=np.array(daily)/peaks-1
    yrs=max(.01,(dates[-1]-dates[0]).days/365.25)
    wins=[t["pnl"] for t in realized if t["pnl"]>0]
    losses=[-t["pnl"] for t in realized if t["pnl"]<0]
    return {
      "period":[str(dates[0].date()),str(dates[-1].date())],
      "total_return_pct":round(100*(cash/INIT-1),3),
      "cagr_pct":round(100*((cash/INIT)**(1/yrs)-1),3),
      "mdd_pct":round(100*float(drawdowns.min()),3),
      "trades":len(realized),
      "win_rate_pct":round(100*len(wins)/len(realized),2) if realized else None,
      "avg_win_loss":round(float(np.mean(wins)/np.mean(losses)),3) if wins and losses else None,
      "profit_factor":round(sum(wins)/sum(losses),3) if losses else None,
      "cash_final_krw":round(cash),
      "buy_year_counts":dict(year_entries),
      "signal_days":trigger_days,"signaled_tickers":len(signal_stocks),
      "top_winners":sorted(realized,key=lambda x:x["pnl"],reverse=True)[:5],
      "top_losers":sorted(realized,key=lambda x:x["pnl"])[:5]
    }

def main():
    t=time.monotonic()
    codes,names,markets=load_universe()
    history={};failures=Counter();jumped={}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        pending={pool.submit(download_history,c):c for c in codes}
        for n,fu in enumerate(as_completed(pending),1):
            code,df,error=fu.result()
            if df is None:failures[error]+=1
            else:
                history[code]=df
                jumps=audit_jumps(df)
                if jumps:jumped[code]=jumps
            if n%400==0 or n==len(codes):
                print("download_progress",n,len(codes),"good",len(history),
                      "failed",sum(failures.values()),"seconds",round(time.monotonic()-t),flush=True)
    if len(history)<.85*len(codes):
        raise RuntimeError(f"Fail closed: usable histories {len(history)} / universe {len(codes)}")
    print("history_downloaded",len(history),"raw_jump_codes",len(jumped),flush=True)
    signals_all={};signals_liq={}
    for code,df in history.items():
        signals_all[code]=compute_signals(code,df,False)
        signals_liq[code]=compute_signals(code,df,True)
    print("signal_rows",sum(map(len,signals_all.values())),"liquid_signals",
          sum(map(len,signals_liq.values())),flush=True)
    clean={c:df for c,df in history.items() if c not in jumped}
    # no min value + liquidity-checked, raw + extreme jump sensitivity
    results={}
    for title,h,s in (("all_accessible_raw",history,signals_all),
                      ("all_accessible_liquid",history,signals_liq),
                      ("no_discontinuous_raw",clean,signals_all),
                      ("no_discontinuous_liquid",clean,signals_liq)):
        results[title]={"five_year":backtest(h,s,START,END),
                        "validation_2025_07":backtest(h,s,HOLDOUT,END)}
        print("finished_variant",title,results[title]["five_year"]["total_return_pct"],
              results[title]["five_year"]["trades"],flush=True)
    output={
      "status":"exploratory_CURRENT_survivor_universe_not_historical_full_market",
      "source":"aikstockdata.com: FSC public market daily (unadjusted, current constituents only)",
      "requested_period":[str(START.date()),str(END.date())],
      "universe_codes":len(codes),"market_counts":markets,"usable_histories":len(history),
      "failed_histories":sum(failures.values()),"failure_categories":dict(failures.most_common(25)),
      "discontinuous_codes_over_35pct":len(jumped),
      "discontinuous_code_samples":{code:ds for code,ds in list(sorted(jumped.items()))[:12]},
      "clean_histories":len(clean),
      "signals_unfiltered":sum(map(len,signals_all.values())),
      "signals_liquidity":sum(map(len,signals_liq.values())),
      "independent_B_rules":{
          "trend":"close > SMA200 and SMA20 > SMA60",
          "RSI14":"50 <= RSI <=70 and RSI increasing",
          "MACD":"MACD > signal and histogram increasing",
          "execution":"next global trading date open, trade only when bar exists",
          "ranking":"B-only weighted 20d and 60d returns and RSI; no 5-of-7 score",
          "exit":"6% stop; 10%,15%,20% staged profit; 60 holding trading sessions",
          "cost":"0.15% one way, 0.3% round trip; no separate slippage beyond modeled fee",
          "capacity":"2 positions and 2 new buys per month",
          "liquidity_variant":"20d average close*volume >= 2 billion KRW"
      },
      "known_limits":[
        "Survivorship bias: delisted historical stocks not supplied",
        "OHLCV raw not corporate-action adjusted; >35% day jumps excluded only in sensitivity variant",
        "Possible preferred shares/SPACs/other quoted securities depending on vendor quote universe",
        "No intraday tick, no true market order fills, or live execution",
        "No IPO allocation and realistic capacity/slippage beyond 0.15% each direction",
        "OHLCV ambiguous stop/profit collision conservatively stops first",
        "Ranking/strategy not yet independently validated point-in-time",
        "Not the same as original 5-of-7 strategy"
      ],"results":results,
      "elapsed_seconds":round(time.monotonic()-t,1)
    }
    dest=Path("experiments/full_market_b_result.json")
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print("FINAL_RESULT_JSON",json.dumps(output,ensure_ascii=False),flush=True)

if __name__=="__main__": main()
