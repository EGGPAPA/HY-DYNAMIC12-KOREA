import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
from itertools import product

st.title('🎛️ ETF 3모드 최적화 · OOS 실전 후보 비교')
st.caption('공격형·균형형·방어형을 같은 후보 계산 결과에서 동시에 선택해 중복 계산을 줄였습니다. 빠른 탐색은 과거 우수구간 주변만 집중 탐색합니다.')

c1,c2,c3=st.columns(3)
with c1: start=st.date_input('시작일',pd.Timestamp('2023-08-19'))
with c2: end=st.date_input('종료일',pd.Timestamp.today())
with c3: initial=st.number_input('초기자금(원)',min_value=1_000_000,value=10_000_000,step=1_000_000)
etf=st.selectbox('ETF',['TIGER 코리아TOP10','KODEX200'])
precise=st.checkbox('정밀 탐색',False)

PROFILES={
    '🚀 공격형':{'mdd':30},
    '⚖️ 균형형':{'mdd':25},
    '🛡️ 방어형':{'mdd':22},
}

st.subheader('🎯 OOS 실전 판정 기준')
a,b,c=st.columns(3)
with a: oos_cagr_floor=st.slider('OOS CAGR 하한(%)',20,80,55,5)
with b: train_days=st.selectbox('학습기간',[360,540,720],index=0,format_func=lambda x:f'약 {x//30}개월')
with c: test_days=st.selectbox('미래 검증기간',[60,90,120,180],index=2,format_func=lambda x:f'약 {x//30}개월')

if precise:
    st.warning('정밀 탐색은 후보가 많아 시간이 오래 걸립니다. 일반 확인은 빠른 탐색을 권장합니다.')
else:
    st.info('⚡ 빠른 탐색: 과거 우수 설정 주변을 집중 탐색하고, 공격형·균형형·방어형이 계산 결과를 공유합니다. 기존보다 크게 빨라집니다.')

@st.cache_data(ttl=1800,show_spinner=False)
def load_price(ticker,start,end):
    s,e=pd.Timestamp(start),pd.Timestamp(end)
    try:
        d=yf.download(ticker,start=str(s.date()),end=str((e+pd.Timedelta(days=1)).date()),auto_adjust=True,progress=False,threads=False,timeout=15)
        if d is None or d.empty:
            d=yf.download(ticker,period='max',auto_adjust=True,progress=False,threads=False,timeout=15)
        if d is None or d.empty:return pd.Series(dtype=float)
        x=d['Close'];x=x.iloc[:,0] if isinstance(x,pd.DataFrame) else x
        x=pd.to_numeric(x,errors='coerce').dropna();x.index=pd.to_datetime(x.index)
        if getattr(x.index,'tz',None) is not None:x.index=x.index.tz_localize(None)
        return x[(x.index>=s)&(x.index<=e)]
    except Exception:
        return pd.Series(dtype=float)

def metrics(eq):
    eq=pd.to_numeric(eq,errors='coerce').dropna()
    if len(eq)<2:return 0.,0.,0.
    years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    total=(eq.iloc[-1]/eq.iloc[0]-1)*100
    cagr=((eq.iloc[-1]/eq.iloc[0])**(1/years)-1)*100
    mdd=(eq/eq.cummax()-1).min()*100
    return total,cagr,mdd

