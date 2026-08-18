import pandas as pd
import streamlit as st
import yfinance as yf
from itertools import product

st.title('🎛️ ETF 3모드 최적화 · 기존 우수전략 포함형')
st.caption('기존 MDD/CAGR 엔진의 탐색공간을 3모드에 다시 포함합니다. MDD 한도를 먼저 지키고 그 안에서 CAGR이 가장 높은 전략을 선택합니다.')

c1,c2,c3=st.columns(3)
with c1: start=st.date_input('시작일',pd.Timestamp('2023-08-19'))
with c2: end=st.date_input('종료일',pd.Timestamp.today())
with c3: initial=st.number_input('초기자금(원)',min_value=1_000_000,value=10_000_000,step=1_000_000)
etf=st.selectbox('ETF',['TIGER 코리아TOP10','KODEX200'])
precise=st.checkbox('정밀 탐색',False)

PROFILES={
 '🚀 공격형':{'target':30,'capture':80},
 '⚖️ 균형형':{'target':25,'capture':70},
 '🛡️ 방어형':{'target':22,'capture':55},
}
REFERENCE={'TIGER 코리아TOP10':{'cagr':53.1,'mdd':-21.4},'KODEX200':{'cagr':49.8,'mdd':-21.2}}
st.info('핵심 변경: 모드별로 탐색공간을 잘라내지 않습니다. 과거에 좋은 결과를 냈던 기존 엔진의 전체 후보군을 모두 만든 뒤 공격형·균형형·방어형이 각자의 MDD 한도 안에서 선택합니다.')

@st.cache_data(ttl=1800,show_spinner=False)
def load_price(ticker,start,end):
    s,e=pd.Timestamp(start),pd.Timestamp(end)
    try:
        d=yf.download(ticker,start=str(s.date()),end=str((e+pd.Timedelta(days=1)).date()),auto_adjust=True,progress=False,threads=False,timeout=15)
        if d is None or d.empty:return pd.Series(dtype=float)
        x=d['Close']
        if isinstance(x,pd.DataFrame):x=x.iloc[:,0]
        x=pd.to_numeric(x,errors='coerce').dropna();x.index=pd.to_datetime(x.index)
        if getattr(x.index,'tz',None) is not None:x.index=x.index.tz_localize(None)
        return x
    except Exception:return pd.Series(dtype=float)

def metrics(eq):
    eq=pd.to_numeric(eq,errors='coerce').dropna()
    if len(eq)<2:return 0.,0.,0.
    years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    total=(eq.iloc[-1]/eq.iloc[0]-1)*100
    cagr=((eq.iloc[-1]/eq.iloc[0])**(1/years)-1)*100
    mdd=(eq/eq.cummax()-1).min()*100
    return total,cagr,mdd

def run(px,fast,slow,slope_days,weak,bear,ddtrig,severe,buffer):
    mf=px.rolling(fast).mean();ms=px.rolling(slow).mean();slope=ms.pct_change(slope_days);dd=px/px.cummax()-1
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

def configs(precise):
    if precise:
        fast=[30,40,50,60,70,80,90,100];slow=[100,120,140,160,180,200];slopes=[5,10,15,20]
        weak=[70,80,90,100];bear=[10,20,30,40,50,60,70,80];dd=[8,10,12,15,18,20,22];sev=[0,10,20,30,40];buf=[0,1,2,3]
    else:
        # 6904665의 강세장 보존형 탐색 범위를 그대로 포함
        fast=[40,60,80];slow=[120,160,200];slopes=[10,20]
        weak=[80,90,100];bear=[30,50,70];dd=[12,15,18,20];sev=[0,20,40];buf=[0,2]
    return [x for x in product(fast,slow,slopes,weak,bear,dd,sev,buf) if x[0]<x[1] and x[4]<=x[3]]

def choose(df,p):
    mdd_ok=df[df['MDD(%)']>=-p['target']].copy()
    both=mdd_ok[mdd_ok['포착률(%)']>=p['capture']].copy()
    if not both.empty:return both.sort_values(['CAGR(%)','MDD(%)'],ascending=[False,False]).iloc[0],'✅ 충족'
    if not mdd_ok.empty:return mdd_ok.sort_values(['CAGR(%)','포착률(%)'],ascending=[False,False]).iloc[0],'⚠️ 포착률 미달'
    near=df.copy();near['MDD초과']=(-p['target']-near['MDD(%)']).clip(lower=0)
    return near.sort_values(['MDD초과','CAGR(%)'],ascending=[True,False]).iloc[0],'❌ MDD 미달'

