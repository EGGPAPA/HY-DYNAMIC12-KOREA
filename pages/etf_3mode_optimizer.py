import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
from itertools import product

st.title('🎛️ ETF 3모드 최적화 · OOS 안정성 검증')
st.caption('OOS MDD 미세조정에 더해, 여러 학습기간×미래검증기간 조합에서도 방어형 성과가 반복되는지 자동 검증합니다.')

c1,c2,c3=st.columns(3)
with c1: start=st.date_input('시작일',pd.Timestamp('2023-08-19'))
with c2: end=st.date_input('종료일',pd.Timestamp.today())
with c3: initial=st.number_input('초기자금(원)',min_value=1_000_000,value=10_000_000,step=1_000_000)
etf=st.selectbox('ETF',['TIGER 코리아TOP10','KODEX200'])
precise=st.checkbox('정밀 탐색',False)

st.subheader('🎯 OOS 실전 목표')
a,b,c,d=st.columns(4)
with a: oos_cagr_floor=st.slider('OOS CAGR 하한(%)',20,90,60,5)
with b: oos_mdd_target=st.slider('OOS 최대낙폭 목표(%)',15,35,25,1)
with c: train_days=st.selectbox('기본 학습기간',[360,540,720],index=0,format_func=lambda x:f'약 {x//30}개월')
with d: test_days=st.selectbox('기본 미래검증기간',[60,90,120,180],index=2,format_func=lambda x:f'약 {x//30}개월')

PROFILES={
    '🚀 공격형':{'train_mdd':30,'selector':'max'},
    '⚖️ 균형형':{'train_mdd':25,'selector':'balanced'},
    '🛡️ 방어형':{'train_mdd':max(15,oos_mdd_target-5),'selector':'robust'},
}

if precise:
    st.warning('정밀 탐색은 후보가 많아 시간이 오래 걸립니다. 먼저 빠른 탐색으로 확인하세요.')
else:
    st.info('⚡ 빠른 탐색은 기존 우수구간 + 방어 강화 후보를 공통 계산합니다.')
st.info(f'방어형 학습 MDD 한도는 -{PROFILES["🛡️ 방어형"]["train_mdd"]}%로 적용합니다.')

@st.cache_data(ttl=1800,show_spinner=False)
def load_price(ticker,start,end):
    s,e=pd.Timestamp(start),pd.Timestamp(end)
    try:
        d=yf.download(ticker,start=str(s.date()),end=str((e+pd.Timedelta(days=1)).date()),auto_adjust=True,progress=False,threads=False,timeout=15)
        if d is None or d.empty:d=yf.download(ticker,period='max',auto_adjust=True,progress=False,threads=False,timeout=15)
        if d is None or d.empty:return pd.Series(dtype=float)
        x=d['Close'];x=x.iloc[:,0] if isinstance(x,pd.DataFrame) else x
        x=pd.to_numeric(x,errors='coerce').dropna();x.index=pd.to_datetime(x.index)
        if getattr(x.index,'tz',None) is not None:x.index=x.index.tz_localize(None)
        return x[(x.index>=s)&(x.index<=e)]
    except Exception:return pd.Series(dtype=float)

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
        fasts=[30,40,50,60,70,80,90,100,120];slows=[100,120,140,160,180,200]
        w1s=[70,80,90,100];w2s=[0,10,20,30,40,50,70];dds=[8,10,12,15,18,20];sevs=[0,10,20,30,40];bufs=[0,1,2,3]
        staged=[x for x in product(fasts,slows,w1s,w2s,dds,sevs,bufs) if x[0]<x[1] and x[3]<=x[2]]
        bull=[x for x in product([30,40,60,80],[120,140,160,200],[10,20],[80,90,100],[20,30,50],[10,12,15,18],[0,10,20,40],[0,1,2]) if x[0]<x[1] and x[4]<=x[3]]
    else:
        staged=[x for x in product([40,60,80],[120,140,160],[80,90,100],[10,20,30,40],[10,12,15,18],[0,10,20,40],[0,2]) if x[0]<x[1] and x[3]<=x[2]]
        bull=[x for x in product([40,60,80],[120,160],[10,20],[80,90,100],[20,30,50],[10,12,15,18],[0,10,20],[0,2]) if x[0]<x[1] and x[4]<=x[3]]
    return staged,bull

def context(px,staged,bull):
    close=px.to_numpy(dtype=float);dd=close/np.maximum.accumulate(close)-1.0
    ma_days=set();slope_pairs=set()
    for cfg in staged:ma_days.update([cfg[0],cfg[1]])
    for cfg in bull:ma_days.update([cfg[0],cfg[1]]);slope_pairs.add((cfg[1],cfg[2]))
    mas={day:px.rolling(day).mean().to_numpy(dtype=float) for day in ma_days}
    slopes={(slow,s):pd.Series(mas[slow],index=px.index).pct_change(s).to_numpy(dtype=float) for slow,s in slope_pairs}
    return close,dd,mas,slopes

