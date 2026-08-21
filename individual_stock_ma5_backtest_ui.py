"""개별종목 월봉 5개월 이동평균선 3년 백테스트."""
from __future__ import annotations
import numpy as np, pandas as pd, streamlit as st, yfinance as yf

def _code(v):
 s=str(v).strip().replace('.0',''); return s.zfill(6) if s.isdigit() else s

def _ticker(code,market): return f"{_code(code)}.{('KQ' if str(market).upper()=='KOSDAQ' else 'KS')}"

def _monthly(t):
 d=yf.download(t,period='5y',interval='1mo',auto_adjust=True,progress=False)
 if d is None or d.empty:return pd.DataFrame()
 if isinstance(d.columns,pd.MultiIndex):d.columns=d.columns.get_level_values(0)
 d=d.dropna(subset=['Close']).copy(); d['MA5']=d.Close.rolling(5).mean(); d['SLOPE']=d.MA5.pct_change()*100
 return d

def _events(d,code,name,market,years):
 if d.empty:return []
 if getattr(d.index,'tz',None) is not None:d.index=d.index.tz_localize(None)
 cutoff=pd.Timestamp.now()-pd.DateOffset(years=years); out=[]
 for i in range(5,len(d)):
  if d.index[i]<cutoff:continue
  c,m,pc,pm=map(float,[d.Close.iloc[i],d.MA5.iloc[i],d.Close.iloc[i-1],d.MA5.iloc[i-1]])
  if np.isfinite(m) and np.isfinite(pm) and pc<=pm and c>m:
   fut=d.Close.iloc[i+1:i+13].astype(float); r={'종목코드':_code(code),'종목명':name,'시장':market,'돌파월':d.index[i].strftime('%Y-%m'),'매수가':round(c),'5개월선':round(m),'기울기(%)':round(float(d.SLOPE.iloc[i]),2)}
   for n in (1,3,6,12):r[f'{n}개월수익률(%)']=round((float(d.Close.iloc[i+n])/c-1)*100,2) if i+n<len(d) else np.nan
   r['최고수익률(%)']=round((fut.max()/c-1)*100,2) if len(fut) else np.nan;r['최대하락률(%)']=round((fut.min()/c-1)*100,2) if len(fut) else np.nan;r['+10%도달']=bool(len(fut) and fut.max()>=c*1.1);r['+20%도달']=bool(len(fut) and fut.max()>=c*1.2);r['상승5개월선']=float(d.SLOPE.iloc[i])>0;out.append(r)
 return out

def _summary(df,label):
 o={'전략':label,'신호수':len(df)}
 for n in (1,3,6,12):
  s=pd.to_numeric(df[f'{n}개월수익률(%)'],errors='coerce').dropna();o[f'{n}개월표본']=len(s);o[f'{n}개월승률(%)']=round((s>0).mean()*100,1) if len(s) else np.nan;o[f'{n}개월평균(%)']=round(s.mean(),2) if len(s) else np.nan;o[f'{n}개월중앙값(%)']=round(s.median(),2) if len(s) else np.nan
 o['+10%도달률(%)']=round(df['+10%도달'].mean()*100,1) if len(df) else np.nan;o['+20%도달률(%)']=round(df['+20%도달'].mean()*100,1) if len(df) else np.nan;o['평균최대하락(%)']=round(pd.to_numeric(df['최대하락률(%)'],errors='coerce').mean(),2) if len(df) else np.nan;return o

def render_individual_stock_ma5_backtest(universe_df):
 st.header('📈 개별종목 월봉 5개월선 · 3년 백테스트');st.caption('ETF가 아니라 KOSPI·KOSDAQ 개별종목의 월말 확정 신규 상향돌파를 검증합니다.')
 years=st.selectbox('기간',[3,5],index=0,key='stock_ma5_years');limit=st.number_input('검사 종목 수',5,1000,300,5,key='stock_ma5_limit')
 st.caption('현재 상장종목 기준이며 거래비용·세금·상장폐지 종목은 반영하지 않은 이벤트 연구입니다.')
 if universe_df is not None and len(universe_df)<100:st.warning(f'전체시장 대신 제한된 후보군 {len(universe_df):,}개를 사용합니다.')
 if not st.button('🚀 개별종목 백테스트 실행',use_container_width=True,key='stock_ma5_run'):return
 if universe_df is None or universe_df.empty:st.error('종목 유니버스가 없습니다.');return
 cm={str(c).lower():c for c in universe_df.columns};cc=cm.get('종목코드') or cm.get('code');nc=cm.get('종목명') or cm.get('name');mc=cm.get('시장') or cm.get('market')
 if cc is None or nc is None:st.error('종목코드/종목명 컬럼이 필요합니다.');return
 rows=[];u=universe_df.head(int(limit));p=st.progress(0)
 for j,(_,r) in enumerate(u.iterrows(),1):
  market=str(r[mc]) if mc else 'KOSPI'
  try:rows+=_events(_monthly(_ticker(r[cc],market)),r[cc],r[nc],market,years)
  except Exception:pass
  p.progress(j/len(u))
 p.empty();df=pd.DataFrame(rows)
 if df.empty:st.warning('돌파 기록이 없습니다.');return
 rising=df[df['상승5개월선']].copy();st.subheader('전략 성과 비교');st.dataframe(pd.DataFrame([_summary(df,'단순 5개월선 돌파'),_summary(rising,'돌파 + 5개월선 상승')]),use_container_width=True,hide_index=True)
 st.subheader('종목별 과거 돌파 기록');st.dataframe(df.sort_values('돌파월',ascending=False),use_container_width=True,hide_index=True,column_config={'매수가':st.column_config.NumberColumn(format='%,d원'),'5개월선':st.column_config.NumberColumn(format='%,d원')})
 st.info('3·6·12개월 평균수익률, +10/+20% 도달률과 최대하락률을 함께 비교하세요.')

