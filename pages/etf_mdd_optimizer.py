import streamlit as st
import pandas as pd
import yfinance as yf
from itertools import product

st.title('🛡️ ETF MDD 방어 최적화')
st.caption('TIGER 코리아TOP10 / KODEX200의 높은 수익률은 최대한 유지하면서 MDD를 줄이는 추세 방어 규칙을 탐색합니다.')

c1,c2,c3=st.columns(3)
with c1:
    start=st.date_input('시작일', pd.Timestamp('2023-08-19'))
with c2:
    end=st.date_input('종료일', pd.Timestamp.today())
with c3:
    initial=st.number_input('초기자금(원)', min_value=1000000, value=10000000, step=1000000)

ETF={'TIGER 코리아TOP10':'292150.KS','KODEX200':'069500.KS'}
etf_name=st.selectbox('ETF', list(ETF))
st.info('방어 방식: ETF가 이동평균선 아래로 내려가거나 고점 대비 일정 수준 하락하면 현금 비중을 높이고, 추세가 회복되면 ETF 비중을 복원합니다. 전일 신호를 다음 거래일 수익률에 적용합니다.')

def metrics(eq):
    eq=eq.dropna()
    r=eq.iloc[-1]/eq.iloc[0]-1
    years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    cagr=(eq.iloc[-1]/eq.iloc[0])**(1/years)-1
    dd=eq/eq.cummax()-1
    return r*100,cagr*100,dd.min()*100

@st.cache_data(ttl=3600, show_spinner=False)
def load_price(ticker,start,end):
    # 1차: 사용자가 요청한 기간
    d=yf.download(ticker,start=str(start),end=str(pd.Timestamp(end)+pd.Timedelta(days=1)),auto_adjust=True,progress=False)
    # 일부 ETF는 상장 전 날짜를 포함한 장기 요청에서 빈 데이터가 오는 경우가 있어 전체 이력으로 재시도
    if d is None or d.empty:
        d=yf.download(ticker,period='max',auto_adjust=True,progress=False)
    if d is None or d.empty:
        return pd.Series(dtype=float)
    if isinstance(d.columns,pd.MultiIndex):
        # yfinance 단일티커 MultiIndex 대응
        if 'Close' in d.columns.get_level_values(0):
            s=d['Close']
            if isinstance(s,pd.DataFrame): s=s.iloc[:,0]
        else:
            s=d.xs('Close',axis=1,level=1)
            if isinstance(s,pd.DataFrame): s=s.iloc[:,0]
    else:
        s=d['Close']
    s=pd.to_numeric(s,errors='coerce').dropna()
    s.index=pd.to_datetime(s.index)
    if getattr(s.index,'tz',None) is not None:
        s.index=s.index.tz_localize(None)
    # 사용자가 ETF 상장 전을 지정했으면 실제 첫 거래일부터 자동 시작
    req_start=pd.Timestamp(start)
    req_end=pd.Timestamp(end)
    s=s[(s.index<=req_end)]
    if s.empty:
        return s
    effective_start=max(req_start,s.index.min())
    return s[s.index>=effective_start]

def run_defense(px, ma, reduce_pct, exit_dd, reentry_buffer):
    sma=px.rolling(ma).mean()
    peak=px.cummax()
    draw=px/peak-1
    w=pd.Series(1.0,index=px.index)
    weak=px < sma
    w.loc[weak]=1-reduce_pct/100
    severe=draw <= -exit_dd/100
    w.loc[severe]=0.0
    defensive=False
    vals=[]
    for i in range(len(px)):
        if severe.iloc[i]: defensive=True
        if defensive and pd.notna(sma.iloc[i]) and px.iloc[i] >= sma.iloc[i]*(1+reentry_buffer/100): defensive=False
        vals.append(0.0 if defensive else w.iloc[i])
    weight=pd.Series(vals,index=px.index).shift(1).fillna(1.0)
    ret=px.pct_change().fillna(0)
    eq=initial*(1+ret*weight).cumprod()
    return eq,weight

