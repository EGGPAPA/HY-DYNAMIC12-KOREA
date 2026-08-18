import streamlit as st
import pandas as pd
import yfinance as yf
from itertools import product

st.title('🛡️ ETF MDD 방어 최적화 · 다단계 비중조절')
st.caption('TIGER 코리아TOP10 / KODEX200의 높은 CAGR을 최대한 유지하면서 MDD를 줄이기 위해 100%→70%→40%→0% 식의 단계적 비중 조절을 자동 탐색합니다.')

c1,c2,c3=st.columns(3)
with c1:
    start=st.date_input('시작일', pd.Timestamp('2023-08-19'))
with c2:
    end=st.date_input('종료일', pd.Timestamp.today())
with c3:
    initial=st.number_input('초기자금(원)', min_value=1000000, value=10000000, step=1000000)

ETF={'TIGER 코리아TOP10':'292150.KS','KODEX200':'069500.KS'}
etf_name=st.selectbox('ETF', list(ETF))
target_mdd=st.slider('목표 최대낙폭 MDD(%)', min_value=15, max_value=35, value=25, step=1)
st.info('다단계 방어: 강세장에서는 ETF 100%를 유지하고, 단기 이평선 이탈→1차 축소, 장기 이평선 이탈→2차 축소, 고점대비 큰 하락→최종 방어를 적용합니다. 전일 신호를 다음 거래일 수익률에 적용합니다.')

def metrics(eq):
    eq=eq.dropna()
    r=eq.iloc[-1]/eq.iloc[0]-1
    years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    cagr=(eq.iloc[-1]/eq.iloc[0])**(1/years)-1
    dd=eq/eq.cummax()-1
    return r*100,cagr*100,dd.min()*100

@st.cache_data(ttl=3600, show_spinner=False)
def load_price(ticker,start,end):
    d=yf.download(ticker,start=str(start),end=str(pd.Timestamp(end)+pd.Timedelta(days=1)),auto_adjust=True,progress=False)
    if d is None or d.empty:
        d=yf.download(ticker,period='max',auto_adjust=True,progress=False)
    if d is None or d.empty:
        return pd.Series(dtype=float)
    if isinstance(d.columns,pd.MultiIndex):
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
    req_start=pd.Timestamp(start); req_end=pd.Timestamp(end)
    s=s[s.index<=req_end]
    if s.empty: return s
    effective_start=max(req_start,s.index.min())
    return s[s.index>=effective_start]

def run_staged(px, fast_ma, slow_ma, w1, w2, dd_trigger, severe_weight, recover_buffer):
    ma_fast=px.rolling(fast_ma).mean()
    ma_slow=px.rolling(slow_ma).mean()
    peak=px.cummax()
    draw=px/peak-1
    vals=[]
    severe=False
    for i in range(len(px)):
        p=px.iloc[i]
        mf=ma_fast.iloc[i]
        ms=ma_slow.iloc[i]
        dd=draw.iloc[i]
        if dd <= -dd_trigger/100:
            severe=True
        if severe and pd.notna(mf) and pd.notna(ms) and p >= mf*(1+recover_buffer/100) and p >= ms:
            severe=False
        if severe:
            weight=severe_weight/100
        elif pd.notna(ms) and p < ms:
            weight=w2/100
        elif pd.notna(mf) and p < mf:
            weight=w1/100
        else:
            weight=1.0
        vals.append(weight)
    weight=pd.Series(vals,index=px.index).shift(1).fillna(1.0)
    ret=px.pct_change().fillna(0)
    eq=initial*(1+ret*weight).cumprod()
    return eq,weight

