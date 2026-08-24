from datetime import datetime
import pandas as pd
import streamlit as st
import yfinance as yf


def _clip(x, lo=0.0, hi=100.0):
    try: return max(lo, min(hi, float(x)))
    except Exception: return lo


def _won(x):
    try: return f"{int(round(float(x))):,}원"
    except Exception: return "-"


def _symbol(code, market):
    return f"{str(code).zfill(6)}.{'KQ' if str(market).upper() == 'KOSDAQ' else 'KS'}"


@st.cache_data(ttl=1800, show_spinner=False)
def _monthly(code, market, period="5y"):
    try:
        h=yf.Ticker(_symbol(code,market)).history(period=period,interval="1mo",auto_adjust=True)
        return h.dropna(subset=["Close"]) if h is not None and not h.empty else pd.DataFrame()
    except Exception:return pd.DataFrame()


def _ma5_live(code,market):
    h=_monthly(code,market,"3y")
    if len(h)<7:return {"score":0,"hit":False,"label":"데이터없음","gap":None}
    c=pd.to_numeric(h["Close"],errors="coerce").dropna();ma=c.rolling(5).mean()
    if len(c)<7:return {"score":0,"hit":False,"label":"데이터없음","gap":None}
    now,prev=float(c.iloc[-1]),float(c.iloc[-2]);mn,mp=float(ma.iloc[-1]),float(ma.iloc[-2]);gap=(now/mn-1)*100;slope=(mn/mp-1)*100 if mp else 0;bo=prev<=mp and now>mn
    if bo and slope>0:score,label=100,"🔥 강한 신규돌파"
    elif bo:score,label=90,"🟢 신규돌파"
    elif now>mn and slope>0 and gap<=6:score,label=80,"🟢 MA5 위·상승"
    elif now>mn and slope>0:score,label=70,"🟡 상승추세·이격주의"
    elif now>mn:score,label=55,"🟡 MA5 위·기울기약함"
    else:score,label=25,"🔴 MA5 아래"
    return {"score":score,"hit":score>=60,"label":label,"gap":round(gap,2)}


def _market_score(regime): return 100 if str(regime)=="강세장" else (30 if str(regime)=="약세장" else 70)


def build_integrated_rows(rows,jump_rows,regime="중립장"):
    jm={str(r.get("_종목코드","")).zfill(6):r for r in (jump_rows or [])};out=[]
    for rank,row in enumerate(rows or [],1):
        code=str(row.get("_종목코드","")).zfill(6);market=row.get("_시장","KOSPI");top=_clip(row.get("종합점수",0));th=rank<=12 or top>=72;j=jm.get(code,{});cv=j.get("Conviction");wealth=50 if cv is None or pd.isna(cv) else _clip(cv);wh=cv is not None and not pd.isna(cv) and float(cv)>=72;ma=_ma5_live(code,market);hits=int(th)+int(wh)+int(ma["hit"]);total=top*.35+wealth*.35+float(ma["score"])*.20+_market_score(regime)*.10;hot=str(row.get("과열",""))=="과열";total-=5 if hot else 0;total=round(_clip(total),1)
        if total>=80 and hits>=2 and not hot:action,entry="🟢 적극매수 후보","1차 25% 분할"
        elif total>=70 and hits>=2 and not hot:action,entry="🟢 1차 분할매수","1차 15~20%"
        elif total>=60:action,entry="🟡 눌림·관찰","신규매수 대기"
        else:action,entry="🔴 제외","매수하지 않음"
        if hot and total>=70:action,entry="🟡 과열·눌림대기","추격매수 금지"
        match="🔥 3/3" if hits==3 else ("🟢 2/3" if hits==2 else ("🟡 1/3" if hits==1 else "⚪ 0/3"))
        out.append({"종목":row.get("종목명"),"현재가":row.get("현재가"),"통합점수":total,"교차포착":match,"TOP12":"✅" if th else "-","TOP점수":round(top,1),"부의점프":"✅" if wh else ("데이터대기" if cv is None or pd.isna(cv) else "-"),"Conviction":None if cv is None or pd.isna(cv) else round(float(cv),1),"5개월선":ma["label"],"MA5이격":ma["gap"],"과열":row.get("과열"),"최종판정":action,"행동":entry,"1차매수가":row.get("1차 매수가"),"2차매수가":row.get("2차 매수가")})
    return sorted(out,key=lambda x:x["통합점수"],reverse=True)