def staged_weights(px,cfg,ctx):
    close,dd,mas,_=ctx;fast,slow,w1,w2,ddtrig,severe,buffer=cfg;mf,ms=mas[fast],mas[slow];risk=False;out=[]
    for i,p in enumerate(close):
        if dd[i]<=-ddtrig/100:risk=True
        if risk and not np.isnan(mf[i]) and not np.isnan(ms[i]) and p>=mf[i]*(1+buffer/100) and p>=ms[i]:risk=False
        if risk:w=severe/100
        elif not np.isnan(ms[i]) and p<ms[i]:w=w2/100
        elif not np.isnan(mf[i]) and p<mf[i]:w=w1/100
        else:w=1.0
        out.append(w)
    return pd.Series(out,index=px.index,dtype=float)

def bull_weights(px,cfg,ctx):
    close,dd,mas,slopes=ctx;fast,slow,slope_days,weak,bear,ddtrig,severe,buffer=cfg;mf,ms=mas[fast],mas[slow];slope=slopes[(slow,slope_days)];risk=False;out=[]
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
        out.append(w)
    return pd.Series(out,index=px.index,dtype=float)

def simulate(px,engine,cfg,ctx,capital=1.0):
    w=staged_weights(px,cfg,ctx) if engine=='기존 다단계' else bull_weights(px,cfg,ctx)
    return capital*(1+px.pct_change().fillna(0)*w.shift(1).fillna(1.0)).cumprod()

def scan_candidates(px,staged,bull,progress=None,label='후보 탐색'):
    ctx=context(px,staged,bull);rows=[];total=len(staged)+len(bull);done=0
    bh=px/px.iloc[0];_,bh_cagr,_=metrics(bh)
    for engine,configs in [('기존 다단계',staged),('강세장 보존',bull)]:
        for cfg in configs:
            eq=simulate(px,engine,cfg,ctx,1.0);_,cagr,mdd=metrics(eq);capture=cagr/bh_cagr*100 if bh_cagr>0 else 100
            rows.append({'cagr':cagr,'mdd':mdd,'capture':capture,'engine':engine,'cfg':cfg});done+=1
            if progress is not None and (done%100==0 or done==total):progress.progress(done/total,text=f'{label}: {done:,}/{total:,} 조합 ({done/total*100:.0f}%)')
    return rows,ctx

def choose_from_scan(scan,limit,selector):
    safe=[r for r in scan if r['mdd']>=-limit]
    if not safe:return None
    if selector=='max':return max(safe,key=lambda r:(r['cagr'],r['mdd']))
    best_cagr=max(r['cagr'] for r in safe)
    ratio=0.95 if selector=='balanced' else 0.90
    # 음수 CAGR 구간에서도 최고 후보가 반드시 pool에 포함되도록 절대값 기반 허용폭을 사용한다.
    threshold=best_cagr-abs(best_cagr)*(1-ratio)
    pool=[r for r in safe if r['cagr']>=threshold]
    if not pool:
        pool=[max(safe,key=lambda r:(r['cagr'],r['mdd']))]
    return max(pool,key=lambda r:(r['mdd'],r['cagr'],r['capture']))

def make_windows(px,train_days,test_days):
    windows=[];d=px.index.min()+pd.Timedelta(days=train_days)
    while d<px.index.max():
        te=min(d+pd.Timedelta(days=test_days),px.index.max());tr=px[(px.index>=d-pd.Timedelta(days=train_days))&(px.index<d)];ts=px[(px.index>=d)&(px.index<=te)]
        if len(tr)<120 or len(ts)<20:break
        windows.append((d,te));d=te+pd.Timedelta(days=1)
    return windows