if st.button('🚀 다단계 ETF 방어 최적화 실행', use_container_width=True, type='primary'):
    with st.spinner('다단계 비중조절 조합을 계산하고 있습니다...'):
        px=load_price(ETF[etf_name],start,end)
        if px.empty:
            st.error('ETF 가격 데이터를 가져오지 못했습니다. 잠시 후 다시 실행해 주세요.')
            st.stop()
        actual_start=px.index.min().date()
        if actual_start > start:
            st.warning(f'선택한 시작일은 ETF 가격 이력보다 앞섭니다. 실제 데이터가 시작되는 {actual_start}부터 자동으로 백테스트합니다.')
        if len(px)<205:
            st.error(f'사용 가능한 거래일이 {len(px)}일뿐이라 장기 이동평균 검증이 어렵습니다.')
            st.stop()

        bh=initial*(px/px.iloc[0])
        bh_ret,bh_cagr,bh_mdd=metrics(bh)
        rows=[]; curves={}

        # 4*4*3*3*4*3*2 = 3456개지만 연산은 단순 벡터/루프라 Streamlit에서 감당 가능
        fast_set=[60,80,100,120]
        slow_set=[120,140,160,200]
        w1_set=[70,80,90]
        w2_set=[30,50,70]
        dd_set=[12,15,18,20]
        severe_set=[0,20,40]
        buffer_set=[0,2]

        total=len(fast_set)*len(slow_set)*len(w1_set)*len(w2_set)*len(dd_set)*len(severe_set)*len(buffer_set)
        bar=st.progress(0,text=f'0/{total} 조합')
        n=0
        for fast_ma,slow_ma,w1,w2,dd_trigger,severe_weight,buffer in product(
            fast_set,slow_set,w1_set,w2_set,dd_set,severe_set,buffer_set
        ):
            if fast_ma>=slow_ma or w2>w1:
                continue
            eq,w=run_staged(px,fast_ma,slow_ma,w1,w2,dd_trigger,severe_weight,buffer)
            ret,cagr,mdd=metrics(eq)
            within=mdd>=-target_mdd
            # 목표 안에서는 CAGR 최우선. 목표 밖에서는 초과 MDD에 큰 벌점.
            penalty=max(0,abs(mdd)-target_mdd)*4.0
            score=cagr-penalty
            rows.append({
                '단기이평':fast_ma,
                '장기이평':slow_ma,
                '1차축소후 ETF비중(%)':w1,
                '2차축소후 ETF비중(%)':w2,
                '고점대비 최종방어(%)':dd_trigger,
                '최종방어 ETF비중(%)':severe_weight,
                '재진입버퍼(%)':buffer,
                '누적수익률(%)':ret,
                'CAGR(%)':cagr,
                'MDD(%)':mdd,
                '목표MDD충족':within,
                '점수':score,
            })
            curves[(fast_ma,slow_ma,w1,w2,dd_trigger,severe_weight,buffer)]=eq
            n+=1
            if n%50==0:
                bar.progress(min(n/total,1.0),text=f'{n}/{total} 조합')
        bar.empty()

        res=pd.DataFrame(rows)
        safe=res[res['목표MDD충족']].sort_values(['CAGR(%)','MDD(%)'],ascending=[False,False]).reset_index(drop=True)
        if len(safe):
            best=safe.iloc[0]
        else:
            best=res.sort_values(['점수','CAGR(%)'],ascending=False).iloc[0]

        key=(int(best['단기이평']),int(best['장기이평']),int(best['1차축소후 ETF비중(%)']),int(best['2차축소후 ETF비중(%)']),int(best['고점대비 최종방어(%)']),int(best['최종방어 ETF비중(%)']),int(best['재진입버퍼(%)']))
        best_eq=curves[key]

        a,b,c,d=st.columns(4)
        a.metric('Buy & Hold CAGR',f'{bh_cagr:.1f}%')
        b.metric('다단계 방어 CAGR',f"{best['CAGR(%)']:.1f}%",f"{best['CAGR(%)']-bh_cagr:+.1f}%p")
        c.metric('Buy & Hold MDD',f'{bh_mdd:.1f}%')
        d.metric('다단계 방어 MDD',f"{best['MDD(%)']:.1f}%",f"{best['MDD(%)']-bh_mdd:+.1f}%p")

        st.success(
            f"추천: {int(best['단기이평'])}일선 이탈→ETF {int(best['1차축소후 ETF비중(%)'])}% · "
            f"{int(best['장기이평'])}일선 이탈→ETF {int(best['2차축소후 ETF비중(%)'])}% · "
            f"고점대비 -{int(best['고점대비 최종방어(%)'])}%→ETF {int(best['최종방어 ETF비중(%)'])}% · "
            f"이평 회복(+{int(best['재진입버퍼(%)'])}% 버퍼) 시 정상복귀"
        )
        st.caption(f'실제 백테스트 기간: {px.index.min().date()} ~ {px.index.max().date()} · {len(px):,} 거래일')

        chart=pd.concat([bh.rename(f'{etf_name} Buy & Hold'),best_eq.rename('다단계 MDD 방어')],axis=1)
        st.subheader('📈 누적자산 비교')
        st.line_chart(chart)

        st.subheader(f'🎯 MDD -{target_mdd}% 이내 CAGR TOP10')
        if len(safe):
            st.dataframe(safe.head(10).round(2),use_container_width=True,hide_index=True)
        else:
            st.warning(f'MDD -{target_mdd}% 조건을 만족하는 조합이 없습니다.')

        st.subheader('🔥 CAGR 50% 이상 후보')
        hi=res[res['CAGR(%)']>=50].sort_values(['MDD(%)','CAGR(%)'],ascending=[False,False])
        if len(hi):
            st.dataframe(hi.head(15).round(2),use_container_width=True,hide_index=True)
        else:
            st.info('이번 기간에는 CAGR 50% 이상인 방어 조합이 없습니다.')

        st.subheader('📊 전체 후보 CAGR TOP15')
        st.dataframe(res.sort_values('CAGR(%)',ascending=False).head(15).round(2),use_container_width=True,hide_index=True)
        st.caption('목표는 Buy & Hold 수익률을 최대한 유지하면서 큰 하락 구간에서만 단계적으로 위험을 줄이는 것입니다. 최종 규칙은 기간분할 검증이 필요합니다.')