def grids(precise=False):
    if precise:
        fasts=[40,50,60,70,80,90,100,120];slows=[100,120,140,160,180,200]
        w1s=[70,80,90,100];w2s=[10,20,30,40,50,70];dds=[8,10,12,15,18,20]
        sevs=[0,10,20,30,40];bufs=[0,1,2,3]
        staged=[x for x in product(fasts,slows,w1s,w2s,dds,sevs,bufs) if x[0]<x[1] and x[3]<=x[2]]
        bull=[x for x in product([40,60,80],[120,160,200],[10,20],[80,90,100],[30,50,70],[12,15,18,20],[0,20,40],[0,2]) if x[0]<x[1] and x[4]<=x[3]]
    else:
        # 과거 실제 우수 설정: 40/160/100/20/15/0/0, 60/140/100/20/15/20/2 등을 반드시 포함
        staged=[x for x in product(
            [40,60,80], [120,140,160], [90,100], [20,30,40],
            [12,15,18], [0,20,40], [0,2]
        ) if x[0]<x[1] and x[3]<=x[2]]
        bull=[x for x in product(
            [40,60,80], [120,160], [10,20], [90,100], [30,50],
            [12,15,18], [0,20], [0,2]
        ) if x[0]<x[1] and x[4]<=x[3]]
    return staged,bull

def context(px,staged,bull):
    close=px.to_numpy(dtype=float)
    ret=px.pct_change().fillna(0).to_numpy(dtype=float)
    dd=close/np.maximum.accumulate(close)-1.0
    ma_days=set()
    slope_pairs=set()
    for cfg in staged:
        ma_days.update([cfg[0],cfg[1]])
    for cfg in bull:
        ma_days.update([cfg[0],cfg[1]]);slope_pairs.add((cfg[1],cfg[2]))
    mas={d:px.rolling(d).mean().to_numpy(dtype=float) for d in ma_days}
    slopes={(slow,s):pd.Series(mas[slow],index=px.index).pct_change(s).to_numpy(dtype=float) for slow,s in slope_pairs}
    return close,ret,dd,mas,slopes

def simulate_staged(px,cfg,ctx,capital=1.0):
    close,ret,dd,mas,_=ctx
    fast,slow,w1,w2,ddtrig,severe,buffer=cfg
    mf,ms=mas[fast],mas[slow];risk=False;wealth=capital;vals=[]
    peak=capital
    for i,p in enumerate(close):
        if dd[i]<=-ddtrig/100:risk=True
        if risk and not np.isnan(mf[i]) and not np.isnan(ms[i]) and p>=mf[i]*(1+buffer/100) and p>=ms[i]:risk=False
        if risk:w=severe/100
        elif not np.isnan(ms[i]) and p<ms[i]:w=w2/100
        elif not np.isnan(mf[i]) and p<mf[i]:w=w1/100
        else:w=1.0
        if i>0:wealth*=1+ret[i]*prev_w
        vals.append(wealth);prev_w=w
    return pd.Series(vals,index=px.index)

def simulate_bull(px,cfg,ctx,capital=1.0):
    close,ret,dd,mas,slopes=ctx
    fast,slow,slope_days,weak,bear,ddtrig,severe,buffer=cfg
    mf,ms=mas[fast],mas[slow];slope=slopes[(slow,slope_days)];risk=False;wealth=capital;vals=[]
    for i,p in enumerate(close):
        valid=not np.isnan(ms[i]) and not np.isnan(slope[i]);bull=valid and p>=ms[i] and slope[i]>0
        if bull:risk=False;w=1.0
        else:
            if dd[i]<=-ddtrig/100:risk=True
            if risk and valid and p>=ms[i]*(1+buffer/100) and slope[i]>=0:risk=False
            if risk:w=severe/100
            elif valid and p<ms[i] and slope[i]<0:w=bear/100
            elif not np.isnan(mf[i]) and p<mf[i]:w=weak/100
            else:w=1.0
        if i>0:wealth*=1+ret[i]*prev_w
        vals.append(wealth);prev_w=w
    return pd.Series(vals,index=px.index)

def scan_candidates(px,staged,bull,progress=None,label='후보 탐색'):
    ctx=context(px,staged,bull);rows=[];total=len(staged)+len(bull);done=0
    for engine,configs in [('기존 다단계',staged),('강세장 보존',bull)]:
        for cfg in configs:
            eq=simulate_staged(px,cfg,ctx,1.0) if engine=='기존 다단계' else simulate_bull(px,cfg,ctx,1.0)
            _,cagr,mdd=metrics(eq);rows.append((cagr,mdd,engine,cfg));done+=1
            if progress is not None and (done%100==0 or done==total):
                progress.progress(done/total,text=f'{label}: {done:,}/{total:,} 조합 ({done/total*100:.0f}%)')
    return rows,ctx