def walk_forward_all(px,staged,bull,train_days,test_days,progress=None,profiles=None):
    profiles=profiles or PROFILES;windows=make_windows(px,train_days,test_days)
    capitals={name:initial for name in profiles};return_parts={name:[] for name in profiles};details=[]
    for wi,(test_start,test_end) in enumerate(windows,1):
        train_start=test_start-pd.Timedelta(days=train_days);train=px[(px.index>=train_start)&(px.index<test_start)];test=px[(px.index>=test_start)&(px.index<=test_end)]
        scan,_=scan_candidates(train,staged,bull,None)
        for name,p in profiles.items():
            best=choose_from_scan(scan,p['train_mdd'],p['selector'])
            if best is None:continue
            engine,cfg=best['engine'],best['cfg'];history=px[(px.index>=train_start)&(px.index<=test_end)]
            hctx=context(history,[cfg] if engine=='기존 다단계' else [],[cfg] if engine=='강세장 보존' else [])
            signal=staged_weights(history,cfg,hctx) if engine=='기존 다단계' else bull_weights(history,cfg,hctx)
            weight=signal.shift(1).fillna(1.0).reindex(test.index).fillna(1.0);asset_ret=history.pct_change().reindex(test.index).fillna(0.0);strategy_ret=(asset_ret*weight).astype(float)
            start_capital=capitals[name];local_eq=start_capital*(1+strategy_ret).cumprod();capitals[name]=float(local_eq.iloc[-1]);return_parts[name].append(strategy_ret)
            base_idx=train.index[-1];metric_curve=pd.concat([pd.Series([start_capital],index=[base_idx]),local_eq]);_,oc,om=metrics(metric_curve)
            bh_local=initial*(1+asset_ret).cumprod();bh_curve=pd.concat([pd.Series([initial],index=[base_idx]),bh_local]);_,bc,bm=metrics(bh_curve)
            details.append({'모드':name,'학습기간':f'{train.index[0].date()}~{train.index[-1].date()}','미래검증':f'{test.index[0].date()}~{test.index[-1].date()}','엔진':engine,'설정':str(cfg),'OOS CAGR(%)':oc,'OOS MDD(%)':om,'B&H CAGR(%)':bc,'B&H MDD(%)':bm,'손실구간':capitals[name]<start_capital,'검증시작자산(원)':start_capital,'검증종료자산(원)':capitals[name]})
        if progress is not None:progress.progress(wi/max(len(windows),1),text=f'워크포워드: {wi}/{len(windows)} 구간 완료 ({wi/max(len(windows),1)*100:.0f}%)')
    curves={}
    for name,rlist in return_parts.items():
        if not rlist:continue
        r=pd.concat(rlist);r=r[~r.index.duplicated(keep='first')].sort_index();eq=initial*(1+r).cumprod();first_idx=r.index[0];prior=px.index[px.index<first_idx];base_idx=prior[-1] if len(prior) else first_idx-pd.Timedelta(days=1);curves[name]=pd.concat([pd.Series([initial],index=[base_idx]),eq])
    return curves,pd.DataFrame(details)

def stability_validate(px,staged,bull,cagr_floor,mdd_target,progress=None):
    combos=[(360,90),(360,120),(540,120),(540,180)]
    profile={'🛡️ 방어형':PROFILES['🛡️ 방어형']};rows=[]
    for i,(tr,te) in enumerate(combos,1):
        curves,detail=walk_forward_all(px,staged,bull,tr,te,None,profile);wf=curves.get('🛡️ 방어형',pd.Series(dtype=float))
        if len(wf)>=2 and not detail.empty:
            ret,cagr,mdd=metrics(wf);d=detail[detail['모드']=='🛡️ 방어형'];losses=int(d['손실구간'].sum());n=len(d);wins=int((d['OOS CAGR(%)']>=d['B&H CAGR(%)']).sum())
            rows.append({'학습기간':f'{tr//30}개월','검증기간':f'{te//30}개월','OOS CAGR(%)':cagr,'OOS MDD(%)':mdd,'손실구간':losses,'총구간':n,'B&H CAGR 우위':f'{wins}/{n}','CAGR≥목표':cagr>=cagr_floor,'MDD≤목표':mdd>=-mdd_target,'동시충족':(cagr>=cagr_floor and mdd>=-mdd_target)})
        if progress is not None:progress.progress(i/len(combos),text=f'안정성 검증 {i}/{len(combos)} 조합 ({i/len(combos)*100:.0f}%)')
    return pd.DataFrame(rows)

