import pandas as pd
import streamlit as st
import yfinance as yf
from itertools import product

st.title('🎛️ ETF 3모드 CAGR/MDD 최적화 · 강제 분리형')
st.caption('공격형·균형형·방어형을 같은 기간에서 비교하되, 각 모드는 서로 다른 MDD 한도와 탐색범위를 사용합니다. 목표 미달은 숨기지 않고 명확히 표시합니다.')

c1,c2,c3=st.columns(3)
with c1: start=st.date_input('시작일',pd.Timestamp('2023-08-19'))
with c2: end=st.date_input('종료일',pd.Timestamp.today())
with c3: initial=st.number_input('초기자금(원)',min_value=1_000_000,value=10_000_000,step=1_000_000)
etf=st.selectbox('ETF',['TIGER 코리아TOP10','KODEX200'])
precise=st.checkbox('정밀 탐색',False)

PROFILES={
 '🚀 공격형':{'target':30,'capture':85,'fast':[40,60,80],'slow':[140,160,200],'weak':[90,100],'bear':[50,70,90],'dd':[15,18,20,22],'sev':[20,40,60]},
 '⚖️ 균형형':{'target':25,'capture':75,'fast':[40,60,80],'slow':[120,140,160,200],'weak':[80,90,100],'bear':[30,50,70],'dd':[12,15,18,20],'sev':[0,20,40]},
 '🛡️ 방어형':{'target':22,'capture':60,'fast':[30,40,60],'slow':[100,120,140,160],'weak':[70,80,90],'bear':[20,30,50],'dd':[10,12,15,18],'sev':[0,10,20]},
}

REFERENCE={'TIGER 코리아TOP10':{'cagr':53.1,'mdd':-21.4},'KODEX200':{'cagr':49.8,'mdd':-21.2}}
st.info('공격형은 상승 포착률, 균형형은 CAGR/MDD 균형, 방어형은 낙폭 제한을 우선합니다. 각 모드는 MDD 조건을 실제 필터로 강제 적용하며, 만족 조합이 없으면 목표 미달로 표시합니다.')

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

def build_configs(profile,precise):
    fast=profile['fast'];slow=profile['slow'];weak=profile['weak'];bear=profile['bear'];dd=profile['dd'];sev=profile['sev'];buf=[0,2]
    if precise:
        fast=sorted(set(fast+[50,70,100]));slow=sorted(set(slow+[180]));weak=sorted(set(weak+[75,85,95]));bear=sorted(set(bear+[40,60,80]));dd=sorted(set(dd+[14,16,20]));sev=sorted(set(sev+[30]));buf=[0,1,2]
    return [x for x in product(fast,slow,weak,bear,dd,sev,buf) if x[0]<x[1] and x[3]<=x[2]]

def choose_hard(df,profile):
    t=profile['target'];cap=profile['capture']
    hard=df[(df['MDD(%)']>=-t)&(df['포착률(%)']>=cap)].copy()
    if not hard.empty:
        return hard.sort_values(['CAGR(%)','MDD(%)'],ascending=[False,False]).iloc[0],True,len(hard)
    mdd_only=df[df['MDD(%)']>=-t].copy()
    if not mdd_only.empty:
        return mdd_only.sort_values(['CAGR(%)','포착률(%)'],ascending=[False,False]).iloc[0],False,len(mdd_only)
    near=df.copy();near['MDD초과']=(-t-near['MDD(%)']).clip(lower=0)
    return near.sort_values(['MDD초과','CAGR(%)'],ascending=[True,False]).iloc[0],False,0

