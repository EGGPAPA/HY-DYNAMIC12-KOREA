import numpy as np
import pandas as pd
import streamlit as st
from theme_styles import inject_theme
import yfinance as yf
from itertools import product
import json
from pathlib import Path

inject_theme()

st.title('🧪 ETF 전략검증 · 3MODE OOS')
st.caption('ETF 3MODE의 기간 안정성, 거래비용, 체결지연과 OOS 성과를 한 화면에서 검증합니다.')

c1,c2,c3=st.columns(3)
with c1: start=st.date_input('시작일',pd.Timestamp('2023-08-19'))
with c2: end=st.date_input('종료일',pd.Timestamp.today())
with c3: initial=st.number_input('초기자금(원)',min_value=1_000_000,value=10_000_000,step=1_000_000)
ETF_CANDIDATES={
    'TIGER 코리아TOP10':{'ticker':'292150.KS','theme':'대형주','holdings':['삼성전자','SK하이닉스','현대차','기아','NAVER','셀트리온','LG에너지솔루션','KB금융','신한지주','POSCO홀딩스']},
    'KODEX200':{'ticker':'069500.KS','theme':'시장대표','holdings':['삼성전자','SK하이닉스','현대차','기아','셀트리온','KB금융','신한지주','POSCO홀딩스','삼성바이오로직스','LG에너지솔루션']},
    'KODEX 반도체':{'ticker':'091160.KS','theme':'반도체','holdings':['SK하이닉스','삼성전자','한미반도체','리노공업','HPSP','이오테크닉스','DB하이텍']},
    'KODEX 자동차':{'ticker':'091180.KS','theme':'자동차','holdings':['현대차','기아','현대모비스','한온시스템','금호타이어','HL만도','DN오토모티브']},
    'TIGER 헬스케어':{'ticker':'143860.KS','theme':'헬스케어','holdings':['삼성바이오로직스','셀트리온','알테오젠','유한양행','리가켐바이오','한미약품']},
    'TIGER 2차전지테마':{'ticker':'305540.KS','theme':'2차전지','holdings':['LG에너지솔루션','삼성SDI','포스코퓨처엠','에코프로비엠','엘앤에프','SK이노베이션']},
    'KODEX K-방산':{'ticker':'449450.KS','theme':'방산','holdings':['한화에어로스페이스','현대로템','LIG넥스원','한국항공우주','한화시스템']},
    'KODEX 조선TOP3플러스':{'ticker':'466920.KS','theme':'조선','holdings':['HD한국조선해양','HD현대중공업','한화오션','삼성중공업','HD현대미포']},
}
etf=st.selectbox('ETF',list(ETF_CANDIDATES))
precise=st.checkbox('정밀 탐색',False)

st.subheader('🎯 OOS 실전 목표')
a,b,c,d=st.columns(4)
with a: oos_cagr_floor=st.slider('OOS CAGR 하한(%)',20,90,60,5)
with b: oos_mdd_target=st.slider('OOS 최대낙폭 목표(%)',15,35,25,1)
with c: train_days=st.selectbox('기본 학습기간',[360,540,720],index=0,format_func=lambda x:f'약 {x//30}개월')
with d: test_days=st.selectbox('기본 미래검증기간',[60,90,120,180],index=2,format_func=lambda x:f'약 {x//30}개월')

st.subheader('💸 실전 비용 스트레스 테스트')
e,f,g=st.columns(3)
with e: fee_bps=st.number_input('매매 수수료 (bp, 편도)',0.0,50.0,1.5,0.5)
with f: slippage_bps=st.number_input('슬리피지 (bp, 편도)',0.0,100.0,2.0,0.5)
with g: sell_tax_bps=st.number_input('매도 세금/기타비용 (bp)',0.0,100.0,0.0,1.0)
st.caption('1bp=0.01%. 기본 비용뿐 아니라 2배·3배 비용과 체결 1거래일 추가 지연도 마지막 단계에서 자동 검증합니다.')

PROFILES={'🚀 공격형':{'train_mdd':30,'selector':'max'},'⚖️ 균형형':{'train_mdd':25,'selector':'balanced'},'🛡️ 방어형':{'train_mdd':max(15,oos_mdd_target-5),'selector':'robust'}}
if precise: st.warning('정밀 탐색은 후보가 많아 시간이 오래 걸립니다. 먼저 빠른 탐색으로 확인하세요.')
else: st.info('⚡ 빠른 탐색은 기존 우수 설정 주변 압축 후보를 계산합니다.')
st.info(f'방어형 학습 MDD 한도는 -{PROFILES["🛡️ 방어형"]["train_mdd"]}%로 적용합니다.')