if st.button('🚀 ETF MDD 방어 최적화 실행', use_container_width=True, type='primary'):
    with st.spinner('ETF 가격 이력과 추세 방어 조합을 계산하고 있습니다...'):
        px=load_price(ETF[etf_name],start,end)
        if px.empty:
            st.error('ETF 가격 데이터를 가져오지 못했습니다. 잠시 후 다시 실행해 주세요.')
            st.stop()
        actual_start=px.index.min().date()
        if actual_start > start:
            st.warning(f'선택한 시작일은 ETF 가격 이력보다 앞섭니다. 실제 데이터가 시작되는 {actual_start}부터 자동으로 백테스트합니다.')
        if len(px)<205:
            st.error(f'사용 가능한 거래일이 {len(px)}일뿐이라 200일 이동평균 검증이 어렵습니다. 종료일을 늘리거나 다른 ETF를 선택해 주세요.')
            st.stop()
        bh=initial*(px/px.iloc[0])
        bh_ret,bh_cagr,bh_mdd=metrics(bh)
        rows=[]; curves={}
        for ma,reduce_pct,exit_dd,buffer in product([60,120,200],[30,50,70],[10,15,20],[0,2]):
            eq,w=run_defense(px,ma,reduce_pct,exit_dd,buffer)
            ret,cagr,mdd=metrics(eq)
            penalty=max(0,abs(mdd)-25)*1.5
            score=cagr-penalty+0.25*(mdd-bh_mdd)
            rows.append({'이평선':ma,'약세시 ETF축소(%)':reduce_pct,'고점대비 전량현금(%)':exit_dd,'재진입버퍼(%)':buffer,'누적수익률(%)':ret,'CAGR(%)':cagr,'MDD(%)':mdd,'균형점수':score})
            curves[(ma,reduce_pct,exit_dd,buffer)]=eq
        res=pd.DataFrame(rows).sort_values(['균형점수','CAGR(%)'],ascending=False).reset_index(drop=True)
        best=res.iloc[0]
        key=(int(best['이평선']),int(best['약세시 ETF축소(%)']),int(best['고점대비 전량현금(%)']),int(best['재진입버퍼(%)']))
        best_eq=curves[key]
        a,b,c,d=st.columns(4)
        a.metric('Buy & Hold CAGR',f'{bh_cagr:.1f}%')
        b.metric('방어전략 CAGR',f"{best['CAGR(%)']:.1f}%",f"{best['CAGR(%)']-bh_cagr:+.1f}%p")
        c.metric('Buy & Hold MDD',f'{bh_mdd:.1f}%')
        d.metric('방어전략 MDD',f"{best['MDD(%)']:.1f}%",f"{best['MDD(%)']-bh_mdd:+.1f}%p")
        st.success(f"추천: {int(best['이평선'])}일선 아래 ETF {int(best['약세시 ETF축소(%)'])}% 축소 · 고점 대비 -{int(best['고점대비 전량현금(%)'])}% 시 현금전환 · 이평선 +{int(best['재진입버퍼(%)'])}% 회복 시 재진입")
        st.caption(f'실제 백테스트 기간: {px.index.min().date()} ~ {px.index.max().date()} · {len(px):,} 거래일')
        chart=pd.concat([bh.rename(f'{etf_name} Buy & Hold'),best_eq.rename('MDD 방어전략')],axis=1)
        st.subheader('📈 누적자산 비교')
        st.line_chart(chart)
        st.subheader('🏆 MDD 방어 전략 TOP10')
        st.dataframe(res.head(10).round(2),use_container_width=True,hide_index=True)
        safe=res[res['MDD(%)']>=-25].sort_values('CAGR(%)',ascending=False)
        st.subheader('🎯 MDD -25% 이내 최고 CAGR')
        if len(safe): st.dataframe(safe.head(10).round(2),use_container_width=True,hide_index=True)
        else: st.warning('이번 기간에는 MDD -25% 조건을 만족하는 조합이 없습니다.')
        st.caption('주의: 과거 백테스트 결과이며 미래 수익을 보장하지 않습니다. 최종 규칙은 기간분할/워크포워드 검증이 필요합니다.')