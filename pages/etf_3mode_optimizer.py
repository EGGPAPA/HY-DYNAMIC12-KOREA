import pandas as pd
import streamlit as st
import yfinance as yf
from itertools import product

st.title('🎛️ ETF 3모드 최적화 · OOS 실전 후보 비교')
st.caption('공격형·균형형·방어형을 각각 워크포워드로 검증하고, OOS CAGR 하한·MDD·손실구간 수를 함께 비교합니다. 미래 데이터는 파라미터 선택에 사용하지 않습니다.')

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
st.info('OOS CAGR 하한은 사후 실전성 판정에만 사용합니다. 각 미래 검증구간의 전략은 그 시점 이전의 학습 데이터만으로 선택합니다.')

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

def equity(px,w,capital):
    w=pd.Series(w,index=px.index).shift(1).fillna(1.0)
    return capital*(1+px.pct_change().fillna(0)*w).cumprod()

def weights_staged(px,cfg):
    fast,slow,w1,w2,ddtrig,severe,buffer=cfg
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
    return out

def weights_bull(px,cfg):
    fast,slow,slope_days,weak,bear,ddtrig,severe,buffer=cfg
    mf,ms=px.rolling(fast).mean(),px.rolling(slow).mean();slope=ms.pct_change(slope_days);dd=px/px.cummax()-1
    out=[];risk=False
    for i,p in enumerate(px):
        valid=pd.notna(ms.iloc[i]) and pd.notna(slope.iloc[i]);bull=valid and p>=ms.iloc[i] and slope.iloc[i]>0
        if bull:
            risk=False;w=1.0
        else:
            if dd.iloc[i]<=-ddtrig/100:risk=True
            if risk and valid and p>=ms.iloc[i]*(1+buffer/100) and slope.iloc[i]>=0:risk=False
            if risk:w=severe/100
            elif valid and p<ms.iloc[i] and slope.iloc[i]<0:w=bear/100
            elif pd.notna(mf.iloc[i]) and p<mf.iloc[i]:w=weak/100
            else:w=1.0
        out.append(w)
    return out

def run_engine(px,engine,cfg,capital):
    w=weights_staged(px,cfg) if engine=='기존 다단계' else weights_bull(px,cfg)
    return equity(px,w,capital)

def grids(precise=False):
    if precise:
        fasts=[40,50,60,70,80,90,100,120];slows=[100,120,140,160,180,200]
        w1s=[70,80,90,100];w2s=[10,20,30,40,50,70];dds=[8,10,12,15,18,20]
        sevs=[0,10,20,30,40];bufs=[0,1,2,3]
    else:
        fasts=[40,60,80,100,120];slows=[100,120,140,160,200]
        w1s=[80,90,100];w2s=[20,30,40,50,70];dds=[10,12,15,18,20]
        sevs=[0,20,40];bufs=[0,2]
    staged=[x for x in product(fasts,slows,w1s,w2s,dds,sevs,bufs) if x[0]<x[1] and x[3]<=x[2]]
    bull=[x for x in product([40,60,80],[120,160,200],[10,20],[80,90,100],[30,50,70],[12,15,18,20],[0,20,40],[0,2]) if x[0]<x[1] and x[4]<=x[3]]
    return staged,bull

def optimize(train,limit,staged,bull,progress=None,label='최적화'):
    candidates=[];total=len(staged)+len(bull);done=0
    for engine,configs in [('기존 다단계',staged),('강세장 보존',bull)]:
        for cfg in configs:
            eq=run_engine(train,engine,cfg,1.0);_,c,m=metrics(eq)
            if m>=-limit:candidates.append((c,m,engine,cfg))
            done+=1
            if progress is not None and (done%100==0 or done==total):
                progress.progress(done/total,text=f'{label}: {done:,}/{total:,} 조합 ({done/total*100:.0f}%)')
    if not candidates:return None
    return max(candidates,key=lambda z:(z[0],z[1]))

