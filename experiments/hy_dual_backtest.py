"""HY 1안 연구용 EOD 프록시 백테스트. 장중 실제 5-of-7 신호와는 다름.
자료: aikstockdata.com, 2026-11-17까지의 일봉 (비수정주가).
현재 기준의 제한된 32종목 목록을 사용하므로 생존편향이 있음.
"""
from __future__ import annotations
import concurrent.futures as futures
import json
from pathlib import Path
import math
import requests
import numpy as np
import pandas as pd

CODES = [
("005930","삼성전자"),("000660","SK하이닉스"),("035420","NAVER"),
("035720","카카오"),("005380","현대차"),("000270","기아"),
("207940","삼성바이오로직스"),("068270","셀트리온"),("373220","LG에너지솔루션"),
("006400","삼성SDI"),("005490","POSCO홀딩스"),("051910","LG화학"),
("012450","한화에어로스페이스"),("042660","한화오션"),("009540","HD한국조선해양"),
("034020","두산에너빌리티"),("105560","KB금융"),("055550","신한지주"),
("086790","하나금융지주"),("316140","우리금융지주"),("028260","삼성물산"),
("066570","LG전자"),("003670","포스코퓨처엠"),("323410","카카오뱅크"),
("247540","에코프로비엠"),("086520","에코프로"),("196170","알테오젠"),
("028300","HLB"),("058470","리노공업"),("403870","HPSP"),
("214150","클래시스"),("039030","이오테크닉스"),
]
START="2021-11-17"
END="2026-11-17"
DATA_SOURCE="https://aikstockdata.com/data/public/s/"
INIT=10000000.
FEE=0.0015
STOP=0.06
MAX_POS=2
MAX_MONTHLY=2
MAX_HOLD=60