def _row_names(value):
    names=[]
    if isinstance(value,pd.DataFrame): value=value.to_dict('records')
    if isinstance(value,dict): value=[value]
    if not isinstance(value,(list,tuple)): return names
    for row in value:
        if isinstance(row,str): names.append(row.strip());continue
        if not isinstance(row,dict): continue
        for key in ('종목명','종목','name','ticker_name'):
            name=str(row.get(key,'')).strip()
            if name and name not in ('-', 'None', 'nan'): names.append(name);break
    return names

def _leader_signals():
    sources={}
    aliases={
        'TOP12':('kr_rows','top12_rows','kr_top12','top12'),
        '부의 점프':('wealth_jump_rows','kr_wealth_rows','wealth_rows','jump_rows'),
        '5개월선':('kr_ma5_rows','ma5_rows','breakout_rows','kr_breakout_rows'),
    }
    for label,keys in aliases.items():
        for key in keys:
            names=_row_names(st.session_state.get(key))
            if names:
                sources[label]=names[:12];break
    if not sources:
        try:
            raw=json.loads(Path('data/korea_analysis.json').read_text(encoding='utf-8'))
            names=_row_names(raw.get('rows',raw) if isinstance(raw,dict) else raw)
            if names:sources['최근 전체시장 분석']=names[:12]
        except Exception:pass
    return sources

def _normal_name(name):
    return str(name).upper().replace(' ','').replace('㈜','').replace('주식회사','')

def _etf_score(name,leaders,px):
    info=ETF_CANDIDATES[name];holding_norm={_normal_name(x) for x in info['holdings']}
    matched=[x for x in leaders if _normal_name(x) in holding_norm]
    overlap=60*len(set(map(_normal_name,matched)))/max(len(set(map(_normal_name,leaders))),1)
    momentum=ma_score=0.
    if len(px)>=65:
        momentum=float(np.clip((px.iloc[-1]/px.iloc[-61]-1)*100,-10,20));momentum=(momentum+10)/30*15
    monthly=_monthly_from_daily(px)
    if len(monthly)>=5:
        ma=float(monthly.rolling(5).mean().iloc[-1]);ma_score=15 if px.iloc[-1]>=ma else 0
    return round(overlap+momentum+ma_score,1),matched,round(momentum,1),round(ma_score,1)