def make_windows(px,train_days,test_days):
    windows=[];d=px.index.min()+pd.Timedelta(days=train_days)
    while d<px.index.max():
        te=min(d+pd.Timedelta(days=test_days),px.index.max())
        tr=px[(px.index>=d-pd.Timedelta(days=train_days))&(px.index<d)]
        ts=px[(px.index>=d)&(px.index<=te)]
        if len(tr)<120 or len(ts)<20:break
        windows.append((d,te));d=te+pd.Timedelta(days=1)
    return windows

def walk_forward_profile(px,profile_name,limit,staged,bull,train_days,test_days,progress=None):
    windows=make_windows(px,train_days,test_days);capital=initial;parts=[];rows=[]
    for wi,(test_start,test_end) in enumerate(windows,1):
        train_start=test_start-pd.Timedelta(days=train_days)
        train=px[(px.index>=train_start)&(px.index<test_start)]
        test=px[(px.index>=test_start)&(px.index<=test_end)]
        best=optimize(train,limit,staged,bull)
        if best is None:break
        _,_,engine,cfg=best
        history=px[(px.index>=train_start)&(px.index<=test_end)]
        wh=weights_staged(history,cfg) if engine=='기존 다단계' else weights_bull(history,cfg)
        wh=pd.Series(wh,index=history.index).shift(1).fillna(1.0)
        wr=wh.reindex(test.index).fillna(1.0)
        eq=capital*(1+test.pct_change().fillna(0)*wr).cumprod();capital=float(eq.iloc[-1]);parts.append(eq)
        _,oc,om=metrics(eq);bh=initial*test/test.iloc[0];_,bc,bm=metrics(bh)
        rows.append({'모드':profile_name,'학습기간':f'{train.index[0].date()}~{train.index[-1].date()}','미래검증':f'{test.index[0].date()}~{test.index[-1].date()}','엔진':engine,'설정':str(cfg),'OOS CAGR(%)':oc,'OOS MDD(%)':om,'B&H CAGR(%)':bc,'B&H MDD(%)':bm,'손실구간':oc<0,'검증종료자산(원)':capital})
        if progress is not None:
            progress.progress(wi/max(len(windows),1),text=f'{profile_name} 워크포워드: {wi}/{len(windows)} 구간 ({wi/max(len(windows),1)*100:.0f}%)')
    if not parts:return pd.Series(dtype=float),pd.DataFrame()
    allparts=pd.concat(parts);allparts=allparts[~allparts.index.duplicated(keep='first')]
    return allparts,pd.DataFrame(rows)