if st.button('🚀 기존 우수전략 포함 3모드 실행',type='primary',use_container_width=True):
    ticker='292150.KS' if etf.startswith('TIGER') else '069500.KS'
    px=load_price(ticker,start,end)
    if len(px)<205:st.error('가격 데이터가 부족합니다. 최소 약 205거래일이 필요합니다.');st.stop()
    bh=initial*px/px.iloc[0];bh_ret,bh_cagr,bh_mdd=metrics(bh)
    cfgs=configs(precise);rows=[];curves={};bar=st.progress(0,text=f'0/{len(cfgs)} 조합')
    for n,cfg in enumerate(cfgs,1):
        eq=run(px,*cfg);ret,cagr,mdd=metrics(eq);capture=cagr/bh_cagr*100 if bh_cagr>0 else 100
        rows.append({'단기':cfg[0],'장기':cfg[1],'기울기':cfg[2],'약화ETF':cfg[3],'약세ETF':cfg[4],'고점방어':cfg[5],'최종ETF':cfg[6],'버퍼':cfg[7],'누적수익률(%)':ret,'CAGR(%)':cagr,'MDD(%)':mdd,'포착률(%)':capture})
        curves[cfg]=eq
        if n%100==0 or n==len(cfgs):bar.progress(n/len(cfgs),text=f'{n}/{len(cfgs)} 조합')
    bar.empty();df=pd.DataFrame(rows)

    results=[];selected={}
    for name,p in PROFILES.items():
        best,status=choose(df,p)
        cfg=(int(best['단기']),int(best['장기']),int(best['기울기']),int(best['약화ETF']),int(best['약세ETF']),int(best['고점방어']),int(best['최종ETF']),int(best['버퍼']))
        selected[name]=curves[cfg]
        results.append({'모드':name,'목표MDD':-p['target'],'목표포착률':p['capture'],'CAGR(%)':best['CAGR(%)'],'MDD(%)':best['MDD(%)'],'포착률(%)':best['포착률(%)'],'누적수익률(%)':best['누적수익률(%)'],'최종자산(원)':selected[name].iloc[-1],'목표충족':status,'설정':f'{cfg[0]}/{cfg[1]}일 · slope{cfg[2]} · 약화{cfg[3]} · 약세{cfg[4]} · DD-{cfg[5]}→{cfg[6]} · buf{cfg[7]}'})
    r=pd.DataFrame(results)
    st.subheader('📊 3모드 비교');st.dataframe(r.round(2),use_container_width=True,hide_index=True)
    st.caption(f'Buy & Hold: CAGR {bh_cagr:.1f}% · MDD {bh_mdd:.1f}% · 누적수익률 {bh_ret:.1f}%')
    ref=REFERENCE[etf]
    st.info(f"기존 기준 참고: CAGR 약 {ref['cagr']:.1f}% / MDD 약 {ref['mdd']:.1f}%. 이번 결과는 같은 날짜 구간의 실제 계산값과 비교하세요.")
    chart={'Buy & Hold':bh};chart.update(selected);st.line_chart(pd.DataFrame(chart))

    st.subheader('💰 1,000만원 기준 최종자산 비교')
    cols=st.columns(3)
    for col,row in zip(cols,results):
        with col:
            st.metric(row['모드'],f"{row['최종자산(원)']:,.0f}원")
            st.caption(f"CAGR {row['CAGR(%)']:.1f}% / MDD {row['MDD(%)']:.1f}% / 포착 {row['포착률(%)']:.1f}% / {row['목표충족']}")

    st.subheader('🏆 MDD 구간별 최고 CAGR')
    bands=[]
    for limit in [20,22,25,30]:
        q=df[df['MDD(%)']>=-limit].sort_values('CAGR(%)',ascending=False)
        if not q.empty:
            z=q.iloc[0];bands.append({'MDD 한도':f'-{limit}%','CAGR(%)':z['CAGR(%)'],'MDD(%)':z['MDD(%)'],'포착률(%)':z['포착률(%)'],'설정':f"{int(z['단기'])}/{int(z['장기'])} · slope{int(z['기울기'])} · 약화{int(z['약화ETF'])} · 약세{int(z['약세ETF'])} · DD-{int(z['고점방어'])}→{int(z['최종ETF'])}"})
        else:bands.append({'MDD 한도':f'-{limit}%','CAGR(%)':None,'MDD(%)':None,'포착률(%)':None,'설정':'충족 후보 없음'})
    st.dataframe(pd.DataFrame(bands).round(2),use_container_width=True,hide_index=True)
    st.warning('과거 백테스트는 미래 수익을 보장하지 않습니다. 특히 한 구간의 높은 CAGR보다 여러 기간에서 MDD와 CAGR이 반복되는지를 확인해야 합니다.')