def choose_from_scan(scan,limit):
    safe=[r for r in scan if r[1]>=-limit]
    if not safe:return None
    return max(safe,key=lambda z:(z[0],z[1]))

def make_windows(px,train_days,test_days):
    windows=[];d=px.index.min()+pd.Timedelta(days=train_days)
    while d<px.index.max():
        te=min(d+pd.Timedelta(days=test_days),px.index.max())
        tr=px[(px.index>=d-pd.Timedelta(days=train_days))&(px.index<d)]
        ts=px[(px.index>=d)&(px.index<=te)]
        if len(tr)<120 or len(ts)<20:break
        windows.append((d,te));d=te+pd.Timedelta(days=1)
    return windows

def walk_forward_all(px,staged,bull,train_days,test_days,progress=None):
    windows=make_windows(px,train_days,test_days)
    capitals={name:initial for name in PROFILES};parts={name:[] for name in PROFILES};details=[]
    for wi,(test_start,test_end) in enumerate(windows,1):
        train_start=test_start-pd.Timedelta(days=train_days)
        train=px[(px.index>=train_start)&(px.index<test_start)]
        test=px[(px.index>=test_start)&(px.index<=test_end)]
        scan,_=scan_candidates(train,staged,bull,None)
        for name,p in PROFILES.items():
            best=choose_from_scan(scan,p['mdd'])
            if best is None:continue
            _,_,engine,cfg=best
            history=px[(px.index>=train_start)&(px.index<=test_end)]
            hctx=context(history,[cfg] if engine=='기존 다단계' else [],[cfg] if engine=='강세장 보존' else [])
            heq=simulate_staged(history,cfg,hctx,capitals[name]) if engine=='기존 다단계' else simulate_bull(history,cfg,hctx,capitals[name])
            eq=heq.reindex(test.index).dropna();capitals[name]=float(eq.iloc[-1]);parts[name].append(eq)
            _,oc,om=metrics(eq);bh=initial*test/test.iloc[0];_,bc,bm=metrics(bh)
            details.append({'모드':name,'학습기간':f'{train.index[0].date()}~{train.index[-1].date()}','미래검증':f'{test.index[0].date()}~{test.index[-1].date()}','엔진':engine,'설정':str(cfg),'OOS CAGR(%)':oc,'OOS MDD(%)':om,'B&H CAGR(%)':bc,'B&H MDD(%)':bm,'손실구간':oc<0,'검증종료자산(원)':capitals[name]})
        if progress is not None:
            progress.progress(wi/max(len(windows),1),text=f'워크포워드: {wi}/{len(windows)} 구간 완료 ({wi/max(len(windows),1)*100:.0f}%)')
    curves={}
    for name,plist in parts.items():
        if plist:
            s=pd.concat(plist);curves[name]=s[~s.index.duplicated(keep='first')]
    return curves,pd.DataFrame(details)