def render_leader_etf_rotation():
    st.divider();st.header('🏆 주도주 포함 ETF 동적 로테이션')
    st.caption('TOP12·부의 점프·5개월선 결과가 바뀌면 ETF 중복도도 다시 계산합니다. 10점 이상 우위가 2회 연속 확인될 때만 교체해 잦은 매매를 줄입니다.')
    sources=_leader_signals();detected=[]
    for names in sources.values():detected.extend(names)
    detected=list(dict.fromkeys(detected))
    manual=st.text_input('추가/수정할 주도주 (쉼표 구분)',value='',placeholder='예: 알테오젠, HMM, 금호타이어')
    leaders=list(dict.fromkeys(detected+[x.strip() for x in manual.split(',') if x.strip()]))
    if sources:st.caption('자동 반영: '+' · '.join(f'{k} {len(v)}개' for k,v in sources.items()))
    if not leaders:
        st.info('먼저 전체 업데이트를 실행하거나 위 칸에 주도주를 입력하면 ETF 비교가 활성화됩니다.');return
    st.write('평가 주도주: '+', '.join(leaders[:20]))
    if st.button('🔄 주도주 ETF 다시 평가',use_container_width=True):
        rows=[];eval_end=pd.Timestamp.today();eval_start=eval_end-pd.DateOffset(months=8)
        with st.spinner('ETF 후보의 중복도와 가격 추세를 비교하는 중...'):
            for name,info in ETF_CANDIDATES.items():
                px=load_price(info['ticker'],eval_start.date(),eval_end.date())
                score,matched,momentum,ma_score=_etf_score(name,leaders,px)
                rows.append({'ETF':name,'테마':info['theme'],'종합점수':score,'포함 주도주':', '.join(matched) or '-','3개월추세점수':momentum,'MA5점수':ma_score})
        result=pd.DataFrame(rows).sort_values(['종합점수','ETF'],ascending=[False,True]).reset_index(drop=True)
        st.session_state['leader_etf_result']=result
        best=result.iloc[0]['ETF'];current=st.session_state.get('leader_etf_current',etf)
        current_score=float(result.loc[result['ETF']==current,'종합점수'].iloc[0]) if current in set(result['ETF']) else 0
        gap=float(result.iloc[0]['종합점수'])-current_score
        pending=st.session_state.get('leader_etf_pending')
        count=int(st.session_state.get('leader_etf_pending_count',0))
        if best==current:
            verdict='✅ 현재 ETF 유지';pending=None;count=0
        elif gap<10:
            verdict=f'🟡 유지 · 추천 우위가 {gap:.1f}점으로 교체 기준(10점) 미달';pending=None;count=0
        else:
            count=count+1 if pending==best else 1;pending=best
            verdict=f'🟠 {best} 교체 확인 {count}/2회' if count<2 else f'🔴 {best}로 교체 검토'
            if count>=2:st.session_state['leader_etf_current']=best
        st.session_state['leader_etf_pending']=pending;st.session_state['leader_etf_pending_count']=count;st.session_state['leader_etf_verdict']=verdict
    result=st.session_state.get('leader_etf_result')
    if isinstance(result,pd.DataFrame) and not result.empty:
        c1,c2,c3=st.columns(3);c1.metric('현재 기준 ETF',st.session_state.get('leader_etf_current',etf));c2.metric('추천 ETF',result.iloc[0]['ETF']);c3.metric('추천 점수',f"{result.iloc[0]['종합점수']:.1f}")
        st.info(st.session_state.get('leader_etf_verdict','평가 버튼을 눌러 판정을 갱신하세요.'))
        st.dataframe(result,use_container_width=True,hide_index=True)
        st.caption('구성종목 목록은 후보 탐색용 기준 목록입니다. 실제 매수 전 운용사 최신 PDF의 편입종목·비중을 확인하세요. 과거 백테스트에는 당시 구성종목만 사용해야 미래정보 편향을 피할 수 있습니다.')

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
    years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25);total=(eq.iloc[-1]/eq.iloc[0]-1)*100;cagr=((eq.iloc[-1]/eq.iloc[0])**(1/years)-1)*100;mdd=(eq/eq.cummax()-1).min()*100
    return total,cagr,mdd

def grids(precise=False):
    if precise:
        staged=[x for x in product([30,40,50,60,70,80,90,100,120],[100,120,140,160,180,200],[70,80,90,100],[0,10,20,30,40,50,70],[8,10,12,15,18,20],[0,10,20,30,40],[0,1,2,3]) if x[0]<x[1] and x[3]<=x[2]]
        bull=[x for x in product([30,40,60,80],[120,140,160,200],[10,20],[80,90,100],[20,30,50],[10,12,15,18],[0,10,20,40],[0,1,2]) if x[0]<x[1] and x[4]<=x[3]]
    else:
        staged=[x for x in product([40,60,80],[120,140,160],[90,100],[10,20,30],[10,12,15],[0,10,20],[0,2]) if x[0]<x[1] and x[3]<=x[2]]
        bull=[x for x in product([40,60,80],[120,160],[10,20],[90,100],[20,30],[10,12,15],[0,10,20],[0,2]) if x[0]<x[1] and x[4]<=x[3]]
        staged=list(dict.fromkeys(staged+[(40,160,100,20,15,0,0),(60,140,100,20,15,20,2),(60,140,100,20,18,40,2),(60,160,100,20,15,20,2),(40,120,100,20,15,40,0)]))
    return staged,bull

def context(px,staged,bull):
    close=px.to_numpy(float);dd=close/np.maximum.accumulate(close)-1;ma_days=set();slope_pairs=set()
    for cfg in staged:ma_days.update([cfg[0],cfg[1]])
    for cfg in bull:ma_days.update([cfg[0],cfg[1]]);slope_pairs.add((cfg[1],cfg[2]))
    mas={day:px.rolling(day).mean().to_numpy(float) for day in ma_days};slopes={(slow,s):pd.Series(mas[slow],index=px.index).pct_change(s).to_numpy(float) for slow,s in slope_pairs}
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

