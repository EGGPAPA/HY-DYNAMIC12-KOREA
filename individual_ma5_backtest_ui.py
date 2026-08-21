import pandas as pd
import streamlit as st
import yfinance as yf
from datetime import datetime, timedelta


def _ticker(code, market):
    return f"{str(code).zfill(6)}.{'KQ' if str(market).upper()=='KOSDAQ' else 'KS'}"


@st.cache_data(ttl=3600, show_spinner=False)
def _universe():
    try:
        from pykrx import stock
        d=datetime.now().date()
        while d.weekday()>=5:
            d-=timedelta(days=1)
        ds=d.strftime('%Y%m%d')
        rows=[]
        for market in ('KOSPI','KOSDAQ'):
            for code in stock.get_market_ticker_list(ds,market=market):
                rows.append((str(code).zfill(6),stock.get_market_ticker_name(code),market))
        if rows:
            return pd.DataFrame(rows,columns=['종목코드','종목명','시장']),f'KRX 현재 상장종목 · {ds}'
    except Exception:
        pass
    try:
        df=pd.read_csv('korea_universe.csv',dtype={'종목코드':str})
        df['종목코드']=df['종목코드'].astype(str).str.zfill(6)
        return df[['종목코드','종목명','시장']], 'korea_universe.csv'
    except Exception:
        return pd.DataFrame(columns=['종목코드','종목명','시장']), '종목목록 없음'


@st.cache_data(ttl=1800, show_spinner=False)
def _monthly(code, market):
    try:
        d=yf.download(_ticker(code,market),period='3y',interval='1mo',auto_adjust=True,progress=False,threads=False,timeout=15)
        if d is None or d.empty:return pd.Series(dtype=float)
        c=d['Close'];c=c.iloc[:,0] if isinstance(c,pd.DataFrame) else c
        c=pd.to_numeric(c,errors='coerce').dropna()
        c.index=pd.to_datetime(c.index)
        return c
    except Exception:
        return pd.Series(dtype=float)


def _rows(code,name,market,rising_only=False):
    c=_monthly(code,market)
    if len(c)<8:return []
    ma=c.rolling(5).mean();out=[]
    for i in range(5,len(c)):
        if pd.isna(ma.iloc[i-1]) or pd.isna(ma.iloc[i]):continue
        br=c.iloc[i-1]<=ma.iloc[i-1] and c.iloc[i]>ma.iloc[i]
        slope=(ma.iloc[i]/ma.iloc[i-1]-1)*100 if ma.iloc[i-1] else 0
        if not br or (rising_only and slope<=0):continue
        entry=float(c.iloc[i]);future=c.iloc[i+1:min(i+13,len(c))]
        maxret=(float(future.max())/entry-1)*100 if not future.empty else None
        minret=(float(future.min())/entry-1)*100 if not future.empty else None
        r={'종목코드':str(code).zfill(6),'종목명':name,'시장':market,'돌파월':str(c.index[i])[:7],'매수가':round(entry),'5개월선':round(float(ma.iloc[i])),'기울기(%)':round(slope,2),'최고수익률(%)':maxret,'최대하락률(%)':minret,'+10%도달':bool(maxret is not None and maxret>=10),'+20%도달':bool(maxret is not None and maxret>=20)}
        for n in (1,3,6,12):
            j=i+n;r[f'{n}개월수익률(%)']=(float(c.iloc[j])/entry-1)*100 if j<len(c) else None
        out.append(r)
    return out


def _summary(df,label):
    if df.empty:return {'전략':label,'신호수':0}
    r={'전략':label,'신호수':len(df)}
    for n in (1,3,6,12):
        s=pd.to_numeric(df[f'{n}개월수익률(%)'],errors='coerce').dropna()
        r[f'{n}개월승률(%)']=round(s.gt(0).mean()*100,1) if len(s) else None
        r[f'{n}개월평균(%)']=round(s.mean(),2) if len(s) else None
        r[f'{n}개월중앙값(%)']=round(s.median(),2) if len(s) else None
    r['+10%도달률(%)']=round(df['+10%도달'].mean()*100,1)
    r['+20%도달률(%)']=round(df['+20%도달'].mean()*100,1)
    r['평균최대하락(%)']=round(pd.to_numeric(df['최대하락률(%)'],errors='coerce').mean(),2)
    return r


def render_individual_ma5_backtest():
    st.divider();st.header('📊 개별종목 월봉 5개월선 · 3년 백테스트')
    st.caption('ETF가 아니라 KOSPI·KOSDAQ 개별종목을 최근 3년간 테스트합니다. 현재 상장종목 기준이므로 생존편향 가능성이 있습니다.')
    uni,source=_universe()
    if uni.empty:
        st.error('개별종목 목록을 불러오지 못했습니다.');return
    c1,c2=st.columns(2)
    market=c1.selectbox('시장',['전체','KOSPI','KOSDAQ'],key='ind_ma5_market')
    limit=c2.number_input('테스트 종목 수',20,1000,min(300,len(uni)),20,key='ind_ma5_limit')
    work=uni if market=='전체' else uni[uni['시장']==market]
    work=work.head(min(int(limit),len(work)))
    st.caption(f'종목목록: {source} · 테스트 {len(work):,}종목')
    if st.button('▶ 개별종목 3년 백테스트 실행',type='primary',use_container_width=True,key='ind_ma5_run'):
        simple=[];rising=[];bar=st.progress(0)
        for n,(_,row) in enumerate(work.iterrows(),1):
            simple.extend(_rows(row['종목코드'],row['종목명'],row['시장'],False))
            rising.extend(_rows(row['종목코드'],row['종목명'],row['시장'],True))
            bar.progress(n/max(len(work),1))
        bar.empty();st.session_state['ind_ma5_simple']=simple;st.session_state['ind_ma5_rising']=rising
    simple=pd.DataFrame(st.session_state.get('ind_ma5_simple',[]));rising=pd.DataFrame(st.session_state.get('ind_ma5_rising',[]))
    if simple.empty and rising.empty:
        st.info('버튼을 눌러 개별종목 백테스트를 실행하세요.');return
    st.subheader('전략 성과 비교')
    st.dataframe(pd.DataFrame([_summary(simple,'단순 5개월선 돌파'),_summary(rising,'돌파 + 5개월선 상승')]),use_container_width=True,hide_index=True)
    detail=rising if not rising.empty else simple
    st.subheader('종목별 과거 돌파 기록')
    cfg={'매수가':st.column_config.NumberColumn('매수가',format='%d원'),'5개월선':st.column_config.NumberColumn('5개월선',format='%d원')}
    st.dataframe(detail.round(2),use_container_width=True,hide_index=True,column_config=cfg)
    st.info('핵심: 평균보다 중앙값, 3·6·12개월 성과, +10/+20% 도달률, 최대하락을 함께 비교하세요. 표본 수가 적으면 결론을 약하게 봐야 합니다.')
