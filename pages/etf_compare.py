from datetime import date, timedelta
from itertools import product

import pandas as pd
import streamlit as st
import yfinance as yf

import korea_backtest_ui as bt

st.set_page_config(page_title="HY DYNAMIC12 V5", page_icon="🚀", layout="wide")

BENCHMARKS = {"TIGER 코리아TOP10": "292150.KS", "KODEX 200": "069500.KS"}


def _series(raw):
    if raw is None or raw.empty: return pd.Series(dtype=float)
    s = raw["Close"]
    if isinstance(s, pd.DataFrame): s = s.iloc[:, 0]
    s = pd.to_numeric(s, errors="coerce").dropna(); s.index = pd.to_datetime(s.index)
    if getattr(s.index, "tz", None) is not None: s.index = s.index.tz_localize(None)
    return s

@st.cache_data(ttl=3600, show_spinner=False)
def _prices(symbol, start, end):
    return _series(yf.download(symbol, start=str(start), end=str(end + timedelta(days=1)), auto_adjust=True, progress=False))

def _stats(s, start, end, cash):
    s=s[(s.index>=pd.Timestamp(start))&(s.index<=pd.Timestamp(end))]
    if s.empty:return pd.Series(dtype=float),{}
    curve=s/s.iloc[0]*cash; final=float(curve.iloc[-1]); years=max((pd.Timestamp(end)-pd.Timestamp(start)).days/365.25,1/365.25)
    return curve,{"final":final,"total":final/cash-1,"cagr":(final/cash)**(1/years)-1,"mdd":float((curve/curve.cummax()-1).min())}

def _score(cagr,mdd,tiger,kodex):
    # V5: 수익성 중심 + -20% 이내 MDD 우대 + ETF 초과수익 보상
    alpha=cagr-max(tiger["cagr"],kodex["cagr"])
    risk_pen=max(0,abs(mdd)-0.20)
    return cagr*100 + alpha*35 - risk_pen*120

st.title("🚀 HY DYNAMIC12 V5 · ETF 초과수익 최적화")
st.caption("목표: CAGR을 높이되 MDD -20% 안팎을 우선 관리하고, TIGER 코리아TOP10·KODEX200 대비 초과수익 가능성을 자동 탐색합니다.")

c1,c2,c3=st.columns(3); today=date.today()
start=c1.date_input("시작일",today-timedelta(days=365*3),max_value=today)
end=c2.date_input("종료일",today,max_value=today)
cash=c3.number_input("초기자금(원)",1_000_000,10_000_000,step=1_000_000)

st.info("V5는 월 1~2종목, 동시 1~3종목, 보유기간 60/90/120일, 진입점수 75/78/82, 손절 5/6/7%, 익절·트레일링 조합을 비교합니다. 결과는 미래 수익을 보장하지 않습니다.")