def cost_adjusted_returns(asset_ret,weight,fee=0,slippage=0,sell_tax=0,initial_weight=1):
    weight=weight.astype(float).clip(0,1);prev=weight.shift(1);prev.iloc[0]=initial_weight;delta=weight-prev;turnover=delta.abs();sells=(-delta).clip(lower=0);cost=turnover*((fee+slippage)/10000)+sells*(sell_tax/10000)
    return asset_ret.astype(float)*weight-cost,turnover,sells

def simulate(px,engine,cfg,ctx,capital=1.0):
    w=staged_weights(px,cfg,ctx) if engine=='기존 다단계' else bull_weights(px,cfg,ctx);r=px.pct_change().fillna(0)*w.shift(1).fillna(1.0)
    return capital*(1+r).cumprod()

def scan_candidates(px,staged,bull,progress=None,label='후보 탐색'):
    ctx=context(px,staged,bull);rows=[];total=len(staged)+len(bull);done=0;bh=px/px.iloc[0];_,bh_cagr,_=metrics(bh)
    for engine,configs in [('기존 다단계',staged),('강세장 보존',bull)]:
        for cfg in configs:
            eq=simulate(px,engine,cfg,ctx);_,cagr,mdd=metrics(eq);capture=cagr/bh_cagr*100 if bh_cagr>0 else 100;rows.append({'cagr':cagr,'mdd':mdd,'capture':capture,'engine':engine,'cfg':cfg});done+=1
            if progress is not None and (done%50==0 or done==total):progress.progress(done/total,text=f'{label}: {done:,}/{total:,} 조합 ({done/total*100:.0f}%)')
    return rows,ctx

def choose_from_scan(scan,limit,selector):
    safe=[r for r in scan if r['mdd']>=-limit]
    if not safe:return None
    if selector=='max':return max(safe,key=lambda r:(r['cagr'],r['mdd']))
    best_cagr=max(r['cagr'] for r in safe);ratio=.95 if selector=='balanced' else .90;threshold=best_cagr-abs(best_cagr)*(1-ratio);pool=[r for r in safe if r['cagr']>=threshold]
    if not pool:pool=[max(safe,key=lambda r:(r['cagr'],r['mdd']))]
    return max(pool,key=lambda r:(r['mdd'],r['cagr'],r['capture']))

def make_windows(px,train_days,test_days):
    windows=[];d=px.index.min()+pd.Timedelta(days=train_days)
    while d<px.index.max():
        te=min(d+pd.Timedelta(days=test_days),px.index.max());tr=px[(px.index>=d-pd.Timedelta(days=train_days))&(px.index<d)];ts=px[(px.index>=d)&(px.index<=te)]
        if len(tr)<120 or len(ts)<20:break
        windows.append((d,te));d=te+pd.Timedelta(days=1)
    return windows

