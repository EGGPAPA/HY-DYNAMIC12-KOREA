from datetime import date, timedelta
import math

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

UNIVERSE = [
    ("005930", "삼성전자", "KOSPI"), ("000660", "SK하이닉스", "KOSPI"),
    ("035420", "NAVER", "KOSPI"), ("035720", "카카오", "KOSPI"),
    ("005380", "현대차", "KOSPI"), ("000270", "기아", "KOSPI"),
    ("207940", "삼성바이오로직스", "KOSPI"), ("068270", "셀트리온", "KOSPI"),
    ("373220", "LG에너지솔루션", "KOSPI"), ("006400", "삼성SDI", "KOSPI"),
    ("005490", "POSCO홀딩스", "KOSPI"), ("051910", "LG화학", "KOSPI"),
    ("012450", "한화에어로스페이스", "KOSPI"), ("042660", "한화오션", "KOSPI"),
    ("009540", "HD한국조선해양", "KOSPI"), ("034020", "두산에너빌리티", "KOSPI"),
    ("105560", "KB금융", "KOSPI"), ("055550", "신한지주", "KOSPI"),
    ("086790", "하나금융지주", "KOSPI"), ("316140", "우리금융지주", "KOSPI"),
    ("028260", "삼성물산", "KOSPI"), ("066570", "LG전자", "KOSPI"),
    ("003670", "포스코퓨처엠", "KOSPI"), ("323410", "카카오뱅크", "KOSPI"),
    ("247540", "에코프로비엠", "KOSDAQ"), ("086520", "에코프로", "KOSDAQ"),
    ("196170", "알테오젠", "KOSDAQ"), ("028300", "HLB", "KOSDAQ"),
    ("058470", "리노공업", "KOSDAQ"), ("403870", "HPSP", "KOSDAQ"),
    ("214150", "클래시스", "KOSDAQ"), ("039030", "이오테크닉스", "KOSDAQ"),
]
NAME_MAP={c:n for c,n,_ in UNIVERSE}; MARKET_MAP={c:m for c,_,m in UNIVERSE}
def _symbol(code,market): return f"{code}.KS" if market=="KOSPI" else f"{code}.KQ"
@st.cache_data(ttl=3600,show_spinner=False)
def _download(start_date,end_date):
    return yf.download([_symbol(c,m) for c,_,m in UNIVERSE],start=str(start_date),end=str(end_date+timedelta(days=1)),auto_adjust=True,progress=False,group_by="ticker",threads=True)
def _one(data,symbol):
    try:
        if isinstance(data.columns,pd.MultiIndex):
            if symbol in data.columns.get_level_values(0): return data[symbol].dropna(how="all")
            if symbol in data.columns.get_level_values(1): return data.xs(symbol,axis=1,level=1).dropna(how="all")
        return data.dropna(how="all")
    except Exception:return pd.DataFrame()
def _rank_on_date(histories,dt):
    rows=[]
    for code,name,market in UNIVERSE:
        h=histories.get(code)
        if h is None or h.empty:continue
        past=h.loc[h.index<=dt]
        if len(past)<61:continue
        close=pd.to_numeric(past["Close"],errors="coerce").dropna();vol=pd.to_numeric(past["Volume"],errors="coerce").dropna()
        if len(close)<61 or len(vol)<20:continue
        p=float(close.iloc[-1]);ma20=float(close.tail(20).mean());ma60=float(close.tail(60).mean())
        rows.append({"code":code,"name":name,"market":market,"r20":p/float(close.iloc[-21])-1,"r60":p/float(close.iloc[-61])-1,"value20":float((close.tail(20)*vol.tail(20)).mean()),"vol_ratio":float(vol.tail(5).mean()/max(float(vol.tail(20).mean()),1)),"trend":1.0 if p>ma20>ma60 else 0.0})
    if not rows:return pd.DataFrame()
    df=pd.DataFrame(rows)
    for c in ["r20","r60","value20","vol_ratio"]:df[c+"_pct"]=df[c].rank(pct=True)
    df["score"]=df["value20_pct"]*25+df["r20_pct"]*25+df["r60_pct"]*20+df["vol_ratio_pct"]*10+df["trend"]*20
    return df.sort_values("score",ascending=False).reset_index(drop=True)