if st.button('🚀 3모드 강제 분리 최적화 실행',type='primary',use_container_width=True):
    ticker='292150.KS' if etf.startswith('TIGER') else '069500.KS'
    px=load_price(ticker,start,end)
    if len(px)<205:st.error('가격 데이터가 부족합니다. 최소 약 205거래일이 필요합니다.');st.stop()
    bh=initial*px/px.iloc[0];bh_ret,bh_cagr,bh_mdd=metrics(bh)

    results=[];selected={};details={}
    total=sum(len(build_configs(p,precise)) for p in PROFILES.values())
    done=0;bar=st.progress(0,text=f'0/{total} 조합')
    for name,p in PROFILES.items():
        rows=[];curves={};configs=build_configs(p,precise)
        for cfg in configs:
            eq=run(px,*cfg);ret,cagr,mdd=metrics(eq);capture=cagr/bh_cagr*100 if bh_cagr>0 else 100
            rows.append({'단기':cfg[0],'장기':cfg[1],'약화ETF':cfg[2],'약세ETF':cfg[3],'고점방어':cfg[4],'최종ETF':cfg[5],'버퍼':cfg[6],'누적수익률(%)':ret,'CAGR(%)':cagr,'MDD(%)':mdd,'포착률(%)':capture})
            curves[cfg]=eq;done+=1
            if done%100==0 or done==total:bar.progress(done/total,text=f'{done}/{total} 조합')
        df=pd.DataFrame(rows);best,ok,count=choose_hard(df,p)
        cfg=(int(best['단기']),int(best['장기']),int(best['약화ETF']),int(best['약세ETF']),int(best['고점방어']),int(best['최종ETF']),int(best['버퍼']))
        selected[name]=curves[cfg];details[name]=(df,best,cfg)
        status='✅ 충족' if ok else ('⚠️ 포착률 미달' if count>0 else '❌ MDD 미달')
        results.append({'모드':name,'목표MDD':-p['target'],'목표포착률':p['capture'],'CAGR(%)':best['CAGR(%)'],'MDD(%)':best['MDD(%)'],'포착률(%)':best['포착률(%)'],'누적수익률(%)':best['누적수익률(%)'],'최종자산(원)':selected[name].iloc[-1],'목표충족':status,'설정':f"{cfg[0]}/{cfg[1]}일선 · 약화{cfg[2]}% · 약세{cfg[3]}% · DD-{cfg[4]}%→{cfg[5]}%"})
    bar.empty();r=pd.DataFrame(results)

    st.subheader('📊 3모드 비교')
    st.dataframe(r.round(2),use_container_width=True,hide_index=True)
    st.caption(f'Buy & Hold: CAGR {bh_cagr:.1f}% · MDD {bh_mdd:.1f}% · 누적수익률 {bh_ret:.1f}%')

    ref=REFERENCE[etf]
    st.info(f"기존 기준전략 참고값: CAGR 약 {ref['cagr']:.1f}% / MDD 약 {ref['mdd']:.1f}% — 새 3모드가 이 기준보다 실제로 나은지 비교하세요.")

    chart={'Buy & Hold':bh};chart.update(selected);st.line_chart(pd.DataFrame(chart))

    st.subheader('💰 1,000만원 기준 최종자산 비교')
    cols=st.columns(3)
    for col,row in zip(cols,results):
        with col:
            st.metric(row['모드'],f"{row['최종자산(원)']:,.0f}원")
            st.caption(f"CAGR {row['CAGR(%)']:.1f}% / MDD {row['MDD(%)']:.1f}% / 포착 {row['포착률(%)']:.1f}% / {row['목표충족']}")

    st.subheader('🔎 모드별 선택 근거')
    for name,p in PROFILES.items():
        df,best,cfg=details[name]
        hard=df[(df['MDD(%)']>=-p['target'])&(df['포착률(%)']>=p['capture'])].sort_values(['CAGR(%)','MDD(%)'],ascending=[False,False])
        with st.expander(f'{name} 후보 상세'):
            if hard.empty:
                mdd_only=df[df['MDD(%)']>=-p['target']].sort_values('CAGR(%)',ascending=False)
                if mdd_only.empty:st.warning(f'MDD {p["target"]}% 이내 후보가 없습니다. 현재 표시값은 가장 가까운 후보입니다.')
                else:
                    st.warning(f'MDD 조건은 만족하지만 포착률 {p["capture"]}%까지 동시에 만족한 후보가 없습니다.')
                    st.dataframe(mdd_only.head(10).round(2),use_container_width=True,hide_index=True)
            else:st.dataframe(hard.head(10).round(2),use_container_width=True,hide_index=True)

    st.warning('백테스트는 과거 결과이며 미래 수익을 보장하지 않습니다. 특히 약 3년 구간의 높은 CAGR은 장기 기대수익률로 그대로 사용하면 안 됩니다.')