def walk_forward_all(px,staged,bull,train_days,test_days,progress=None,profiles=None,with_cost=False,cost_mult=1.0,extra_delay=0):
    profiles=profiles or PROFILES;windows=make_windows(px,train_days,test_days);capitals={n:initial for n in profiles};return_parts={n:[] for n in profiles};details=[]
    for wi,(test_start,test_end) in enumerate(windows,1):
        train_start=test_start-pd.Timedelta(days=train_days);train=px[(px.index>=train_start)&(px.index<test_start)];test=px[(px.index>=test_start)&(px.index<=test_end)];scan,_=scan_candidates(train,staged,bull)
        for name,p in profiles.items():
            best=choose_from_scan(scan,p['train_mdd'],p['selector'])
            if best is None:continue
            engine,cfg=best['engine'],best['cfg'];history=px[(px.index>=train_start)&(px.index<=test_end)];hctx=context(history,[cfg] if engine=='기존 다단계' else [],[cfg] if engine=='강세장 보존' else []);signal=staged_weights(history,cfg,hctx) if engine=='기존 다단계' else bull_weights(history,cfg,hctx)
            weight=signal.shift(1+extra_delay).fillna(1.0).reindex(test.index).fillna(1.0);asset_ret=history.pct_change().reindex(test.index).fillna(0.0)
            if with_cost:strategy_ret,turnover,_=cost_adjusted_returns(asset_ret,weight,fee_bps*cost_mult,slippage_bps*cost_mult,sell_tax_bps*cost_mult,1.0)
            else:strategy_ret=asset_ret*weight;turnover=pd.Series(0.0,index=test.index)
            start_capital=capitals[name];local_eq=start_capital*(1+strategy_ret).cumprod();capitals[name]=float(local_eq.iloc[-1]);return_parts[name].append(strategy_ret);base_idx=train.index[-1];metric_curve=pd.concat([pd.Series([start_capital],index=[base_idx]),local_eq]);_,oc,om=metrics(metric_curve);bh_local=initial*(1+asset_ret).cumprod();bh_curve=pd.concat([pd.Series([initial],index=[base_idx]),bh_local]);_,bc,bm=metrics(bh_curve)
            details.append({'모드':name,'학습기간':f'{train.index[0].date()}~{train.index[-1].date()}','미래검증':f'{test.index[0].date()}~{test.index[-1].date()}','엔진':engine,'설정':str(cfg),'OOS CAGR(%)':oc,'OOS MDD(%)':om,'B&H CAGR(%)':bc,'B&H MDD(%)':bm,'손실구간':capitals[name]<start_capital,'회전율합계':float(turnover.sum()),'검증시작자산(원)':start_capital,'검증종료자산(원)':capitals[name]})
        if progress is not None:progress.progress(wi/max(len(windows),1),text=f'워크포워드: {wi}/{len(windows)} 구간 완료')
    curves={}
    for name,rlist in return_parts.items():
        if not rlist:continue
        r=pd.concat(rlist);r=r[~r.index.duplicated(keep='first')].sort_index();eq=initial*(1+r).cumprod();first_idx=r.index[0];prior=px.index[px.index<first_idx];base_idx=prior[-1] if len(prior) else first_idx-pd.Timedelta(days=1);curves[name]=pd.concat([pd.Series([initial],index=[base_idx]),eq])
    return curves,pd.DataFrame(details)

def parse_cfg(text):
    try:return tuple(int(float(x.strip())) for x in str(text).strip().strip('()').split(',') if x.strip())
    except Exception:return None

def replay_fixed_oos(px,detail,mode='🛡️ 방어형',cost_mult=1.0,extra_delay=0):
    d=detail[detail['모드']==mode].copy() if not detail.empty else pd.DataFrame()
    if d.empty:return pd.Series(dtype=float)
    capital=float(initial);parts=[]
    for _,row in d.iterrows():
        cfg=parse_cfg(row['설정']);engine=row['엔진']
        if cfg is None:continue
        tr_start=pd.Timestamp(str(row['학습기간']).split('~')[0]);te_start,te_end=[pd.Timestamp(x) for x in str(row['미래검증']).split('~')]
        history=px[(px.index>=tr_start)&(px.index<=te_end)];test=px[(px.index>=te_start)&(px.index<=te_end)]
        if history.empty or test.empty:continue
        hctx=context(history,[cfg] if engine=='기존 다단계' else [],[cfg] if engine=='강세장 보존' else []);signal=staged_weights(history,cfg,hctx) if engine=='기존 다단계' else bull_weights(history,cfg,hctx)
        weight=signal.shift(1+extra_delay).fillna(1.0).reindex(test.index).fillna(1.0);asset_ret=history.pct_change().reindex(test.index).fillna(0.0);strategy_ret,_,_=cost_adjusted_returns(asset_ret,weight,fee_bps*cost_mult,slippage_bps*cost_mult,sell_tax_bps*cost_mult,1.0)
        local=capital*(1+strategy_ret).cumprod();capital=float(local.iloc[-1]);parts.append(strategy_ret)
    if not parts:return pd.Series(dtype=float)
    r=pd.concat(parts);r=r[~r.index.duplicated(keep='first')].sort_index();eq=initial*(1+r).cumprod();first_idx=r.index[0];prior=px.index[px.index<first_idx];base_idx=prior[-1] if len(prior) else first_idx-pd.Timedelta(days=1)
    return pd.concat([pd.Series([initial],index=[base_idx]),eq])