def _extract(batch,sym,field):
    try:
        if isinstance(batch.columns,pd.MultiIndex):
            if sym in batch.columns.get_level_values(0):s=batch[sym][field]
            elif sym in batch.columns.get_level_values(1):s=batch.xs(sym,axis=1,level=1)[field]
            else:return pd.Series(dtype=float)
        else:s=batch[field]
        return pd.to_numeric(s,errors="coerce")
    except Exception:return pd.Series(dtype=float)


@st.cache_data(ttl=3600,show_spinner=False)
def _download(symbols):
    try:return yf.download(list(symbols),period="5y",interval="1mo",auto_adjust=True,group_by="ticker",threads=True,progress=False)
    except Exception:return pd.DataFrame()


@st.cache_data(ttl=3600,show_spinner=False)
def _kospi():
    try:return yf.Ticker("^KS11").history(period="5y",interval="1mo",auto_adjust=True).dropna(subset=["Close"])
    except Exception:return pd.DataFrame()


def _market_hist(k,dt):
    if k is None or k.empty:return 70
    c=pd.to_numeric(k["Close"],errors="coerce").dropna();key=pd.Timestamp(dt).tz_localize(None).to_period("M");idx=[i for i,x in enumerate(c.index) if pd.Timestamp(x).tz_localize(None).to_period("M")<=key]
    if not idx or idx[-1]<9:return 70
    i=idx[-1];p=float(c.iloc[i]);m5=float(c.iloc[i-4:i+1].mean());m10=float(c.iloc[i-9:i+1].mean());return 100 if p>m5>m10 else (30 if p<m5<m10 else 70)


def _hist_score(c,v,i,ms):
    if i<11:return None
    p=float(c.iloc[i]);p3=float(c.iloc[i-3]);p6=float(c.iloc[i-6]);m5=float(c.iloc[i-4:i+1].mean());pm5=float(c.iloc[i-5:i].mean());mom3=(p/p3-1)*100;mom6=(p/p6-1)*100;top=_clip(52+mom3*1.4+mom6*.55+(10 if p>m5 else -10));vr=1
    if v is not None and len(v)>i:
        pv=pd.to_numeric(v.iloc[i-5:i],errors="coerce").dropna()
        try:vr=float(v.iloc[i])/float(pv.mean()) if len(pv) and pv.mean()>0 else 1
        except:vr=1
    high=float(c.iloc[i-11:i+1].max());wealth=_clip(50+mom3*1.35+(vr-1)*22+((p/high)-.90)*80);slope=(m5/pm5-1)*100 if pm5 else 0;prev=float(c.iloc[i-1]);bo=prev<=pm5 and p>m5;gap=(p/m5-1)*100
    ma=100 if bo and slope>0 else (90 if bo else (80 if p>m5 and slope>0 and gap<=6 else (70 if p>m5 and slope>0 else (55 if p>m5 else 25))))
    hits=int(top>=65)+int(wealth>=65)+int(ma>=60);return round(_clip(top*.35+wealth*.35+ma*.20+ms*.10),2),hits


def run_integrated_backtest(universe,limit=100):
    if universe is None or universe.empty:return pd.DataFrame()
    work=universe.head(min(int(limit),len(universe))).copy();syms=tuple(_symbol(r["종목코드"],r["시장"]) for _,r in work.iterrows());batch=_download(syms);k=_kospi();events=[];ths=(65,70,75,80)
    if batch is None or batch.empty:return pd.DataFrame()
    for _,r in work.iterrows():
        sym=_symbol(r["종목코드"],r["시장"]);c=_extract(batch,sym,"Close").dropna();v=_extract(batch,sym,"Volume").reindex(c.index)
        if len(c)<18:continue
        prev={t:False for t in ths}
        for i in range(11,len(c)-1):
            z=_hist_score(c,v,i,_market_hist(k,c.index[i]))
            if not z:continue
            score,hits=z;entry=float(c.iloc[i]);future=c.iloc[i+1:min(i+13,len(c))];rets=(future.astype(float)/entry-1)*100 if not future.empty else pd.Series(dtype=float)
            for t in ths:
                on=score>=t and hits>=2
                if on and not prev[t]:
                    e={"기준":t,"종목":r["종목명"],"신호월":str(pd.Timestamp(c.index[i]).date())[:7],"통합점수":score,"포착수":hits,"12개월최고":float(rets.max()) if len(rets) else None,"12개월최대하락":float(rets.min()) if len(rets) else None}
                    for m in (3,6,12):e[f"{m}개월"]=(float(c.iloc[i+m])/entry-1)*100 if i+m<len(c) else None
                    events.append(e)
                prev[t]=on
    return pd.DataFrame(events)