if st.button('🚀 3모드 OOS 실전 최적화 실행',type='primary',use_container_width=True):
    ticker='292150.KS' if etf.startswith('TIGER') else '069500.KS'
    mainbar=st.progress(0,text='가격 데이터 불러오는 중...');status=st.empty();px=load_price(ticker,start,end)
    if len(px)<205:
        mainbar.empty();st.error('가격 데이터가 부족합니다.');st.stop()
    staged,bull=grids(precise);bh=initial*px/px.iloc[0];bh_ret,bh_cagr,bh_mdd=metrics(bh)

    # 전체기간 참고용 3모드
    whole=[];whole_curves={}
    for idx,(name,p) in enumerate(PROFILES.items(),1):
        status.info(f'{name} 전체기간 참고 최적화 중...')
        best=optimize(px,p['mdd'],staged,bull,mainbar,f'{name} 전체기간')
        if best is None:continue
        _,_,engine,cfg=best;eq=run_engine(px,engine,cfg,initial);ret,cagr,mdd=metrics(eq);whole_curves[name]=eq
        whole.append({'모드':name,'목표MDD':-p['mdd'],'엔진':engine,'CAGR(%)':cagr,'MDD(%)':mdd,'누적수익률(%)':ret,'최종자산(원)':eq.iloc[-1]})
    mainbar.progress(1.0,text='전체기간 참고 최적화 완료 100%');status.success('전체기간 참고 최적화 완료')
    st.subheader('📊 전체기간 참고 결과');st.dataframe(pd.DataFrame(whole).round(2),use_container_width=True,hide_index=True)
    st.caption(f'Buy & Hold: CAGR {bh_cagr:.1f}% · MDD {bh_mdd:.1f}% · 누적수익률 {bh_ret:.1f}%')

    st.divider();st.header('🚶 3모드 Walk-Forward OOS 비교')
    summaries=[];detail_frames=[];curves={}
    for name,p in PROFILES.items():
        wfbar=st.progress(0,text=f'{name} 워크포워드 준비 중...')
        wf,detail=walk_forward_profile(px,name,p['mdd'],staged,bull,train_days,test_days,wfbar)
        if len(wf)<2:continue
        ret,cagr,mdd=metrics(wf);losses=int(detail['손실구간'].sum());n=len(detail);wins=int((detail['OOS CAGR(%)']>=detail['B&H CAGR(%)']).sum());mddwins=int((detail['OOS MDD(%)']>detail['B&H MDD(%)']).sum())
        summaries.append({'모드':name,'OOS CAGR(%)':cagr,'OOS MDD(%)':mdd,'손실구간':losses,'총구간':n,'B&H 대비 CAGR 우위':f'{wins}/{n}','MDD 개선구간':f'{mddwins}/{n}','최종자산(원)':wf.iloc[-1],'CAGR하한충족':cagr>=oos_cagr_floor,'모드MDD충족':mdd>=-p['mdd']})
        detail_frames.append(detail);curves[name]=wf
        wfbar.progress(1.0,text=f'{name} 워크포워드 완료 100%')

    summary=pd.DataFrame(summaries)
    if summary.empty:
        st.warning('워크포워드 검증기간이 부족합니다. 시작일을 더 앞당겨 주세요.');st.stop()
    st.subheader('🏁 OOS 실전 후보 3종')
    st.dataframe(summary.round(2),use_container_width=True,hide_index=True)
    st.line_chart(pd.DataFrame(curves))

    # 실전 우선순위: CAGR 하한 충족 → 모드 MDD 충족 → 손실구간 적음 → MDD 작음 → CAGR 높음
    rank=summary.copy()
    rank['조건점수']=rank['CAGR하한충족'].astype(int)*2+rank['모드MDD충족'].astype(int)*2
    rank=rank.sort_values(['조건점수','손실구간','OOS MDD(%)','OOS CAGR(%)'],ascending=[False,True,False,False]).reset_index(drop=True)
    best=rank.iloc[0]
    st.subheader('🥇 실전 우선 후보')
    st.success(f"{best['모드']} · OOS CAGR {best['OOS CAGR(%)']:.1f}% · OOS MDD {best['OOS MDD(%)']:.1f}% · 손실구간 {int(best['손실구간'])}/{int(best['총구간'])}")

    all_detail=pd.concat(detail_frames,ignore_index=True)
    st.subheader('📋 구간별 Out-of-Sample 상세')
    st.dataframe(all_detail.round(2),use_container_width=True,hide_index=True)

    # 최종 판정
    qualified=summary[(summary['CAGR하한충족'])&(summary['모드MDD충족'])]
    if not qualified.empty:
        q=qualified.sort_values(['손실구간','OOS MDD(%)','OOS CAGR(%)'],ascending=[True,False,False]).iloc[0]
        st.success(f"✅ 실전 채택 후보: {q['모드']}가 OOS CAGR 하한 {oos_cagr_floor}%와 해당 MDD 기준을 모두 충족했습니다. 다만 실제 투자는 소액 전진검증부터 권장합니다.")
    elif (summary['OOS CAGR(%)']>=oos_cagr_floor).any():
        st.warning('🟡 수익성은 충분하지만 MDD 목표를 동시에 충족한 모드는 없습니다. 손실구간과 MDD가 가장 낮은 후보를 중심으로 추가 방어 조정을 권장합니다.')
    else:
        st.error(f'❌ 실전 채택 보류: OOS CAGR {oos_cagr_floor}% 하한을 충족한 모드가 없습니다.')
    st.warning('워크포워드 역시 미래 수익을 보장하지 않습니다. 거래비용·세금·추적오차·체결가격을 포함하면 실제 성과는 더 낮아질 수 있습니다.')