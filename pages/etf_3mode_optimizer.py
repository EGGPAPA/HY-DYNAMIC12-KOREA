import pandas as pd
import streamlit as st
import yfinance as yf
from itertools import product

st.title('🎛️ ETF 3모드 CAGR/MDD 최적화')
st.caption('공격형·균형형·방어형을 같은 기간과 같은 가격 데이터로 비교합니다. 전일 신호를 다음 거래일 수익률에 적용합니다.')

c1,c2,c3=st.columns(3)
with c1: start=st.date_input('시작일',pd.Timestamp('2023-08-19'))
with c2: end=st.date_input('종료일',pd.Timestamp.today())
with c3: initial=st.number_input('초기자금(원)',min_value=1_000_000,value=10_000_000,step=1_000_000)
etf=st.selectbox('ETF',['TIGER 코리아TOP10','KODEX200'])
precise=st.checkbox('정밀 탐색',False)

PROFILES={
 '🚀 공격형':{'target':30,'capture':90,'penalty':1.0},
 '⚖️ 균형형':{'target':25,'capture':80,'penalty':2.0},
 '🛡️ 방어형':{'target':22,'capture':65,'penalty':4.0},
}
st.info('공격형은 상승 포착률을, 균형형은 CAGR/MDD 균형을, 방어형은 낙폭 제한을 우선합니다. 강세장에서는 100% 보유를 우선합니다.')

@st.cache_data(ttl=1800,show_spinner=False)
def load_price(ticker,start,end):
    s,e=pd.Timestamp(start),pd.Timestamp(end)
    try:
        d=yf.download(ticker,start=str(s.date()),end=str((e+pd.Timedelta(days=1)).date()),auto_adjust=True,progress=False,threads=False,timeout=15)
        if d is None or d.empty:return pd.Series(dtype=float)
        x=d['Close']
        if isinstance(x,pd.DataFrame):x=x.iloc[:,0]
        x=pd.to_numeric(x,errors='coerce').dropna();x.index=pd.to_datetime(x.index).tz_localize(None)
        return x
    except Exception:return pd.Series(dtype=float)

def metrics(eq):
    if len(eq)<2:return 0.,0.,0.
    years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    total=(eq.iloc[-1]/eq.iloc[0]-1)*100
    cagr=((eq.iloc[-1]/eq.iloc[0])**(1/years)-1)*100
    mdd=(eq/eq.cummax()-1).min()*100
    return total,cagr,mdd

def run(px,fast,slow,weak,bear,ddtrig,severe,buffer):
    mf=px.rolling(fast).mean();ms=px.rolling(slow).mean();slope=ms.pct_change(10);dd=px/px.cummax()-1
    ws=[];risk=False
    for i,p in enumerate(px):
        valid=pd.notna(ms.iloc[i]) and pd.notna(slope.iloc[i])
        bull=valid and p>=ms.iloc[i] and slope.iloc[i]>0
        if bull:
            risk=False;w=1.0
        else:
            if dd.iloc[i]<=-ddtrig/100:risk=True
            if risk and valid and p>=ms.iloc[i]*(1+buffer/100) and slope.iloc[i]>=0:risk=False
            if risk:w=severe/100
            elif valid and p<ms.iloc[i] and slope.iloc[i]<0:w=bear/100
            elif pd.notna(mf.iloc[i]) and p<mf.iloc[i]:w=weak/100
            else:w=1.0
        ws.append(w)
    w=pd.Series(ws,index=px.index).shift(1).fillna(1.0)
    return initial*(1+px.pct_change().fillna(0)*w).cumprod()

def choose(df,profile):
    t=profile['target'];cap=profile['capture'];pen=profile['penalty']
    q=df[(df['MDD(%)']>=-t)&(df['포착률(%)']>=cap)]
    if not q.empty:return q.sort_values(['CAGR(%)','MDD(%)'],ascending=[False,False]).iloc[0],True
    x=df.copy();x['벌점']=((-t-x['MDD(%)']).clip(lower=0)*pen+(cap-x['포착률(%)']).clip(lower=0))
    return x.sort_values(['벌점','CAGR(%)'],ascending=[True,False]).iloc[0],False