if st.button('🚀 3모드 OOS 실전 최적화 실행',type='primary',use_container_width=True):
    ticker='292150.KS' if etf.startswith('TIGER') else '069500.KS'
    mainbar=st.progress(0,text='가격 데이터 불러오는 중...');status=st.empty();px=load_price(ticker,start,end)
    if len(px)<205:
        mainbar.empty();st.error('가격 데이터가 부족합니다.');st.stop()
    staged,bull=grids(precise);total=len(staged)+len(bull)
    st.caption(f"탐색 후보: {total:,}개 · 3개 모드가 이 계산 결과를 공유합니다.")
    bh=initial*px/px.iloc[0];bh_ret,bh_cagr,bh_mdd=metrics(bh)

    status.info('전체기간 후보를 한 번만 계산 중...')
    scan,ctx=scan_candidates(px,staged,bull,mainbar,'전체기간 공통 탐색')
    whole=[];whole_curves={}
    for name,p in PROFILES.items():
        best=choose_from_scan(scan,p['mdd'])
        if best is None:continue
        _,_,engine,cfg=best
        eq=simulate_staged(px,cfg,ctx,initial) if engine=='기존 다단계' else simulate_bull(px,cfg,ctx,initial)
        ret,cagr,mdd=metrics(eq);whole_curves[name]=eq
        whole.append({'모드':name,'목표MDD':-p['mdd'],'엔진':engine,'CAGR(%)':cagr,'MDD(%)':mdd,'누적수익률(%)':ret,'최종자산(원)':eq.iloc[-1],'설정':str(cfg)})
    mainbar.progress(1.0,text='전체기간 공통 탐색 완료 100%');status.success('전체기간 최적화 완료')
    st.subheader('📊 전체기간 참고 결과');st.dataframe(pd.DataFrame(whole).round(2),use_container_width=True,hide_index=True)
    st.caption(f'Buy & Hold: CAGR {bh_cagr:.1f}% · MDD {bh_mdd:.1f}% · 누적수익률 {bh_ret:.1f}%')

    st.divider();st.header('🚶 3모드 Walk-Forward OOS 비교')
    wfbar=st.progress(0,text='워크포워드 준비 중...')
    curves,detail=walk_forward_all(px,staged,bull,train_days,test_days,wfbar)
    wfbar.progress(1.0,text='워크포워드 전체 완료 100%')
    summaries=[]
    for name,p in PROFILES.items():
        wf=curves.get(name,pd.Series(dtype=float));d=detail[detail['모드']==name] if not detail.empty else pd.DataFrame()
        if len(wf)<2 or d.empty:continue
        ret,cagr,mdd=metrics(wf);losses=int(d['손실구간'].sum());n=len(d);wins=int((d['OOS CAGR(%)']>=d['B&H CAGR(%)']).sum());mddwins=int((d['OOS MDD(%)']>d['B&H MDD(%)']).sum())
        summaries.append({'모드':name,'OOS CAGR(%)':cagr,'OOS MDD(%)':mdd,'손실구간':losses,'총구간':n,'B&H 대비 CAGR 우위':f'{wins}/{n}','MDD 개선구간':f'{mddwins}/{n}','최종자산(원)':wf.iloc[-1],'CAGR하한충족':cagr>=oos_cagr_floor,'모드MDD충족':mdd>=-p['mdd']})
    summary=pd.DataFrame(summaries)
    if summary.empty:st.warning('워크포워드 검증기간이 부족합니다.');st.stop()
    st.subheader('🏁 OOS 실전 후보 3종');st.dataframe(summary.round(2),use_container_width=True,hide_index=True);st.line_chart(pd.DataFrame(curves))
    rank=summary.copy();rank['조건점수']=rank['CAGR하한충족'].astype(int)*2+rank['모드MDD충족'].astype(int)*2;rank=rank.sort_values(['조건점수','손실구간','OOS MDD(%)','OOS CAGR(%)'],ascending=[False,True,False,False]).reset_index(drop=True);best=rank.iloc[0]
    st.subheader('🥇 실전 우선 후보');st.success(f"{best['모드']} · OOS CAGR {best['OOS CAGR(%)']:.1f}% · OOS MDD {best['OOS MDD(%)']:.1f}% · 손실 {int(best['손실구간'])}/{int(best['총구간'])} 구간")
    st.subheader('📋 구간별 Out-of-Sample 결과');st.dataframe(detail.round(2),use_container_width=True,hide_index=True)
    st.warning('과거 및 워크포워드 결과는 미래 수익을 보장하지 않습니다. 실제 거래비용·세금·추적오차를 반영하면 결과는 낮아질 수 있습니다.')