def _bar(h,dt):
    if dt not in h.index:return None
    r=h.loc[dt];return {"open":float(r.get("Open",r["Close"])),"high":float(r.get("High",r["Close"])),"low":float(r.get("Low",r["Close"])),"close":float(r["Close"])}
def _simulate(data,start_date,end_date,monthly_buys,max_positions,hold_days,stop_pct,trail_pct,cost_pct,initial_cash,min_score):
    histories={}
    for code,_,market in UNIVERSE:
        h=_one(data,_symbol(code,market))
        if h.empty:continue
        idx=pd.to_datetime(h.index)
        if getattr(idx,"tz",None) is not None:idx=idx.tz_localize(None)
        h=h.copy();h.index=idx;histories[code]=h
    all_dates=sorted(set().union(*[set(h.index) for h in histories.values()])) if histories else []
    dates=[d for d in all_dates if pd.Timestamp(start_date)<=d<=pd.Timestamp(end_date)]
    if not dates:return pd.DataFrame(),pd.DataFrame(),pd.DataFrame(),{}
    fee_side=cost_pct/100/2;cash=float(initial_cash);positions={};pending=[];completed=[];exits=[];equity_rows=[];month_entries={};last_month=None
    def total_equity(dt):
        value=cash
        for code,p in positions.items():
            b=_bar(histories[code],dt);px=b["close"] if b else p["last_price"];value+=p["qty"]*px
        return value
    for dt in dates:
        if pending:
            month_key=(dt.year,dt.month);used=month_entries.get(month_key,0);slots=min(max_positions-len(positions),monthly_buys-used)
            held=set(positions);picks=[p for p in pending if p["code"] not in held][:max(0,slots)]
            if picks:
                equity_before=total_equity(dt);target_per_position=equity_before/max_positions
                for pick in picks:
                    b=_bar(histories.get(pick["code"],pd.DataFrame()),dt)
                    if not b or b["open"]<=0:continue
                    px=b["open"];budget=min(cash,target_per_position);qty=math.floor(budget/(px*(1+fee_side)))
                    if qty<=0:continue
                    buy=qty*px;fee=buy*fee_side;cash-=buy+fee
                    positions[pick["code"]]={"code":pick["code"],"name":pick["name"],"score":pick["score"],"entry_date":dt,"entry":px,"initial_qty":qty,"qty":qty,"peak":px,"days":0,"sold15":False,"sold20":False,"sold25":False,"realized_cash":0.0,"fees":fee,"last_price":px,"exit_notes":[]}
                    month_entries[month_key]=month_entries.get(month_key,0)+1
            pending=[]
        to_close=[]
        for code,p in list(positions.items()):
            b=_bar(histories[code],dt)
            if not b:continue
            p["last_price"]=b["close"];p["days"]+=1;p["peak"]=max(p["peak"],b["high"])
            hard=p["entry"]*(1-stop_pct/100);trail=p["peak"]*(1-trail_pct/100);down=max(hard,trail if p["peak"]>p["entry"] else hard)
            if b["low"]<=down:
                reason=f"고점대비 -{trail_pct:g}% 전량매도" if trail>=hard and p["peak"]>p["entry"] else f"-{stop_pct:g}% 손절";qty=p["qty"];proceeds=qty*down;fee=proceeds*fee_side;cash+=proceeds-fee;p["fees"]+=fee;p["realized_cash"]+=proceeds-fee;p["exit_notes"].append(reason);exits.append({"날짜":dt.date(),"종목명":p["name"],"종목코드":code,"구분":reason,"수량":qty,"가격":round(down)});p["qty"]=0;to_close.append((code,dt));continue
            for gain,frac,reason,flag in [(0.15,.30,"1차 익절 +15%","sold15"),(0.20,.30,"2차 익절 +20%","sold20"),(0.25,1.,"3차 익절 +25%","sold25")]:
                if p[flag] or p["qty"]<=0:continue
                target=p["entry"]*(1+gain)
                if b["high"]>=target:
                    sell_qty=min(math.floor(p["initial_qty"]*frac),p["qty"]) if gain<.25 else p["qty"]
                    if sell_qty<=0:p[flag]=True;continue
                    proceeds=sell_qty*target;fee=proceeds*fee_side;cash+=proceeds-fee;p["fees"]+=fee;p["realized_cash"]+=proceeds-fee;p["qty"]-=sell_qty;p[flag]=True;p["exit_notes"].append(reason);exits.append({"날짜":dt.date(),"종목명":p["name"],"종목코드":code,"구분":reason,"수량":sell_qty,"가격":round(target)})
                    if p["qty"]<=0:to_close.append((code,dt));break
            if p["qty"]>0 and p["days"]>=hold_days:
                qty=p["qty"];proceeds=qty*b["close"];fee=proceeds*fee_side;cash+=proceeds-fee;p["fees"]+=fee;p["realized_cash"]+=proceeds-fee;p["exit_notes"].append(f"{hold_days}일 기간청산");exits.append({"날짜":dt.date(),"종목명":p["name"],"종목코드":code,"구분":f"{hold_days}일 기간청산","수량":qty,"가격":round(b["close"])});p["qty"]=0;to_close.append((code,dt))
        for code,exit_date in to_close:
            if code not in positions:continue
            p=positions.pop(code);invested=p["initial_qty"]*p["entry"]*(1+fee_side);pnl=p["realized_cash"]-invested
            completed.append({"매수일":p["entry_date"].date(),"최종매도일":exit_date.date(),"종목코드":code,"종목명":p["name"],"점수":round(float(p["score"]),1),"매수가":round(p["entry"]),"초기수량":p["initial_qty"],"실현손익(원)":round(pnl),"수익률(%)":round(pnl/invested*100,2),"매도내역":" → ".join(p["exit_notes"])})
        month_key=(dt.year,dt.month)
        if month_key!=last_month:
            last_month=month_key
            if month_entries.get(month_key,0)<monthly_buys:
                rank=_rank_on_date(histories,dt);held=set(positions)
                if not rank.empty:
                    eligible=rank[(rank["score"]>=min_score)&(rank["trend"]>0)]
                    pending=[{"code":r["code"],"name":r["name"],"score":float(r["score"])} for _,r in eligible.iterrows() if r["code"] not in held][:monthly_buys]
        equity_rows.append((dt,total_equity(dt)))
    if positions:
        dt=dates[-1]
        for code,p in list(positions.items()):
            b=_bar(histories[code],dt);sell=b["close"] if b else p["last_price"];qty=p["qty"];proceeds=qty*sell;fee=proceeds*fee_side;cash+=proceeds-fee;p["realized_cash"]+=proceeds-fee;p["exit_notes"].append("종료일 청산");invested=p["initial_qty"]*p["entry"]*(1+fee_side);pnl=p["realized_cash"]-invested;completed.append({"매수일":p["entry_date"].date(),"최종매도일":dt.date(),"종목코드":code,"종목명":p["name"],"점수":round(float(p["score"]),1),"매수가":round(p["entry"]),"초기수량":p["initial_qty"],"실현손익(원)":round(pnl),"수익률(%)":round(pnl/invested*100,2),"매도내역":" → ".join(p["exit_notes"])});exits.append({"날짜":dt.date(),"종목명":p["name"],"종목코드":code,"구분":"종료일 청산","수량":qty,"가격":round(sell)})
        positions.clear();equity_rows.append((dt,cash))
    eq=pd.DataFrame(equity_rows,columns=["date","HY DYNAMIC12"]).drop_duplicates("date",keep="last").set_index("date")
    if not eq.empty:
        kospi=yf.download("^KS11",start=str(start_date),end=str(end_date+timedelta(days=1)),auto_adjust=True,progress=False)
        if not kospi.empty:
            k=kospi["Close"];k=k.iloc[:,0] if isinstance(k,pd.DataFrame) else k;k.index=pd.to_datetime(k.index);k.index=k.index.tz_localize(None) if getattr(k.index,"tz",None) is not None else k.index;k=k.reindex(eq.index).ffill().dropna()
            if not k.empty:eq=eq.join(((k/float(k.iloc[0]))*initial_cash).rename("KOSPI"),how="left").ffill()
    tdf=pd.DataFrame(completed);xdf=pd.DataFrame(exits)
    if eq.empty:return tdf,xdf,eq,{}
    curve=eq["HY DYNAMIC12"].dropna();final=float(curve.iloc[-1]);total=final/initial_cash-1;years=max((pd.Timestamp(end_date)-pd.Timestamp(start_date)).days/365.25,1/365.25);cagr=(final/initial_cash)**(1/years)-1;dd=curve/curve.cummax()-1;mdd=float(dd.min()) if not dd.empty else 0.;win=float((tdf["수익률(%)"]>0).mean()) if not tdf.empty else 0.
    return tdf,xdf,eq,{"initial":initial_cash,"final":final,"total":total,"cagr":cagr,"mdd":mdd,"win":win,"n":len(tdf)}