def fetch(item):
    code,name=item
    url=f"{DATA_SOURCE}{code}_history.json"
    try:
        res=requests.get(url,headers={"User-Agent":"HY research backtest 1.0"},timeout=25)
        res.raise_for_status()
        raw=res.json()
        f=pd.DataFrame(raw["rows"],columns=raw["columns"])
        f.columns=[str(x).lower() for x in f.columns]
        for k in ("open","high","low","close","volume"):
            f[k]=pd.to_numeric(f[k],errors="coerce")
        f["date"]=pd.to_datetime(f["date"],format="%Y%m%d",errors="coerce")
        f=f.dropna(subset=["date","open","high","low","close","volume"])
        f=f[(f["open"]>0)&(f["high"]>0)&(f["low"]>0)&(f["close"]>0)&(f["volume"]>0)]
        f=f.drop_duplicates("date").sort_values("date").set_index("date")
        if len(f)<500: raise ValueError(f"insufficient bars {len(f)}")
        f["ma20"]=f.close.rolling(20).mean()
        f["ma60"]=f.close.rolling(60).mean()
        f["ma200"]=f.close.rolling(200).mean()
        change=f.close.diff()
        gains=change.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
        losses=(-change.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
        f["rsi"]=100-100/(1+gains/losses.replace(0,np.nan))
        f["macd"]=f.close.ewm(span=12,adjust=False).mean()-f.close.ewm(span=26,adjust=False).mean()
        f["macd_signal"]=f.macd.ewm(span=9,adjust=False).mean()
        f["hist"]=f.macd-f.macd_signal
        return code,name,f,None
    except Exception as exc: return code,name,None,str(exc)

def build_signals(code,frame):
    out={}
    idx=frame.index
    v=frame
    for i in range(205,len(v)-1):
        r=v.iloc[i]; old=v.iloc[i-1]
        day=str(idx[i].date())
        if day<START or day>END:continue
        price=float(r.close); m20=float(r.ma20);m60=float(r.ma60)
        if not all(math.isfinite(x) and x>0 for x in (price,m20,m60,float(r.ma200))):continue
        prior_high=float(v.close.iloc[i-20:i].max())
        prev=float(old.close)
        volbase=float(v.volume.iloc[i-20:i].mean())
        volratio=float(v.volume.iloc[i-2:i+1].max()/volbase) if volbase>0 else 0
        gap20=100*(price/m20-1)
        ret10=100*(price/float(v.close.iloc[i-10])-1)
        ret20=100*(price/float(v.close.iloc[i-20])-1)
        rising20=m20>float(v.ma20.iloc[i-5])
        recentcross=any(
            float(v.ma20.iloc[j])>float(v.ma60.iloc[j])
            and float(v.ma20.iloc[j-1])<=float(v.ma60.iloc[j-1])
            for j in range(i-9,i+1))
        breakout=price>=prior_high and prev<prior_high
        score=(20 if price>m20 else 0)+(15 if rising20 else 0)+(15 if price>m60 else 0)
        score+=15 if recentcross else 0
        score+=20 if breakout else (8 if price>=prior_high*.98 else 0)
        score+=15 if volratio>=1.5 else (8 if volratio>=1.1 else 0)
        if gap20>12:score-=20
        if ret10>25:score-=15
        score=max(0,min(100,score))
        late=gap20>15 or ret20>35 or ret10>25
        green=not late and score>=75 and gap20<=8
        buy1=max(m20,prior_high*.99)
        if green:buy1=min(price,max(m20,prior_high*.995))
        stop=min(price*.97,max(m20*.96,float(v.close.iloc[i-9:i+1].min())*.98))
        stage=green and price>stop and buy1*.98<=price<=buy1*1.02
        gap=100*(price/buy1-1) if buy1 else 999
        risk=100*(price-stop)/price
        mand=(-1<=gap<=3 and volratio>=1.5 and price>=round(prior_high)>0 and 0<=risk<=7)
        # EOD 프록시: 장중 10~60초 지속성 검증 불가. stage/score만 보조 신호로 인정.
        A=mand and (stage or score>=85) and not late
        rsi=float(r.rsi) if pd.notna(r.rsi) else -1
        B=price>float(r.ma200) and m20>m60 and 50<=rsi<=70 and rsi>float(old.rsi) and float(r.macd)>float(r.macd_signal) and float(r.hist)>float(old.hist)
        out[day]={"A":bool(A),"B":bool(B),"both":bool(A and B),
                  "rank":float(score)+(volratio*2 if volratio<5 else 10)}
    return out

def test_invariants(signals):
    n=0
    for code,s in signals.items():
        for dt,d in s.items():
            assert not d["both"] or (d["A"] and d["B"]), (code,dt)
            n+=1
    assert n>100
    return n

def simulate(hist,signals,strategy,lo=START,hi=END):
    days=sorted({str(d.date()) for f in hist.values() for d in f.index
                 if lo<=str(d.date())<=hi})
    if not days:return {"error":"no days"}
    bars={c:{str(i.date()):r for i,r in f.iterrows()} for c,f in hist.items()}
    cash=INIT;holdings={};daily=[];trades=[];last_closes={}
    monthcount={}
    selected_days=0;selected_stocks=set()
    def sell(code,price,q):
        nonlocal cash
        p=holdings[code]
        qty=min(int(q),p["qty"])
        if qty<=0:return
        proceeds=qty*price*(1-FEE)
        cash+=proceeds
        p["proceeds"]+=proceeds
        p["qty"]-=qty
        if p["qty"]==0:
            trades.append({"code":code,"entry":p["entryday"],"exit":day,
                           "pnl":p["proceeds"]-p["cost"]})
            del holdings[code]
    for n,day in enumerate(days):
        yesterday=days[n-1] if n else ""
        today_bars={c:b[day] for c,b in bars.items() if day in b}
        for c,r in today_bars.items():last_closes[c]=float(r.close)
        # 전날 종가 신호만 사용 -> 다음 거래일 시가 진입
        if yesterday:
            cand=sorted([(c,s[yesterday]["rank"]) for c,s in signals.items()
                        if yesterday in s and s[yesterday][strategy] and c in today_bars],
                        key=lambda z:-z[1])
            if cand:selected_days+=1;selected_stocks.update(c for c,_ in cand)
            key=day[:7]
            for c,_ in cand:
                if c in holdings or len(holdings)>=MAX_POS or monthcount.get(key,0)>=MAX_MONTHLY:continue
                entry=float(today_bars[c].open)
                budget=min(cash,INIT/MAX_POS)
                qty=int(budget/(entry*(1+FEE)))
                if qty<1:continue
                cost=qty*entry*(1+FEE)
                cash-=cost
                holdings[c]={"qty":qty,"orig":qty,"entry":entry,"entryday":day,
                             "cost":cost,"proceeds":0.,"age":0,"done":set()}
                monthcount[key]=monthcount.get(key,0)+1
        for c in list(holdings):
            if c not in today_bars:continue
            r=today_bars[c];p=holdings[c]
            p["age"]+=1
            # 갭하락과 같은 날 손절/익절이 모두 있는 경우 손절 먼저 적용 (보수적).
            stop=p["entry"]*(1-STOP)
            if float(r.low)<=stop:
                exitprice=min(float(r.open),stop)
                sell(c,exitprice,p["qty"])
                continue
            for target in (.10,.15,.20):
                if c not in holdings:break
                p=holdings[c]
                if target in p["done"] or float(r.high)<p["entry"]*(1+target):continue
                q=p["qty"] if target==.20 else max(1,int(p["orig"]/3))
                sell(c,p["entry"]*(1+target),q)
                if c in holdings:holdings[c]["done"].add(target)
            if c in holdings and holdings[c]["age"]>=MAX_HOLD:
                sell(c,float(r.close),holdings[c]["qty"])
        eq=cash+sum(p["qty"]*last_closes.get(c,p["entry"]) for c,p in holdings.items())
        daily.append((day,eq))
    day=days[-1]
    for c in list(holdings):sell(c,last_closes.get(c,holdings[c]["entry"]),holdings[c]["qty"])
    equity=[x[1] for x in daily]
    peaks=np.maximum.accumulate(equity)
    mdd=float(np.min(np.array(equity)/peaks-1))
    yrs=(pd.Timestamp(days[-1])-pd.Timestamp(days[0])).days/365.25
    profits=[t["pnl"] for t in trades if t["pnl"]>0]
    losses=[-t["pnl"] for t in trades if t["pnl"]<0]
    ratio=float(np.mean(profits)/np.mean(losses)) if profits and losses else None
    return {"period":[days[0],days[-1]],"signals_days":selected_days,"signal_stocks":len(selected_stocks),
            "trades":len(trades),"returns_pct":round((cash/INIT-1)*100,3),
            "cagr_pct":round(((cash/INIT)**(1/max(yrs,.01))-1)*100,3),
            "mdd_pct":round(mdd*100,3),
            "win_pct":round(100*len(profits)/len(trades),2) if trades else None,
            "avg_win_loss":round(ratio,3) if ratio is not None else None,
            "ending_cash":round(cash,2)}

def main():
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        retrieved=list(pool.map(fetch,CODES))
    errors={code:err for code,name,df,err in retrieved if err}
    hist={code:df for code,name,df,err in retrieved if df is not None}
    print("loaded",len(hist),"failed",len(errors),"failure_samples",list(errors.items())[:4],flush=True)
    if len(hist)<8: raise RuntimeError("Not enough real OHLCV histories; no performance estimate")
    signals={c:build_signals(c,df) for c,df in hist.items()}
    tested=test_invariants(signals)
    count={label:sum(v[label] for s in signals.values() for v in s.values()) for label in ("A","B","both")}
    results={}
    for period,lo,hi in (("full",START,END),("out_of_sample","2025-07-01",END)):
        results[period]={key:simulate(hist,signals,key,lo,hi) for key in ("A","B","both")}
    output={"method":"EOD proxy, not exact intraday HY; original A mandatory 4 + stage/score, no 10-60s persistence",
       "data_source":DATA_SOURCE,"dates":[START,END],
       "biases":["32 currently listed names only: survivorship bias","raw unadjusted OHLCV","no intraday persistence","fill assumptions","fees 0.15 percent each way"],
       "historical_series_count":len(hist),"failed_series":errors,"signal_invariant_rows":tested,
       "signal_counts":count,"results":results}
    p=Path("experiments/hy_dual_backtest_result.json");p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(output,ensure_ascii=False,indent=2),flush=True)

if __name__=="__main__":main()
