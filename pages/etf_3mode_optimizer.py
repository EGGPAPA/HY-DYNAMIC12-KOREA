import pandas as pd
import streamlit as st
import yfinance as yf
from itertools import product

st.title('🎛️ ETF 3모드 최적화 · 레거시 우수엔진 통합')
st.caption('과거 성과가 좋았던 다단계 방어 엔진과 강세장 보존 엔진을 둘 다 계산한 뒤, 각 MDD 구간에서 CAGR이 가장 높은 후보를 선택합니다.')

c1,c2,c3=st.columns(3)
with c1: start=st.date_input('시작일',pd.Timestamp('2023-08-19'))
with c2: end=st.date_input('종료일',pd.Timestamp.today())
with c3: initial=st.number_input('초기자금(원)',min_value=1_000_000,value=10_000_000,step=1_000_000)
etf=st.selectbox('ETF',['TIGER 코리아TOP10','KODEX200'])
precise=st.checkbox('정밀 탐색',False)
PROFILES={'🚀 공격형':30,'⚖️ 균형형':25,'🛡️ 방어형':22}

@st.cache_data(ttl=1800,show_spinner=False)
def load_price(ticker,start,end):
    s,e=pd.Timestamp(start),pd.Timestamp(end)
    try:
        d=yf.download(ticker,start=str(s.date()),end=str((e+pd.Timedelta(days=1)).date()),auto_adjust=True,progress=False,threads=False,timeout=15)
        if d is None or d.empty:d=yf.download(ticker,period='max',auto_adjust=True,progress=False,threads=False,timeout=15)
        if d is None or d.empty:return pd.Series(dtype=float)
        x=d['Close']
        if isinstance(x,pd.DataFrame):x=x.iloc[:,0]
        x=pd.to_numeric(x,errors='coerce').dropna();x.index=pd.to_datetime(x.index)
        if getattr(x.index,'tz',None) is not None:x.index=x.index.tz_localize(None)
        return x[(x.index>=s)&(x.index<=e)]
    except Exception:return pd.Series(dtype=float)

def metrics(eq):
    if len(eq)<2:return 0.,0.,0.
    years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    return (eq.iloc[-1]/eq.iloc[0]-1)*100,((eq.iloc[-1]/eq.iloc[0])**(1/years)-1)*100,(eq/eq.cummax()-1).min()*100

def equity(px,w,capital=None):
    capital=initial if capital is None else capital
    w=pd.Series(w,index=px.index).shift(1).fillna(1.0)
    return capital*(1+px.pct_change().fillna(0)*w).cumprod()

def run_staged(px,fast,slow,w1,w2,ddtrig,severe,buffer,capital=None):
    mf,ms=px.rolling(fast).mean(),px.rolling(slow).mean();dd=px/px.cummax()-1
    out=[];risk=False
    for i,p in enumerate(px):
        if dd.iloc[i]<=-ddtrig/100:risk=True
        if risk and pd.notna(mf.iloc[i]) and pd.notna(ms.iloc[i]) and p>=mf.iloc[i]*(1+buffer/100) and p>=ms.iloc[i]:risk=False
        if risk:w=severe/100
        elif pd.notna(ms.iloc[i]) and p<ms.iloc[i]:w=w2/100
        elif pd.notna(mf.iloc[i]) and p<mf.iloc[i]:w=w1/100
        else:w=1.0
        out.append(w)
    return equity(px,out,capital)

def run_bull(px,fast,slow,slope_days,weak,bear,ddtrig,severe,buffer,capital=None):
    mf,ms=px.rolling(fast).mean(),px.rolling(slow).mean();slope=ms.pct_change(slope_days);dd=px/px.cummax()-1
    out=[];risk=False
    for i,p in enumerate(px):
        valid=pd.notna(ms.iloc[i]) and pd.notna(slope.iloc[i]);bull=valid and p>=ms.iloc[i] and slope.iloc[i]>0
        if bull:risk=False;w=1.0
        else:
            if dd.iloc[i]<=-ddtrig/100:risk=True
            if risk and valid and p>=ms.iloc[i]*(1+buffer/100) and slope.iloc[i]>=0:risk=False
            if risk:w=severe/100
            elif valid and p<ms.iloc[i] and slope.iloc[i]<0:w=bear/100
            elif pd.notna(mf.iloc[i]) and p<mf.iloc[i]:w=weak/100
            else:w=1.0
        out.append(w)
    return equity(px,out,capital)

