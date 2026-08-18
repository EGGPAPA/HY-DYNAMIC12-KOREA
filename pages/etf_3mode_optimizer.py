import pandas as pd
import streamlit as st
import yfinance as yf
from itertools import product

st.title('🎛️ ETF 3모드 최적화 · 워크포워드 검증')
st.caption('전체기간 최적화와 별도로, 과거 구간에서만 전략을 선택하고 다음 미래 구간에 고정 적용하는 Out-of-Sample 검증을 수행합니다.')

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
        x=d['Close'];x=x.iloc[:,0] if isinstance(x,pd.DataFrame) else x
        x=pd.to_numeric(x,errors='coerce').dropna();x.index=pd.to_datetime(x.index)
        if getattr(x.index,'tz',None) is not None:x.index=x.index.tz_localize(None)
        return x[(x.index>=s)&(x.index<=e)]
    except Exception:return pd.Series(dtype=float)

def metrics(eq):
    if len(eq)<2:return 0.,0.,0.
    years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    return (eq.iloc[-1]/eq.iloc[0]-1)*100,((eq.iloc[-1]/eq.iloc[0])**(1/years)-1)*100,(eq/eq.cummax()-1).min()*100

def equity(px,w,capital):
    w=pd.Series(w,index=px.index).shift(1).fillna(1.0)
    return capital*(1+px.pct_change().fillna(0)*w).cumprod()

def weights_staged(px,cfg):
    fast,slow,w1,w2,ddtrig,severe,buffer=cfg;mf,ms=px.rolling(fast).mean(),px.rolling(slow).mean();dd=px/px.cummax()-1;out=[];risk=False
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
    fast,slow,slope_days,weak,bear,ddtrig,severe,buffer=cfg;mf,ms=px.rolling(fast).mean(),px.rolling(slow).mean();slope=ms.pct_change(slope_days);dd=px/px.cummax()-1;out=[];risk=False
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
    return out

def run_engine(px,engine,cfg,capital):return equity(px,weights_staged(px,cfg) if engine=='기존 다단계' else weights_bull(px,cfg),capital)

def grids(precise=False):
    if precise:
        fasts=[40,50,60,70,80,90,100,120];slows=[100,120,140,160,180,200];w1s=[70,80,90,100];w2s=[10,20,30,40,50,70];dds=[8,10,12,15,18,20];sevs=[0,10,20,30,40];bufs=[0,1,2,3]
    else:
        fasts=[40,60,80,100,120];slows=[100,120,140,160,200];w1s=[80,90,100];w2s=[20,30,40,50,70];dds=[10,12,15,18,20];sevs=[0,20,40];bufs=[0,2]
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
            if progress is not None and (done%100==0 or done==total):progress.progress(done/total,text=f'{label}: {done:,}/{total:,} 조합 ({done/total*100:.0f}%)')
    if not candidates:return None
    return max(candidates,key=lambda z:(z[0],z[1]))

def walk_forward(px,limit,staged,bull,train_days=360,test_days=120,progress=None):
    start_date=px.index.min()+pd.Timedelta(days=train_days);capital=initial;parts=[];rows=[];windows=[];d=start_date
    while d<px.index.max():
        te=min(d+pd.Timedelta(days=test_days),px.index.max());tr=px[(px.index>=d-pd.Timedelta(days=train_days))&(px.index<d)];ts=px[(px.index>=d)&(px.index<=te)]
        if len(tr)<120 or len(ts)<20:break
        windows.append((d,te));d=te+pd.Timedelta(days=1)
    for wi,(start_date,test_end) in enumerate(windows,1):
        train_start=start_date-pd.Timedelta(days=train_days);train=px[(px.index>=train_start)&(px.index<start_date)];test=px[(px.index>=start_date)&(px.index<=test_end)]
        best=optimize(train,limit,staged,bull)
        if best is None:break
        _,_,engine,cfg=best;history=px[(px.index>=train_start)&(px.index<=test_end)];w=weights_staged(history,cfg) if engine=='기존 다단계' else weights_bull(history,cfg);w=pd.Series(w,index=history.index).shift(1).fillna(1.0);wr=w.reindex(test.index).fillna(1.0);rets=test.pct_change().fillna(0);eq=capital*(1+rets*wr).cumprod();capital=float(eq.iloc[-1]);parts.append(eq)
        _,tc,tm=metrics(eq);bh=initial*test/test.iloc[0];_,bc,bm=metrics(bh);rows.append({'학습기간':f'{train.index[0].date()}~{train.index[-1].date()}','미래검증':f'{test.index[0].date()}~{test.index[-1].date()}','엔진':engine,'설정':str(cfg),'OOS CAGR(%)':tc,'OOS MDD(%)':tm,'B&H CAGR(%)':bc,'B&H MDD(%)':bm,'검증종료자산(원)':capital})
        if progress is not None:progress.progress(wi/max(len(windows),1),text=f'워크포워드: {wi}/{len(windows)} 구간 완료 ({wi/max(len(windows),1)*100:.0f}%)')
    if not parts:return pd.Series(dtype=float),pd.DataFrame()
    allparts=pd.concat(parts);return allparts[~allparts.index.duplicated(keep='first')],pd.DataFrame(rows)