if st.button("🚀 V5 자동 최적화 실행",type="primary",use_container_width=True):
    if start>=end: st.error("시작일은 종료일보다 앞서야 합니다."); st.stop()
    with st.spinner("ETF 기준성과와 HY 후보전략을 계산 중입니다..."):
        tiger_curve,tiger=_stats(_prices(BENCHMARKS["TIGER 코리아TOP10"],start,end),start,end,float(cash))
        kodex_curve,kodex=_stats(_prices(BENCHMARKS["KODEX 200"],start,end),start,end,float(cash))
        if not tiger or not kodex: st.error("ETF 데이터를 불러오지 못했습니다."); st.stop()
        data=bt._download(start-timedelta(days=220),end)
        # 계산량을 억제하면서 수익/보유/익절 구조를 넓게 탐색
        configs=list(product([1,2],[1,2,3],[60,90,120],[75.0,78.0,82.0],[5.0,6.0,7.0],[(15,20,30,30,10),(20,20,40,30,10),(20,20,50,30,12),(25,20,50,30,12),(25,15,60,25,12)]))
        rows=[]; best=None
        bar=st.progress(0)
        for i,(ml,mp,hd,ms,sl,ex) in enumerate(configs):
            tp1,s1,tp2,s2,tr=ex
            try:
                _,_,eq,hs=bt._simulate(data,start,end,ml,mp,hd,sl,tr,0.30,float(cash),ms,tp1,s1,tp2,s2)
                if hs:
                    sc=_score(hs["cagr"],hs["mdd"],tiger,kodex)
                    rec={"월매수":ml,"동시보유":mp,"보유일":hd,"진입점수":ms,"손절%":sl,"1차익절%":tp1,"1차매도%":s1,"2차익절%":tp2,"2차매도%":s2,"트레일링%":tr,"누적수익률%":hs["total"]*100,"CAGR%":hs["cagr"]*100,"MDD%":hs["mdd"]*100,"ETF초과CAGR%p":(hs["cagr"]-max(tiger["cagr"],kodex["cagr"]))*100,"V5점수":sc,"eq":eq,"stats":hs}
                    rows.append(rec)
                    if best is None or sc>best["V5점수"]: best=rec
            except Exception:
                pass
            if i%10==0: bar.progress((i+1)/len(configs))
        bar.empty()
    if not rows: st.error("유효한 전략 결과가 없습니다."); st.stop()
    df=pd.DataFrame(rows).sort_values("V5점수",ascending=False).reset_index(drop=True)
    b=df.iloc[0]
    st.success(f"추천 V5: 월 {int(b['월매수'])}종목 · 동시 {int(b['동시보유'])}종목 · 진입 {b['진입점수']:.0f}점 · 손절 -{b['손절%']:.0f}% · {b['1차익절%']:.0f}%에서 {b['1차매도%']:.0f}% 매도 · {b['2차익절%']:.0f}%에서 {b['2차매도%']:.0f}% 매도 · 잔여 트레일링 -{b['트레일링%']:.0f}%")
    m1,m2,m3,m4,m5=st.columns(5)
    m1.metric("V5 CAGR",f"{b['CAGR%']:.1f}%");m2.metric("V5 누적수익률",f"{b['누적수익률%']:.1f}%");m3.metric("V5 MDD",f"{b['MDD%']:.1f}%");m4.metric("TIGER CAGR",f"{tiger['cagr']*100:.1f}%");m5.metric("KODEX200 CAGR",f"{kodex['cagr']*100:.1f}%")
    if b['ETF초과CAGR%p']>0 and b['MDD%']>=-20: st.success("🟢 목표 달성 후보: ETF보다 높은 CAGR과 -20% 이내 MDD를 동시에 달성했습니다.")
    elif b['ETF초과CAGR%p']>0: st.warning("🟡 ETF 수익률은 넘었지만 MDD가 -20%를 초과합니다.")
    else: st.warning(f"🟠 최선 후보도 강한 ETF보다 CAGR이 {abs(b['ETF초과CAGR%p']):.1f}%p 낮습니다. 전략 개선이 더 필요합니다.")
    show=df.drop(columns=["eq","stats"]).head(15).copy()
    for col in ["누적수익률%","CAGR%","MDD%","ETF초과CAGR%p","V5점수"]: show[col]=show[col].round(1)
    st.subheader("🏆 V5 후보 TOP15");st.dataframe(show,use_container_width=True,hide_index=True)
    best_full=best; eq=best_full['eq']; comp=pd.DataFrame(index=eq.index); comp['HY DYNAMIC12 V5']=eq['HY DYNAMIC12']; comp['TIGER 코리아TOP10']=tiger_curve.reindex(comp.index).ffill(); comp['KODEX 200']=kodex_curve.reindex(comp.index).ffill()
    st.subheader("📈 최적 후보 vs ETF");st.line_chart(comp)
    st.caption("중요: 같은 과거 데이터에서 파라미터를 고르면 과최적화될 수 있습니다. 추천값은 다음 단계에서 기간분할/워크포워드 검증을 거쳐야 실전 후보로 볼 수 있습니다.")