def stability_validate(px,staged,bull,cagr_floor,mdd_target,progress=None,with_cost=False,cost_mult=1.0,extra_delay=0):
    combos=[(360,90),(360,120),(540,120),(540,180)];profile={'🛡️ 방어형':PROFILES['🛡️ 방어형']};rows=[]
    for i,(tr,te) in enumerate(combos,1):
        curves,detail=walk_forward_all(px,staged,bull,tr,te,None,profile,with_cost,cost_mult,extra_delay);wf=curves.get('🛡️ 방어형',pd.Series(dtype=float))
        if len(wf)>=2 and not detail.empty:
            _,cagr,mdd=metrics(wf);d=detail[detail['모드']=='🛡️ 방어형'];losses=int(d['손실구간'].sum());n=len(d);wins=int((d['OOS CAGR(%)']>=d['B&H CAGR(%)']).sum());rows.append({'학습기간':f'{tr//30}개월','검증기간':f'{te//30}개월','OOS CAGR(%)':cagr,'OOS MDD(%)':mdd,'손실구간':losses,'총구간':n,'B&H CAGR 우위':f'{wins}/{n}','CAGR≥목표':cagr>=cagr_floor,'MDD≤목표':mdd>=-mdd_target,'동시충족':cagr>=cagr_floor and mdd>=-mdd_target})
        if progress is not None:progress.progress(i/len(combos),text=f'안정성 검증 {i}/{len(combos)} 조합')
    return pd.DataFrame(rows)

# --- 월봉 5개월선 3년 백테스트 ---
def _monthly_from_daily(px):
    if px is None or px.empty:return pd.Series(dtype=float)
    m=px.resample('ME').last().dropna()
    return m

def _ma5_bt_rows(monthly,rising_only=False):
    c=pd.to_numeric(monthly,errors='coerce').dropna();ma=c.rolling(5).mean();rows=[]
    for i in range(5,len(c)):
        if pd.isna(ma.iloc[i-1]) or pd.isna(ma.iloc[i]):continue
        br=c.iloc[i-1]<=ma.iloc[i-1] and c.iloc[i]>ma.iloc[i]
        slope=(ma.iloc[i]/ma.iloc[i-1]-1)*100 if ma.iloc[i-1] else 0
        if not br or (rising_only and slope<=0):continue
        entry=float(c.iloc[i]);future=c.iloc[i+1:min(i+13,len(c))]
        max_ret=(float(future.max())/entry-1)*100 if not future.empty else np.nan
        min_ret=(float(future.min())/entry-1)*100 if not future.empty else np.nan
        row={'돌파월':str(c.index[i].date())[:7],'매수가':round(entry),'5개월선':round(float(ma.iloc[i])),'기울기(%)':round(float(slope),2),'최고수익률(%)':max_ret,'최대하락률(%)':min_ret,'+10%도달':bool(pd.notna(max_ret) and max_ret>=10),'+20%도달':bool(pd.notna(max_ret) and max_ret>=20)}
        for n in (1,3,6,12):
            j=i+n;row[f'{n}개월수익률(%)']=(float(c.iloc[j])/entry-1)*100 if j<len(c) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)

def _ma5_summary(bt,label):
    if bt.empty:return {'전략':label,'신호수':0}
    r={'전략':label,'신호수':len(bt)}
    for n in (1,3,6,12):
        s=pd.to_numeric(bt[f'{n}개월수익률(%)'],errors='coerce').dropna();r[f'{n}개월승률(%)']=round(s.gt(0).mean()*100,1) if len(s) else np.nan;r[f'{n}개월평균(%)']=round(s.mean(),2) if len(s) else np.nan;r[f'{n}개월중앙값(%)']=round(s.median(),2) if len(s) else np.nan
    r['+10%도달률(%)']=round(bt['+10%도달'].mean()*100,1);r['+20%도달률(%)']=round(bt['+20%도달'].mean()*100,1);r['평균최대하락(%)']=round(pd.to_numeric(bt['최대하락률(%)'],errors='coerce').mean(),2)
    return r