def _num(s):return pd.to_numeric(s,errors="coerce").dropna()


def _summary(events):
    rows=[]
    for th in (65,70,75,80):
        x=events[events["기준"]==th] if not events.empty else pd.DataFrame();r={"기준점수":th,"진입기준":f"{th}점+ · 2/3 이상","신호수":len(x)}
        for m in (3,6,12):
            s=_num(x[f"{m}개월"]) if not x.empty else pd.Series(dtype=float);r[f"{m}개월승률"]=(s.gt(0).mean()*100) if len(s) else None;r[f"{m}개월평균"]=s.mean() if len(s) else None
        annual=_num(x["12개월"]) if not x.empty else pd.Series(dtype=float);r["12개월중앙값"]=annual.median() if len(annual) else None;r["12개월최악"]=annual.min() if len(annual) else None;r["12개월최고수익"]=annual.max() if len(annual) else None;r["손실확률"]=(annual.lt(0).mean()*100) if len(annual) else None
        mx=_num(x["12개월최고"]) if not x.empty else pd.Series(dtype=float);dd=_num(x["12개월최대하락"]) if not x.empty else pd.Series(dtype=float);r["+10%도달률"]=(mx.ge(10).mean()*100) if len(mx) else None;r["+20%도달률"]=(mx.ge(20).mean()*100) if len(mx) else None;r["+30%도달률"]=(mx.ge(30).mean()*100) if len(mx) else None;r["평균최대하락"]=dd.mean() if len(dd) else None;rows.append(r)
    return pd.DataFrame(rows)


def _recommend(summary):
    if summary is None or summary.empty:return None
    s=summary.copy();maxsig=max(float(s["신호수"].max()),1)
    for col in ["6개월승률","12개월승률","12개월중앙값","+20%도달률","평균최대하락"]:s[col]=pd.to_numeric(s[col],errors="coerce")
    s["균형점수"]=(s["신호수"]/maxsig*15+s["6개월승률"].fillna(0)/100*20+s["12개월승률"].fillna(0)/100*25+((s["12개월중앙값"].fillna(-20)+20).clip(0,50)/50)*20+((s["+20%도달률"].fillna(0))/100)*10+((s["평균최대하락"].fillna(-30)+30).clip(0,30)/30)*10)
    valid=s[s["신호수"]>=max(10,maxsig*.12)]
    if valid.empty:valid=s[s["신호수"]>0]
    if valid.empty:return None
    return valid.sort_values("균형점수",ascending=False).iloc[0]