def render_backtest_tab():
    st.subheader("📊 한국장 실전형 백테스트")
    st.caption("무조건 매주 매수하지 않습니다. 월 1~2종목만 선별하고, 기준점수를 넘는 강한 후보가 없으면 그 달은 현금으로 대기합니다.")
    today=date.today();c1,c2,c3=st.columns(3);start=c1.date_input("시작일",value=today-timedelta(days=365*3),max_value=today,key="bt_start");end=c2.date_input("종료일",value=today,max_value=today,key="bt_end");initial_cash=c3.number_input("초기자금(원)",min_value=1_000_000,value=10_000_000,step=1_000_000,key="bt_cash")
    d1,d2,d3=st.columns(3);monthly_buys=d1.selectbox("월 최대 신규매수 종목수",[1,2],index=1,key="bt_monthly");max_positions=d2.selectbox("동시 최대 보유종목",[1,2,3,4],index=1,key="bt_maxpos");hold_days=d3.selectbox("최대 보유기간(거래일)",[20,40,60,90],index=2,key="bt_hold")
    e1,e2,e3=st.columns(3);min_score=e1.number_input("최소 매수 판정점수",min_value=50.0,max_value=100.0,value=75.0,step=1.0,key="bt_minscore");stop_pct=e2.number_input("기본 손절률(%)",min_value=1.0,max_value=20.0,value=5.0,step=.5,key="bt_stop");trail_pct=e3.number_input("고점대비 전량매도(%)",min_value=3.0,max_value=30.0,value=10.0,step=1.0,key="bt_trail")
    cost_pct=st.number_input("왕복 비용+슬리피지(%)",min_value=0.0,max_value=2.0,value=.30,step=.05,key="bt_cost")
    st.info("매수: 매월 첫 거래일에 전체 후보를 평가 → 점수 기준 + 상승추세를 모두 통과한 종목 중 최대 1~2개만 다음 거래일 시가 매수. 조건 미달이면 매수하지 않습니다. 익절: +15% 30% → +20% 30% → +25% 잔량 전량.")
    if st.button("▶ 월 1~2종목 선별 백테스트 실행",type="primary",use_container_width=True,key="kr_backtest_run_v3"):
        if start>=end:st.error("시작일은 종료일보다 앞서야 합니다.");return
        with st.spinner("월별 우수 후보 선별 및 계좌 시뮬레이션 중..."):
            data=_download(start-timedelta(days=120),end);trades,exits,equity,stats=_simulate(data,start,end,monthly_buys,max_positions,hold_days,stop_pct,trail_pct,cost_pct,float(initial_cash),min_score)
        if not stats:st.warning("백테스트 결과를 만들지 못했습니다.");return
        m1,m2,m3,m4,m5,m6=st.columns(6);m1.metric("초기자금",f"{stats['initial']:,.0f}원");m2.metric("최종자산",f"{stats['final']:,.0f}원");m3.metric("누적수익률",f"{stats['total']*100:.1f}%");m4.metric("CAGR",f"{stats['cagr']*100:.1f}%");m5.metric("MDD",f"{stats['mdd']*100:.1f}%");m6.metric("승률",f"{stats['win']*100:.1f}%")
        st.markdown("### 누적 자산 vs KOSPI");st.line_chart(equity);st.markdown("### 종목별 완료 거래");st.dataframe(trades.sort_values("최종매도일",ascending=False),use_container_width=True,hide_index=True) if not trades.empty else st.info("완료된 거래가 없습니다.");st.markdown("### 분할매도 / 손절 / 트레일링 상세");st.dataframe(exits.sort_values("날짜",ascending=False),use_container_width=True,hide_index=True) if not exits.empty else st.info("매도내역이 없습니다.")
        st.caption("현재 버전은 월 1~2종목 집중형입니다. 과거 가격·거래량·추세 기반 proxy이며 과거 외국인/기관 수급 및 당시 재무정보는 아직 포함하지 않았습니다.")