def render_ma5_etf_backtest():
    st.divider();st.header('📈 ETF 월봉 5개월선 · 3년 백테스트')
    st.caption('선택 ETF를 최근 3년 월봉으로 검증합니다. 평균뿐 아니라 중앙값·최대하락·+10/+20% 도달률까지 함께 봅니다.')
    ticker=ETF_CANDIDATES[etf]['ticker'];bt_end=pd.Timestamp(end);bt_start=bt_end-pd.DateOffset(years=3,months=6);px3=load_price(ticker,bt_start.date(),bt_end.date());monthly=_monthly_from_daily(px3)
    if len(monthly)<18:st.warning('3년 백테스트에 필요한 월봉 데이터가 부족합니다.');return
    simple=_ma5_bt_rows(monthly,False);rising=_ma5_bt_rows(monthly,True);summary=pd.DataFrame([_ma5_summary(simple,'단순 5개월선 돌파'),_ma5_summary(rising,'돌파 + 5개월선 상승')])
    st.subheader('전략 성과 비교');st.dataframe(summary,use_container_width=True,hide_index=True)
    detail=rising if not rising.empty else simple
    st.subheader('과거 돌파 기록')
    if detail.empty:st.info('최근 3년 동안 조건에 맞는 돌파 신호가 없습니다.')
    else:
        cfg={'매수가':st.column_config.NumberColumn('매수가',format='%d원'),'5개월선':st.column_config.NumberColumn('5개월선',format='%d원')};st.dataframe(detail.round(2),use_container_width=True,hide_index=True,column_config=cfg)
        if pd.to_numeric(detail['12개월수익률(%)'],errors='coerce').dropna().abs().max()>300:st.warning('⚠️ 12개월 수익률 300% 초과 값이 있습니다. 액면분할·데이터 보정 여부를 반드시 재확인하세요.')
    st.info('핵심: 평균수익률이 한 종목/한 구간에 끌려가지 않는지 중앙값을 함께 확인하고, 상승 중인 5개월선 조건이 단순 돌파보다 실제로 개선되는지 비교하세요.')

render_leader_etf_rotation()
render_ma5_etf_backtest()