def render_integrated_decision(rows,jump_rows,regime="중립장",universe=None):
    st.divider();st.markdown("## 🎯 TOP12 × 부의 점프 × 5개월선 통합 매수판정");st.caption("3개 모두를 필수로 묶지 않고 2/3 동시포착부터 정상 후보로 인정합니다.")
    data=build_integrated_rows(rows,jump_rows,regime)
    if not data:st.info("통합할 후보 데이터가 없습니다.");return
    buy=[x for x in data if x["최종판정"].startswith("🟢")];a,b,c,d=st.columns(4);a.metric("🟢 매수후보",len(buy));b.metric("🔥 3/3",sum(x["교차포착"].startswith("🔥") for x in data));c.metric("🟢 2/3",sum(x["교차포착"].startswith("🟢") for x in data));d.metric("시장환경",regime)
    if buy:st.success("오늘 우선 검토: "+", ".join(f"{x['종목']} {x['통합점수']:.1f}점" for x in buy[:3]))
    show=[]
    for i,x in enumerate(data[:15],1):show.append({"순위":i,"종목":x["종목"],"통합점수":x["통합점수"],"교차포착":x["교차포착"],"TOP12":x["TOP12"],"부의점프":x["부의점프"],"5개월선":x["5개월선"],"MA5이격":x["MA5이격"],"최종판정":x["최종판정"],"행동":x["행동"],"현재가":_won(x["현재가"]),"1차매수가":_won(x["1차매수가"]),"2차매수가":_won(x["2차매수가"])})
    st.dataframe(pd.DataFrame(show),use_container_width=True,hide_index=True);st.info("80점↑ 적극매수 후보 · 70~79점 1차 분할매수 · 60~69점 눌림/관찰 · 60점 미만 제외")
    st.markdown("### 🧪 통합점수 백테스트");st.caption("과거 월봉 가격·거래량만 사용한 탐색형 백테스트입니다. TOP12·부의점프는 프록시이므로 실전 공식과 완전히 동일하지 않습니다.")
    if universe is None or universe.empty:
        universe=pd.DataFrame([{"종목코드":x.get("_종목코드"),"종목명":x.get("종목명"),"시장":x.get("_시장","KOSPI")} for x in rows])
    max_n=max(20,min(300,len(universe)));limit=st.number_input("백테스트 종목 수",min_value=20,max_value=max_n,value=min(100,max_n),step=20,key="integrated_bt_limit")
    if st.button("▶ 통합 매수기준 백테스트 실행",type="primary",use_container_width=True,key="integrated_bt_run"):
        with st.spinner("65·70·75·80점 기준 비교 중..."):ev=run_integrated_backtest(universe,int(limit))
        st.session_state["integrated_bt_events"]=ev.to_dict("records") if not ev.empty else []
    ev=pd.DataFrame(st.session_state.get("integrated_bt_events",[]))
    if not ev.empty:
        summary=_summary(ev);rec=_recommend(summary)
        if rec is not None:
            st.markdown("#### 🏆 HY 추천 기준점수")
            r1,r2,r3,r4=st.columns(4);r1.metric("추천",f"{int(rec['기준점수'])}점+");r2.metric("신호수",f"{int(rec['신호수'])}회");r3.metric("12개월 승률",f"{rec['12개월승률']:.1f}%" if pd.notna(rec['12개월승률']) else "-");r4.metric("평균 MDD",f"{rec['평균최대하락']:+.1f}%" if pd.notna(rec['평균최대하락']) else "-")
            st.success(f"현재 백테스트에서는 **{int(rec['기준점수'])}점 이상 + 2/3 포착**이 매수기회·승률·수익률·하락폭의 균형이 가장 좋습니다. 이 값은 백테스트를 다시 실행하면 자동 재계산됩니다.")
        disp=summary.copy()
        for col in ["3개월승률","6개월승률","12개월승률","손실확률","+10%도달률","+20%도달률","+30%도달률"]:disp[col]=disp[col].map(lambda v:f"{v:.1f}%" if pd.notna(v) else "-")
        for col in ["3개월평균","6개월평균","12개월평균","12개월중앙값","12개월최악","12개월최고수익","평균최대하락"]:disp[col]=disp[col].map(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")
        st.markdown("#### 65·70·75·80점 비교");st.dataframe(disp.drop(columns=["기준점수"]),use_container_width=True,hide_index=True);st.caption("HY 추천은 일부 급등 종목의 영향을 줄이기 위해 12개월 평균 대신 중앙값을 반영하고, 승률·+20% 도달률·평균 MDD를 함께 평가합니다.")
        st.info("평균과 중앙값의 차이가 크면 일부 대박 종목이 평균을 끌어올렸을 가능성이 큽니다. 전형적인 결과는 중앙값에 더 가깝게 해석하세요.")
        with st.expander("과거 통합신호 상세"):st.dataframe(ev.sort_values(["기준","신호월"],ascending=[True,False]).head(300),use_container_width=True,hide_index=True)

