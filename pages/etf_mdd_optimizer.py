import streamlit as st
import pandas as pd
import yfinance as yf
from itertools import product

st.title('🛡️ ETF MDD 방어 최적화 · CAGR 강화')
st.caption('TIGER 코리아TOP10 / KODEX200의 높은 수익률을 최대한 유지하면서 MDD를 줄이는 추세 방어 규칙을 더 촘촘하게 탐색합니다.')

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
st.info('CAGR 강화 방식: 너무 빨리 전량 현금화하지 않도록 약세 시 부분축소 폭과 고점대비 방어 기준을 세밀하게 탐색합니다. 목표 MDD 이내에서는 CAGR이 가장 높은 조합을 우선 추천합니다.')

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

def run_defense(px, ma, reduce_pct, trigger_dd, severe_weight, reentry_buffer):
    sma=px.rolling(ma).mean()
    peak=px.cummax()
    draw=px/peak-1

    normal_weight=1.0
    weak_weight=1-reduce_pct/100
    severe_target=severe_weight/100
    vals=[]
    defensive=False

    for i in range(len(px)):
        if draw.iloc[i] <= -trigger_dd/100:
            defensive=True
        if defensive and pd.notna(sma.iloc[i]) and px.iloc[i] >= sma.iloc[i]*(1+reentry_buffer/100):
            defensive=False

        if defensive:
            vals.append(severe_target)
        elif pd.notna(sma.iloc[i]) and px.iloc[i] < sma.iloc[i]:
            vals.append(weak_weight)
        else:
            vals.append(normal_weight)

    weight=pd.Series(vals,index=px.index).shift(1).fillna(1.0)
    ret=px.pct_change().fillna(0)
    eq=initial*(1+ret*weight).cumprod()
    return eq,weight

if st.button('🚀 CAGR 강화형 ETF 방어 최적화 실행', use_container_width=True, type='primary'):
    with st.spinner('ETF 가격 이력과 CAGR 강화형 방어 조합을 계산하고 있습니다...'):
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

        # 5*4*4*2*2 = 320개. 기존보다 촘촘하지만 계산은 가벼운 편입니다.
        for ma,reduce_pct,trigger_dd,severe_weight,buffer in product(
            [80,100,120,140,160],
            [10,20,30,40],
            [10,12,15,18],
            [0,25],
            [0,2],
        ):
            eq,w=run_defense(px,ma,reduce_pct,trigger_dd,severe_weight,buffer)
            ret,cagr,mdd=metrics(eq)
            within_target = mdd >= -target_mdd
            # 목표 MDD 안에서는 CAGR을 사실상 최우선, 목표 초과 시 강한 벌점
            penalty=max(0,abs(mdd)-target_mdd)*3.0
            score=cagr-penalty
            rows.append({
                '이평선':ma,
                '약세시 ETF축소(%)':reduce_pct,
                '고점대비 방어발동(%)':trigger_dd,
                '방어시 ETF잔존비중(%)':severe_weight,
                '재진입버퍼(%)':buffer,
                '누적수익률(%)':ret,
                'CAGR(%)':cagr,
                'MDD(%)':mdd,
                '목표MDD충족':within_target,
                '점수':score,
            })
            curves[(ma,reduce_pct,trigger_dd,severe_weight,buffer)]=eq

        res=pd.DataFrame(rows)
        safe=res[res['목표MDD충족']].sort_values(['CAGR(%)','MDD(%)'],ascending=[False,False]).reset_index(drop=True)
        if len(safe):
            best=safe.iloc[0]
        else:
            best=res.sort_values(['점수','CAGR(%)'],ascending=False).iloc[0]

        key=(int(best['이평선']),int(best['약세시 ETF축소(%)']),int(best['고점대비 방어발동(%)']),int(best['방어시 ETF잔존비중(%)']),int(best['재진입버퍼(%)']))
        best_eq=curves[key]

        a,b,c,d=st.columns(4)
        a.metric('Buy & Hold CAGR',f'{bh_cagr:.1f}%')
        b.metric('방어전략 CAGR',f"{best['CAGR(%)']:.1f}%",f"{best['CAGR(%)']-bh_cagr:+.1f}%p")
        c.metric('Buy & Hold MDD',f'{bh_mdd:.1f}%')
        d.metric('방어전략 MDD',f"{best['MDD(%)']:.1f}%",f"{best['MDD(%)']-bh_mdd:+.1f}%p")

        st.success(
            f"추천: {int(best['이평선'])}일선 아래 ETF {int(best['약세시 ETF축소(%)'])}% 축소 · "
            f"고점 대비 -{int(best['고점대비 방어발동(%)'])}%에서 방어모드 · "
            f"ETF {int(best['방어시 ETF잔존비중(%)'])}% 유지 · 이평선 +{int(best['재진입버퍼(%)'])}% 회복 시 정상복귀"
        )
        st.caption(f'실제 백테스트 기간: {px.index.min().date()} ~ {px.index.max().date()} · {len(px):,} 거래일')

        chart=pd.concat([bh.rename(f'{etf_name} Buy & Hold'),best_eq.rename('CAGR 강화 방어전략')],axis=1)
        st.subheader('📈 누적자산 비교')
        st.line_chart(chart)

        st.subheader(f'🎯 MDD -{target_mdd}% 이내 CAGR TOP10')
        if len(safe):
            st.dataframe(safe.head(10).round(2),use_container_width=True,hide_index=True)
        else:
            st.warning(f'MDD -{target_mdd}% 조건을 만족하는 조합이 없습니다.')

        st.subheader('📊 전체 후보 중 CAGR TOP15')
        st.dataframe(res.sort_values('CAGR(%)',ascending=False).head(15).round(2),use_container_width=True,hide_index=True)

        st.caption('이 버전은 MDD 목표 안에서 CAGR을 최대화하도록 바꿨습니다. 과거 최적화이므로 다음 단계에서 기간분할/워크포워드 검증이 필요합니다.')