def add_row(rows,curves,engine,cfg,eq,bh_cagr):
    ret,cagr,mdd=metrics(eq);key=(engine,)+tuple(cfg);curves[key]=eq
    rows.append({'엔진':engine,'설정':str(tuple(cfg)),'누적수익률(%)':ret,'CAGR(%)':cagr,'MDD(%)':mdd,'포착률(%)':cagr/bh_cagr*100 if bh_cagr>0 else 100,'key':key})

def validate_period(px,engine,cfg,label):
    if len(px)<2:return None
    eq=run_staged(px,*cfg,capital=initial) if engine=='기존 다단계' else run_bull(px,*cfg,capital=initial)
    bh=initial*px/px.iloc[0]
    ret,cagr,mdd=metrics(eq);_,bc,bm=metrics(bh)
    return {'기간':label,'거래일':len(px),'전략 CAGR(%)':cagr,'전략 MDD(%)':mdd,'B&H CAGR(%)':bc,'B&H MDD(%)':bm,'CAGR 차이(%p)':cagr-bc,'MDD 개선(%p)':mdd-bm,'누적수익률(%)':ret}

if st.button('🚀 레거시 기준 + 3모드 최적화 실행',type='primary',use_container_width=True):
    ticker='292150.KS' if etf.startswith('TIGER') else '069500.KS';px=load_price(ticker,start,end)
    if len(px)<205:st.error('가격 데이터가 부족합니다.');st.stop()
    bh=initial*px/px.iloc[0];bh_ret,bh_cagr,bh_mdd=metrics(bh);rows=[];curves={}
    if precise:
        fasts=[40,50,60,70,80,90,100,120];slows=[100,120,140,160,180,200];w1s=[70,80,90,100];w2s=[10,20,30,40,50,70];dds=[8,10,12,15,18,20];sevs=[0,10,20,30,40];bufs=[0,1,2,3]
    else:
        fasts=[40,60,80,100,120];slows=[100,120,140,160,200];w1s=[80,90,100];w2s=[20,30,40,50,70];dds=[10,12,15,18,20];sevs=[0,20,40];bufs=[0,2]
    staged=[x for x in product(fasts,slows,w1s,w2s,dds,sevs,bufs) if x[0]<x[1] and x[3]<=x[2]]
    bull=[x for x in product([40,60,80],[120,160,200],[10,20],[80,90,100],[30,50,70],[12,15,18,20],[0,20,40],[0,2]) if x[0]<x[1] and x[4]<=x[3]]
    total=len(staged)+len(bull);bar=st.progress(0,text=f'0/{total}');n=0
    for cfg in staged:
        add_row(rows,curves,'기존 다단계',cfg,run_staged(px,*cfg),bh_cagr);n+=1
        if n%100==0:bar.progress(n/total,text=f'{n}/{total}')
    for cfg in bull:
        add_row(rows,curves,'강세장 보존',cfg,run_bull(px,*cfg),bh_cagr);n+=1
        if n%100==0 or n==total:bar.progress(n/total,text=f'{n}/{total}')
    bar.empty();df=pd.DataFrame(rows)

    results=[];selected={};selected_meta={}
    for name,limit in PROFILES.items():
        safe=df[df['MDD(%)']>=-limit].sort_values(['CAGR(%)','MDD(%)'],ascending=[False,False])
        if safe.empty:
            tmp=df.copy();tmp['초과']=(-limit-tmp['MDD(%)']).clip(lower=0);best=tmp.sort_values(['초과','CAGR(%)'],ascending=[True,False]).iloc[0];status='❌ MDD 미달'
        else:best=safe.iloc[0];status='✅ 충족'
        eq=curves[best['key']];selected[name]=eq;selected_meta[name]=best
        results.append({'모드':name,'목표MDD':-limit,'엔진':best['엔진'],'CAGR(%)':best['CAGR(%)'],'MDD(%)':best['MDD(%)'],'포착률(%)':best['포착률(%)'],'누적수익률(%)':best['누적수익률(%)'],'최종자산(원)':eq.iloc[-1],'목표충족':status})

    r=pd.DataFrame(results);st.subheader('📊 3모드 비교');st.dataframe(r.round(2),use_container_width=True,hide_index=True)
    st.caption(f'Buy & Hold: CAGR {bh_cagr:.1f}% · MDD {bh_mdd:.1f}% · 누적수익률 {bh_ret:.1f}%')
    chart={'Buy & Hold':bh};chart.update(selected);st.line_chart(pd.DataFrame(chart))

    st.subheader('🏆 MDD 구간별 실제 최고 CAGR')
    bands=[]
    for limit in [20,22,25,30]:
        q=df[df['MDD(%)']>=-limit].sort_values(['CAGR(%)','MDD(%)'],ascending=[False,False])
        if q.empty:bands.append({'MDD한도':f'-{limit}%','결과':'충족 후보 없음'})
        else:
            z=q.iloc[0];bands.append({'MDD한도':f'-{limit}%','엔진':z['엔진'],'CAGR(%)':z['CAGR(%)'],'MDD(%)':z['MDD(%)'],'포착률(%)':z['포착률(%)'],'설정':z['설정']})
    st.dataframe(pd.DataFrame(bands).round(2),use_container_width=True,hide_index=True)

    st.subheader('🧪 방어형 고정전략 기간분할 검증')
    best=selected_meta['🛡️ 방어형'];cfg=tuple(best['key'][1:]);engine=best['엔진']
    st.info(f"중요: 각 기간마다 다시 최적화하지 않고, 전체기간에서 선택된 방어형 설정 {cfg} ({engine})을 그대로 고정 적용합니다. 이것이 과최적화 여부를 보는 핵심입니다.")
    tests=[]
    years=sorted(px.index.year.unique())
    for y in years:
        sub=px[px.index.year==y]
        if len(sub)>=40:
            v=validate_period(sub,engine,cfg,str(y));
            if v:tests.append(v)
    # rolling 1-year windows from available start, non-overlapping for easy reading
    anchor=px.index.min();k=1
    while anchor<px.index.max():
        stop=min(anchor+pd.DateOffset(years=1),px.index.max())
        sub=px[(px.index>=anchor)&(px.index<=stop)]
        if len(sub)>=100:
            v=validate_period(sub,engine,cfg,f'1년구간 {k}: {anchor.date()}~{stop.date()}');
            if v:tests.append(v)
        anchor=stop+pd.Timedelta(days=1);k+=1
    val=pd.DataFrame(tests)
    if len(val):
        st.dataframe(val.round(2),use_container_width=True,hide_index=True)
        full_cagr=float(best['CAGR(%)']);yearly=val[val['기간'].astype(str).str.fullmatch(r'\d{4}')]
        positive=(yearly['CAGR 차이(%p)']>=0).sum() if len(yearly) else 0
        good_mdd=(yearly['MDD 개선(%p)']>0).sum() if len(yearly) else 0
        st.metric('연도별 B&H 대비 CAGR 우위',f'{positive}/{len(yearly)}개 연도' if len(yearly) else '-')
        st.metric('연도별 MDD 개선',f'{good_mdd}/{len(yearly)}개 연도' if len(yearly) else '-')
        if len(yearly)>=2 and positive>=max(1,len(yearly)-1) and good_mdd>=max(1,len(yearly)-1):st.success('여러 연도에서 성과가 반복됩니다. 다음 단계로 워크포워드 검증을 진행할 가치가 있습니다.')
        else:st.warning('전체기간 성과는 좋지만 기간별 일관성이 충분하지 않을 수 있습니다. 이 전략을 바로 실전 기대수익률로 해석하면 안 됩니다.')

    st.subheader('🔬 기존 우수구간 검증')
    legacy=df[(df['CAGR(%)']>=50)&(df['MDD(%)']>=-25)].sort_values(['CAGR(%)','MDD(%)'],ascending=[False,False])
    if legacy.empty:st.warning('이번 날짜/가격 데이터에서는 CAGR 50% 이상 + MDD -25% 이내 조합이 재현되지 않았습니다.')
    else:st.dataframe(legacy.head(15).drop(columns=['key']).round(2),use_container_width=True,hide_index=True)
    st.warning('과거 백테스트는 미래 수익을 보장하지 않습니다. 특히 전체기간에서 고른 파라미터를 같은 전체기간으로 평가하면 성과가 과대평가될 수 있습니다.')