if st.button('🚀 3모드 최적화 + 워크포워드 실행',type='primary',use_container_width=True):
    ticker='292150.KS' if etf.startswith('TIGER') else '069500.KS';status=st.empty();mainbar=st.progress(0,text='가격 데이터 불러오는 중...');px=load_price(ticker,start,end)
    if len(px)<205:mainbar.empty();st.error('가격 데이터가 부족합니다.');st.stop()
    staged,bull=grids(precise);bh=initial*px/px.iloc[0];bh_ret,bh_cagr,bh_mdd=metrics(bh);results=[];selected={};meta={}
    for idx,(name,limit) in enumerate(PROFILES.items(),1):
        status.info(f'{name} 전체기간 최적화 계산 중...');best=optimize(px,limit,staged,bull,mainbar,f'{name} 탐색')
        if best is None:continue
        c,m,engine,cfg=best;eq=run_engine(px,engine,cfg,initial);ret,cagr,mdd=metrics(eq);selected[name]=eq;meta[name]=(engine,cfg);results.append({'모드':name,'목표MDD':-limit,'엔진':engine,'CAGR(%)':cagr,'MDD(%)':mdd,'포착률(%)':cagr/bh_cagr*100 if bh_cagr>0 else 100,'누적수익률(%)':ret,'최종자산(원)':eq.iloc[-1],'목표충족':'✅ 충족'})
    mainbar.progress(1.0,text='전체기간 3모드 최적화 완료 100%');status.success('전체기간 최적화 완료')
    st.subheader('📊 전체기간 3모드 비교');st.dataframe(pd.DataFrame(results).round(2),use_container_width=True,hide_index=True);st.caption(f'Buy & Hold: CAGR {bh_cagr:.1f}% · MDD {bh_mdd:.1f}% · 누적수익률 {bh_ret:.1f}%');chart={'Buy & Hold':bh};chart.update(selected);st.line_chart(pd.DataFrame(chart))
    st.divider();st.header('🚶 워크포워드 실전 검증');st.info('각 검증구간 시작 전에 이용 가능했던 과거 데이터만으로 방어형(MDD -22%) 전략을 다시 선택하고, 다음 미래구간에는 설정을 고정합니다. 미래 데이터를 보고 파라미터를 고르지 않습니다.')
    a,b=st.columns(2)
    with a: train_days=st.selectbox('학습기간',[360,540,720],index=0,format_func=lambda x:f'약 {x//30}개월')
    with b: test_days=st.selectbox('미래 검증기간',[60,90,120,180],index=2,format_func=lambda x:f'약 {x//30}개월')
    wfbar=st.progress(0,text='워크포워드 검증 준비 중...');wf,wft=walk_forward(px,22,staged,bull,train_days,test_days,wfbar)
    if len(wf)<2:st.warning('워크포워드 검증에 필요한 기간이 부족합니다. 시작일을 더 앞당겨 주세요.')
    else:
        wfbar.progress(1.0,text='워크포워드 검증 완료 100%');wf_ret,wf_cagr,wf_mdd=metrics(wf);bh_oos=initial*px.reindex(wf.index)/px.reindex(wf.index).iloc[0];bo_ret,bo_cagr,bo_mdd=metrics(bh_oos);fixed=selected.get('🛡️ 방어형',pd.Series(dtype=float));fixed_oos=fixed.reindex(wf.index).dropna();fx_ret,fx_cagr,fx_mdd=metrics(fixed_oos) if len(fixed_oos)>1 else (0,0,0)
        comp=pd.DataFrame([{'전략':'Buy & Hold (동일 OOS)','CAGR(%)':bo_cagr,'MDD(%)':bo_mdd,'누적수익률(%)':bo_ret,'최종 1,000만원':initial*(1+bo_ret/100)},{'전략':'전체기간 선택 방어형','CAGR(%)':fx_cagr,'MDD(%)':fx_mdd,'누적수익률(%)':fx_ret,'최종 1,000만원':initial*(1+fx_ret/100)},{'전략':'Walk-Forward OOS','CAGR(%)':wf_cagr,'MDD(%)':wf_mdd,'누적수익률(%)':wf_ret,'최종 1,000만원':wf.iloc[-1]}]);st.subheader('🏁 실전성 비교');st.dataframe(comp.round(2),use_container_width=True,hide_index=True);st.line_chart(pd.DataFrame({'Walk-Forward OOS':wf,'Buy & Hold OOS':bh_oos}).dropna());st.subheader('📋 구간별 Out-of-Sample 결과');st.dataframe(wft.round(2),use_container_width=True,hide_index=True)
        wins=int((wft['OOS CAGR(%)']>=wft['B&H CAGR(%)']).sum());mddwins=int((wft['OOS MDD(%)']>wft['B&H MDD(%)']).sum());n=len(wft);c1,c2,c3=st.columns(3);c1.metric('OOS CAGR',f'{wf_cagr:.1f}%');c2.metric('OOS MDD',f'{wf_mdd:.1f}%');c3.metric('B&H 대비 CAGR 우위',f'{wins}/{n} 구간')
        if wf_mdd>=-22 and wf_cagr>=bo_cagr*0.9 and mddwins>=max(1,n//2):st.success('✅ 실전 채택 후보: OOS에서도 목표 MDD를 지키면서 Buy & Hold 수익의 90% 이상을 유지했습니다.')
        elif wf_mdd>=-25 and wf_cagr>=bo_cagr*0.75:st.warning('🟡 보류/개선: 방어 효과는 있으나 수익 포착력이 아직 부족합니다.')
        else:st.error('❌ 실전 채택 보류: Out-of-Sample 결과가 충분히 안정적이지 않습니다.')
    st.warning('워크포워드도 미래 수익을 보장하지 않습니다. 거래비용·세금·추적오차·체결가격을 포함하면 실제 성과는 더 낮아질 수 있습니다.')