if st.button('🚀 3모드 동시 최적화 실행',type='primary',use_container_width=True):
    ticker='292150.KS' if etf.startswith('TIGER') else '069500.KS'
    px=load_price(ticker,start,end)
    if len(px)<205:st.error('가격 데이터가 부족합니다. 최소 약 205거래일이 필요합니다.');st.stop()
    bh=initial*px/px.iloc[0];bh_ret,bh_cagr,bh_mdd=metrics(bh)
    fasts=[40,60,80] if not precise else [30,40,50,60,80]
    slows=[120,160,200] if not precise else [100,120,140,160,180,200]
    weak=[80,90,100];bear=[30,50,70];dds=[10,12,15,18,20];sev=[0,20,40];buf=[0,2]
    configs=[x for x in product(fasts,slows,weak,bear,dds,sev,buf) if x[0]<x[1] and x[3]<=x[2]]
    rows=[];curves={};bar=st.progress(0)
    for n,cfg in enumerate(configs,1):
        eq=run(px,*cfg);ret,cagr,mdd=metrics(eq);capture=cagr/bh_cagr*100 if bh_cagr>0 else 100
        rows.append({'단기':cfg[0],'장기':cfg[1],'약화ETF':cfg[2],'약세ETF':cfg[3],'고점방어':cfg[4],'최종ETF':cfg[5],'버퍼':cfg[6],'누적수익률(%)':ret,'CAGR(%)':cagr,'MDD(%)':mdd,'포착률(%)':capture})
        curves[cfg]=eq
        if n%100==0 or n==len(configs):bar.progress(n/len(configs))
    bar.empty();df=pd.DataFrame(rows)
    results=[];selected={}
    for name,p in PROFILES.items():
        best,ok=choose(df,p);cfg=(int(best['단기']),int(best['장기']),int(best['약화ETF']),int(best['약세ETF']),int(best['고점방어']),int(best['최종ETF']),int(best['버퍼']))
        selected[name]=curves[cfg]
        results.append({'모드':name,'목표MDD':-p['target'],'목표포착률':p['capture'],'CAGR(%)':best['CAGR(%)'],'MDD(%)':best['MDD(%)'],'포착률(%)':best['포착률(%)'],'누적수익률(%)':best['누적수익률(%)'],'최종자산(원)':selected[name].iloc[-1],'목표충족':'✅' if ok else '근접','설정':f"{cfg[0]}/{cfg[1]}일선 · 약화{cfg[2]}% · 약세{cfg[3]}% · DD-{cfg[4]}%→{cfg[5]}%"})
    r=pd.DataFrame(results)
    st.subheader('📊 3모드 비교')
    st.dataframe(r.round(2),use_container_width=True,hide_index=True)
    st.caption(f'Buy & Hold: CAGR {bh_cagr:.1f}% · MDD {bh_mdd:.1f}% · 누적수익률 {bh_ret:.1f}%')
    chart={'Buy & Hold':bh}
    chart.update(selected);st.line_chart(pd.DataFrame(chart))
    st.subheader('💰 1,000만원 기준 예상 비교')
    cols=st.columns(3)
    for col,row in zip(cols,results):
        with col:
            st.metric(row['모드'],f"{row['최종자산(원)']:,.0f}원")
            st.caption(f"CAGR {row['CAGR(%)']:.1f}% / MDD {row['MDD(%)']:.1f}% / 포착 {row['포착률(%)']:.1f}%")
    st.warning('백테스트는 과거 결과이며 미래 수익을 보장하지 않습니다. 1년 강세장 결과만으로 전략을 선택하지 말고 여러 기간으로 반복 검증하세요.')