if st.button('🚀 OOS + 안정성 + 실전 스트레스 검증 실행',type='primary',use_container_width=True):
    ticker=ETF_CANDIDATES[etf]['ticker'];bar=st.progress(0,text='가격 데이터 불러오는 중...');status=st.empty();px=load_price(ticker,start,end)
    if len(px)<205:bar.empty();st.error('가격 데이터가 부족합니다.');st.stop()
    staged,bull=grids(precise);st.caption(f'탐색 후보: {len(staged)+len(bull):,}개');bh=initial*px/px.iloc[0];bh_ret,bh_cagr,bh_mdd=metrics(bh);status.info('전체기간 공통 후보 계산 중...');scan,ctx=scan_candidates(px,staged,bull,bar,'전체기간 공통 탐색');whole=[]
    for name,p in PROFILES.items():
        best=choose_from_scan(scan,p['train_mdd'],p['selector'])
        if best is None:continue
        eq=simulate(px,best['engine'],best['cfg'],ctx,initial);ret,cagr,mdd=metrics(eq);whole.append({'모드':name,'CAGR(%)':cagr,'MDD(%)':mdd,'포착률(%)':best['capture'],'최종자산(원)':eq.iloc[-1],'설정':str(best['cfg'])})
    bar.progress(1.0,text='전체기간 완료 100%');status.success('전체기간 최적화 완료');st.subheader('📊 전체기간 참고 결과');st.dataframe(pd.DataFrame(whole).round(2),use_container_width=True,hide_index=True);st.caption(f'Buy & Hold: CAGR {bh_cagr:.1f}% · MDD {bh_mdd:.1f}%')

    st.divider();st.header('🚶 3모드 Walk-Forward OOS 비교');wfbar=st.progress(0);curves,detail=walk_forward_all(px,staged,bull,train_days,test_days,wfbar);summaries=[]
    for name in PROFILES:
        wf=curves.get(name,pd.Series(dtype=float));d=detail[detail['모드']==name] if not detail.empty else pd.DataFrame()
        if len(wf)<2 or d.empty:continue
        _,cagr,mdd=metrics(wf);losses=int(d['손실구간'].sum());n=len(d);summaries.append({'모드':name,'OOS CAGR(%)':cagr,'OOS MDD(%)':mdd,'손실구간':losses,'총구간':n,'최종자산(원)':wf.iloc[-1],'CAGR≥목표':cagr>=oos_cagr_floor,'MDD≤목표':mdd>=-oos_mdd_target})
    summary=pd.DataFrame(summaries);st.subheader('🏁 OOS 실전 후보');st.dataframe(summary.round(2),use_container_width=True,hide_index=True);st.line_chart(pd.DataFrame(curves))

    st.divider();st.header('🧪 OOS 안정성 검증 · 방어형');stab=stability_validate(px,staged,bull,oos_cagr_floor,oos_mdd_target)
    if not stab.empty:
        st.dataframe(stab.round(2),use_container_width=True,hide_index=True);passed_n=int(stab['동시충족'].sum());total_n=len(stab);c1,c2,c3=st.columns(3);c1.metric('동시충족',f'{passed_n}/{total_n}');c2.metric('안정성 통과율',f'{passed_n/total_n*100:.0f}%');c3.metric('평균 OOS MDD',f"{stab['OOS MDD(%)'].mean():.1f}%")

    st.divider();st.header('💸 기본 거래비용 반영 OOS');cost_wf=replay_fixed_oos(px,detail,'🛡️ 방어형',1.0,0)
    if len(cost_wf)>=2:
        _,cc,cm=metrics(cost_wf);gross_wf=curves.get('🛡️ 방어형',pd.Series(dtype=float));_,gc,_=metrics(gross_wf);c1,c2,c3,c4=st.columns(4);c1.metric('비용후 OOS CAGR',f'{cc:.1f}%');c2.metric('비용후 OOS MDD',f'{cm:.1f}%');c3.metric('CAGR 비용차감',f'-{gc-cc:.1f}%p');c4.metric('비용후 최종자산',f'{cost_wf.iloc[-1]:,.0f}원');st.line_chart(pd.DataFrame({'비용 전 방어형':gross_wf,'비용 후 방어형':cost_wf}))

    st.divider();st.header('🧯 최종 실전 스트레스 테스트')
    st.info('워크포워드에서 이미 선택된 방어형 설정을 그대로 재생합니다. 스트레스 시나리오마다 재최적화하지 않아 빠르고, 미래 데이터로 파라미터를 다시 맞추지 않습니다.')
    scenarios=[('기본비용',1.0,0),('비용 2배',2.0,0),('비용 3배',3.0,0),('비용 2배 + 1일 지연',2.0,1)];stress_rows=[];sbar=st.progress(0,text='고정 설정 스트레스 테스트 준비 중...')
    for i,(label,mult,delay) in enumerate(scenarios,1):
        wf=replay_fixed_oos(px,detail,'🛡️ 방어형',mult,delay)
        if len(wf)>=2:
            _,cagr,mdd=metrics(wf);stress_rows.append({'시나리오':label,'비용배수':mult,'추가지연(거래일)':delay,'OOS CAGR(%)':cagr,'OOS MDD(%)':mdd,'최종자산(원)':wf.iloc[-1],'CAGR≥목표':cagr>=oos_cagr_floor,'MDD≤목표':mdd>=-oos_mdd_target,'동시충족':cagr>=oos_cagr_floor and mdd>=-oos_mdd_target})
        sbar.progress(i/len(scenarios),text=f'고정 설정 스트레스 테스트 {i}/{len(scenarios)} 완료')
    stress=pd.DataFrame(stress_rows)
    if not stress.empty:
        st.dataframe(stress.round(2),use_container_width=True,hide_index=True);sp=int(stress['동시충족'].sum());sn=len(stress);worst_cagr=stress['OOS CAGR(%)'].min();worst_mdd=stress['OOS MDD(%)'].min();c1,c2,c3=st.columns(3);c1.metric('스트레스 통과',f'{sp}/{sn}');c2.metric('최저 CAGR',f'{worst_cagr:.1f}%');c3.metric('최악 MDD',f'{worst_mdd:.1f}%')
        if sp==sn:st.success('🏅 실전 운용 후보 V1.0 확정 후보: 모든 스트레스 시나리오에서 CAGR/MDD 목표를 통과했습니다.')
        elif sp>=3:st.warning('🟢 스트레스 내성 양호: 4개 중 3개 이상 통과했습니다. 소액 전진검증 후 확대가 적절합니다.')
        else:st.error('❌ 스트레스 내성 부족: 실전 V1.0 확정을 보류합니다.')

    st.subheader('📋 기본 구간별 Out-of-Sample 결과');st.dataframe(detail.round(2),use_container_width=True,hide_index=True)
    st.warning('백테스트·워크포워드는 미래 성과를 보장하지 않습니다. 실제 체결가격·세금·추적오차와 시장 구조 변화로 실전 결과는 달라질 수 있습니다.')