if st.button('🚀 OOS MDD 미세조정 + 안정성 검증 실행',type='primary',use_container_width=True):
    ticker='292150.KS' if etf.startswith('TIGER') else '069500.KS';bar=st.progress(0,text='가격 데이터 불러오는 중...');status=st.empty();px=load_price(ticker,start,end)
    if len(px)<205:bar.empty();st.error('가격 데이터가 부족합니다.');st.stop()
    staged,bull=grids(precise);total=len(staged)+len(bull);st.caption(f'탐색 후보: {total:,}개 · 세 모드가 공통 계산 결과를 공유합니다.')
    bh=initial*px/px.iloc[0];bh_ret,bh_cagr,bh_mdd=metrics(bh)

    status.info('전체기간 공통 후보 계산 중...');scan,ctx=scan_candidates(px,staged,bull,bar,'전체기간 공통 탐색');whole=[]
    for name,p in PROFILES.items():
        best=choose_from_scan(scan,p['train_mdd'],p['selector'])
        if best is None:continue
        eq=simulate(px,best['engine'],best['cfg'],ctx,initial);ret,cagr,mdd=metrics(eq);whole.append({'모드':name,'학습MDD한도':-p['train_mdd'],'CAGR(%)':cagr,'MDD(%)':mdd,'포착률(%)':best['capture'],'최종자산(원)':eq.iloc[-1],'설정':str(best['cfg'])})
    bar.progress(1.0,text='전체기간 공통 탐색 완료 100%');status.success('전체기간 최적화 완료')
    st.subheader('📊 전체기간 참고 결과');st.dataframe(pd.DataFrame(whole).round(2),use_container_width=True,hide_index=True);st.caption(f'Buy & Hold: CAGR {bh_cagr:.1f}% · MDD {bh_mdd:.1f}% · 누적수익률 {bh_ret:.1f}%')

    st.divider();st.header('🚶 3모드 Walk-Forward OOS 비교');wfbar=st.progress(0,text='워크포워드 준비 중...');curves,detail=walk_forward_all(px,staged,bull,train_days,test_days,wfbar);wfbar.progress(1.0,text='워크포워드 완료 100%')
    summaries=[]
    for name,p in PROFILES.items():
        wf=curves.get(name,pd.Series(dtype=float));d=detail[detail['모드']==name] if not detail.empty else pd.DataFrame()
        if len(wf)<2 or d.empty:continue
        ret,cagr,mdd=metrics(wf);losses=int(d['손실구간'].sum());n=len(d);wins=int((d['OOS CAGR(%)']>=d['B&H CAGR(%)']).sum());mddwins=int((d['OOS MDD(%)']>d['B&H MDD(%)']).sum())
        summaries.append({'모드':name,'OOS CAGR(%)':cagr,'OOS MDD(%)':mdd,'손실구간':losses,'총구간':n,'B&H CAGR 우위':f'{wins}/{n}','MDD 개선':f'{mddwins}/{n}','최종자산(원)':wf.iloc[-1],'CAGR≥목표':cagr>=oos_cagr_floor,'MDD≤목표':mdd>=-oos_mdd_target})
    summary=pd.DataFrame(summaries)
    if summary.empty:st.warning('워크포워드 검증기간이 부족합니다.');st.stop()
    st.subheader('🏁 OOS 실전 후보');st.dataframe(summary.round(2),use_container_width=True,hide_index=True);st.line_chart(pd.DataFrame(curves))
    passed=summary[(summary['CAGR≥목표'])&(summary['MDD≤목표'])].copy()
    if not passed.empty:
        passed=passed.sort_values(['손실구간','OOS MDD(%)','OOS CAGR(%)'],ascending=[True,False,False]);best=passed.iloc[0];st.success(f"✅ 목표 달성 후보: {best['모드']} · OOS CAGR {best['OOS CAGR(%)']:.1f}% · OOS MDD {best['OOS MDD(%)']:.1f}%")
    else:
        st.warning('🟡 기본 OOS 구간에서는 목표 동시충족 후보가 없습니다.')

    st.divider();st.header('🧪 OOS 안정성 검증 · 방어형')
    st.info('방어형을 학습 12개월→검증 3개월, 12→4개월, 18→4개월, 18→6개월로 다시 워크포워드 검증합니다. 각 조합에서도 미래 데이터는 파라미터 선택에 쓰지 않습니다.')
    stabbar=st.progress(0,text='안정성 검증 준비 중...');stab=stability_validate(px,staged,bull,oos_cagr_floor,oos_mdd_target,stabbar);stabbar.progress(1.0,text='안정성 검증 완료 100%')
    if stab.empty:
        st.warning('안정성 검증에 필요한 데이터가 부족합니다.')
    else:
        st.dataframe(stab.round(2),use_container_width=True,hide_index=True);passed_n=int(stab['동시충족'].sum());total_n=len(stab);rate=passed_n/total_n*100
        c1,c2,c3=st.columns(3);c1.metric('동시충족',f'{passed_n}/{total_n} 조합');c2.metric('안정성 통과율',f'{rate:.0f}%');c3.metric('평균 OOS MDD',f"{stab['OOS MDD(%)'].mean():.1f}%")
        if passed_n>=3:st.success('✅ 안정성 우수: 대부분의 학습·검증기간 조합에서 CAGR/MDD 목표를 동시에 충족했습니다.')
        elif passed_n>=2:st.warning('🟡 안정성 보통: 절반 정도의 조합에서 목표를 충족했습니다. 소액 전진검증이 필요합니다.')
        else:st.error('❌ 안정성 부족: 특정 기간설정에 민감합니다. 실전 채택은 보류하는 것이 좋습니다.')

    st.subheader('📋 기본 구간별 Out-of-Sample 결과');st.dataframe(detail.round(2),use_container_width=True,hide_index=True)
    st.warning('워크포워드와 안정성 검증도 미래 성과를 보장하지 않습니다. 거래비용·세금·추적오차를 반영하면 실제 성과는 더 낮아질 수 있습니다.')