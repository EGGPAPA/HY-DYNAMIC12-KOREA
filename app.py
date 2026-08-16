import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, time
from zoneinfo import ZoneInfo
from pathlib import Path
import json

st.set_page_config(page_title="HY DYNAMIC12 KOREA V3.1", page_icon="🇰🇷", layout="wide")

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SEOUL = ZoneInfo("Asia/Seoul")
WATCHLIST_FILE = DATA_DIR / "korea_watchlist.json"
UNIVERSE_FILE = Path("korea_universe.csv")
FLOW_FILE = Path("investor_flow.csv")
EXPORT_FILE = Path("export_history.csv")

DEFAULT_UNIVERSE = [
    ("005930","삼성전자","KOSPI"),("000660","SK하이닉스","KOSPI"),
    ("035420","NAVER","KOSPI"),("035720","카카오","KOSPI"),
    ("005380","현대차","KOSPI"),("000270","기아","KOSPI"),
    ("373220","LG에너지솔루션","KOSPI"),("005490","POSCO홀딩스","KOSPI"),
    ("051910","LG화학","KOSPI"),("006400","삼성SDI","KOSPI"),
    ("012450","한화에어로스페이스","KOSPI"),("042660","한화오션","KOSPI"),
    ("009540","HD한국조선해양","KOSPI"),("034020","두산에너빌리티","KOSPI"),
    ("055550","신한지주","KOSPI"),("105560","KB금융","KOSPI"),
    ("068270","셀트리온","KOSPI"),("207940","삼성바이오로직스","KOSPI"),
    ("247540","에코프로비엠","KOSDAQ"),("086520","에코프로","KOSDAQ"),
]

def save_json(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

def market_open():
    now=datetime.now(SEOUL)
    return now.weekday()<5 and time(9,0)<=now.time()<=time(15,30)

def load_universe():
    rows=[]
    if UNIVERSE_FILE.exists():
        try:
            df=pd.read_csv(UNIVERSE_FILE,dtype={"종목코드":str})
            for _,r in df.iterrows():
                code=str(r.get("종목코드","")).zfill(6)
                if code:
                    rows.append((code,str(r.get("종목명",code)),str(r.get("시장","KOSPI")).upper()))
        except Exception:
            pass
    seen={x[0] for x in rows}
    for item in DEFAULT_UNIVERSE:
        if item[0] not in seen:
            rows.append(item); seen.add(item[0])
    return pd.DataFrame(rows,columns=["종목코드","종목명","시장"])

def load_investor_flow():
    if not FLOW_FILE.exists():
        return pd.DataFrame(columns=["종목코드","외국인순매수","기관순매수","기준일"])
    try:
        df=pd.read_csv(FLOW_FILE,dtype={"종목코드":str})
        df["종목코드"]=df["종목코드"].astype(str).str.zfill(6)
        for c in ["외국인순매수","기관순매수"]:
            if c not in df.columns: df[c]=0
            df[c]=pd.to_numeric(df[c],errors="coerce").fillna(0)
        return df
    except Exception:
        return pd.DataFrame(columns=["종목코드","외국인순매수","기관순매수","기준일"])

def load_export_history():
    if not EXPORT_FILE.exists(): return pd.DataFrame()
    try:
        df=pd.read_csv(EXPORT_FILE)
        df["date"]=pd.to_datetime(df["date"],errors="coerce")
        for c in ["export_yoy","semi_yoy"]:
            if c in df.columns: df[c]=pd.to_numeric(df[c],errors="coerce")
        return df.dropna(subset=["date"]).sort_values("date")
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=900)
def get_history(ticker,period="1y"):
    try:
        return yf.Ticker(ticker).history(period=period,auto_adjust=True).dropna()
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def get_info(ticker):
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}

def yf_symbol(code,market):
    return f"{code}.KS" if market=="KOSPI" else f"{code}.KQ"

def clip(x,lo,hi):
    return float(np.clip(float(x),lo,hi))

def fundamental_score(info):
    score=50.0
    roe=info.get("returnOnEquity")
    if isinstance(roe,(int,float)):
        score += clip((roe*100-8)*1.3,-15,20)
    rg=info.get("revenueGrowth")
    if isinstance(rg,(int,float)):
        score += clip(rg*100*0.7,-12,15)
    eg=info.get("earningsGrowth")
    if isinstance(eg,(int,float)):
        score += clip(eg*100*0.5,-12,15)
    pe=info.get("trailingPE")
    if isinstance(pe,(int,float)) and pe>0:
        score += 8 if pe<=15 else (4 if pe<=25 else (-8 if pe>=50 else 0))
    return clip(score,0,100)

def flow_component(v):
    try: v=float(v)
    except Exception: v=0.0
    if v==0: return 50.0
    return clip(50 + np.sign(v)*min(40,np.log10(abs(v)+1)*6),0,100)

def rs_score(r20,r60):
    return clip(50+clip(r20,-20,30)*1.1+clip(r60,-30,50)*0.45,0,100)

def combined_opinion(foreign_net,inst_net,rs,fund,flow_available):
    score=flow_component(foreign_net)*0.30+flow_component(inst_net)*0.30+rs*0.25+fund*0.15
    if not flow_available:
        text="🟡 수급 데이터 대기 · 질적흐름 양호" if score>=70 else ("⚪ 수급 데이터 대기 · 질적흐름 중립" if score>=55 else "🟠 수급 데이터 대기 · 질적흐름 약함")
    elif score>=80: text="🟢 강한 매수우위"
    elif score>=68: text="🔵 매수우위"
    elif score>=55: text="🟡 중립·개선중"
    elif score>=42: text="🟠 수급 혼조"
    else: text="🔴 매도우위"
    return round(score,1),text

def analyze_one(code,name,market,foreign_net=0,inst_net=0,flow_available=False):
    ticker=yf_symbol(code,market)
    d=get_history(ticker,"1y")
    if len(d)<120: return None
    c=d["Close"].dropna(); v=d["Volume"].dropna()
    if len(c)<120 or len(v)<20: return None
    price=float(c.iloc[-1]); ma20=float(c.tail(20).mean()); ma60=float(c.tail(60).mean())
    r20=(price/float(c.iloc[-21])-1)*100; r60=(price/float(c.iloc[-61])-1)*100
    vr=float(v.tail(5).mean()/max(float(v.tail(20).mean()),1.0))
    high120=float(c.tail(120).max()); highpos=price/high120*100 if high120>0 else 0
    trend=clip(45+(18 if price>ma20>ma60 else 0)+clip(r20,-15,20)*1.2+clip(r60,-25,35)*0.35,0,100)
    pricepos=clip(100-abs(highpos-92)*4,0,100)
    volume=clip(50+(vr-1)*35,0,100)
    rs=rs_score(r20,r60)
    fund=fundamental_score(get_info(ticker))
    qscore,qop=combined_opinion(foreign_net,inst_net,rs,fund,flow_available)
    total=trend*0.30+pricepos*0.15+volume*0.15+qscore*0.40
    overheated=highpos>=99 and r20>=15
    return {
        "종목명":name,"현재가":int(round(price)),"종합점수":round(total,1),
        "추세":round(trend,1),"가격위치":round(pricepos,1),"거래량":round(volume,1),
        "수급·질적 종합의견":qop,"상대순위":"","과열":"⚠️ 과열" if overheated else "정상",
        "판정":"","_수급질적점수":qscore,"_종목코드":code,"_시장":market
    }

def kospi_regime():
    d=get_history("^KS11","1y")
    if len(d)<200: return "중립장"
    c=d["Close"].dropna(); p=float(c.iloc[-1]); m50=float(c.tail(50).mean()); m200=float(c.tail(200).mean())
    r20=(p/float(c.iloc[-21])-1)*100
    exp=load_export_history(); export_ok=None
    if not exp.empty and "export_yoy" in exp.columns:
        s=exp["export_yoy"].dropna()
        if not s.empty: export_ok=float(s.iloc[-1])>0
    if p>m50>m200 and r20>0 and export_ok is not False: return "강세장"
    if p<m50 and p<m200 and r20<0 and export_ok is False: return "약세장"
    return "중립장"

def apply_relative(rows,regime):
    rows=sorted(rows,key=lambda x:x["종합점수"],reverse=True); n=len(rows)
    floor,pct=(78,10) if regime=="강세장" else ((82,3) if regime=="약세장" else (78,5))
    for i,r in enumerate(rows,1):
        rankpct=i/max(n,1)*100; r["상대순위"]=f"상위 {rankpct:.1f}%"
        q=float(r["_수급질적점수"])
        active=r["종합점수"]>=floor and rankpct<=pct and q>=68 and r["과열"]=="정상"
        if active: j="🟢 적극매수"
        elif r["종합점수"]>=75 and q>=55: j="🔵 매수후보"
        elif r["종합점수"]>=65: j="🟡 관찰"
        elif r["종합점수"]>=55: j="🟠 대기"
        else: j="🔴 제외"
        if r["과열"]!="정상" and j in ("🟢 적극매수","🔵 매수후보"): j="🟡 관찰"
        r["판정"]=j
    return rows,floor,pct

def color_judgment(v):
    s=str(v)
    if "적극매수" in s: return "background-color:#153d2a;color:#59e391;font-weight:700"
    if "매수후보" in s: return "background-color:#173a63;color:#74b9ff;font-weight:700"
    if "관찰" in s: return "background-color:#594a12;color:#ffd65a;font-weight:700"
    if "대기" in s: return "background-color:#5b3511;color:#ffad4d;font-weight:700"
    if "제외" in s: return "background-color:#5b2020;color:#ff7777;font-weight:700"
    return ""

def color_opinion(v):
    s=str(v)
    if "강한 매수우위" in s: return "color:#59e391;font-weight:700"
    if "매수우위" in s: return "color:#74b9ff;font-weight:700"
    if "중립" in s or "데이터 대기" in s: return "color:#ffd65a;font-weight:700"
    if "혼조" in s or "약함" in s: return "color:#ffad4d;font-weight:700"
    if "매도우위" in s: return "color:#ff7777;font-weight:700"
    return ""

st.title("🇰🇷 HY DYNAMIC12 KOREA V3.1")
st.caption("KOSPI · KOSDAQ 상대평가 + 수급·질적 종합의견 + KOSPI vs 수출 + 과열필터")
tabs=st.tabs(["🌐 시장환경","🔎 종목분석","🏆 TOP10","🔔 카카오 준비","⚙️ 설정"])

with tabs[0]:
    regime=kospi_regime()
    c1,c2=st.columns(2)
    c1.metric("현재 시장 레짐",regime)
    c2.metric("한국 정규장","OPEN" if market_open() else "CLOSED","09:00~15:30 KST")
    st.subheader("KOSPI vs 한국 수출 YoY")
    kd=get_history("^KS11","5y"); exp=load_export_history()
    if not kd.empty:
        k=pd.DataFrame({"KOSPI":kd["Close"]})
        if getattr(k.index,"tz",None) is not None: k.index=k.index.tz_localize(None)
        m=k.resample("ME").last(); m["KOSPI YoY"]=m["KOSPI"].pct_change(12)*100
        if not exp.empty:
            e=exp.set_index("date"); use=[c for c in ["export_yoy","semi_yoy"] if c in e.columns]
            chart=m[["KOSPI YoY"]].join(e[use],how="outer").sort_index().rename(columns={"export_yoy":"수출 YoY","semi_yoy":"반도체 수출 YoY"})
            st.line_chart(chart)
        else:
            st.line_chart(m[["KOSPI YoY"]])

with tabs[1]:
    st.info("외국인·기관 실제 수급은 investor_flow.csv를 사용합니다. 값이 0이면 임의 수급을 만들지 않고 '수급 데이터 대기'로 표시합니다.")
    if st.button("① KOSPI·KOSDAQ 분석 실행",type="primary",use_container_width=True):
        universe=load_universe(); flow=load_investor_flow()
        fmap={}; favail={}
        for _,x in flow.iterrows():
            code=str(x["종목코드"]).zfill(6); f=float(x.get("외국인순매수",0) or 0); i=float(x.get("기관순매수",0) or 0)
            fmap[code]=(f,i); favail[code]=(f!=0 or i!=0)
        rows=[]; bar=st.progress(0); status=st.empty()
        for j,x in universe.iterrows():
            code=str(x["종목코드"]).zfill(6); name=str(x["종목명"]); market=str(x["시장"]).upper()
            f,i=fmap.get(code,(0,0)); status.write(f"분석 중: {name}")
            try:
                r=analyze_one(code,name,market,f,i,favail.get(code,False))
                if r: rows.append(r)
            except Exception: pass
            bar.progress((j+1)/max(len(universe),1))
        regime=kospi_regime(); rows,floor,pct=apply_relative(rows,regime)
        st.session_state["kr_rows"]=rows; st.session_state["kr_regime"]=regime
        active=[{"ticker":r["_종목코드"],"name":r["종목명"],"market":r["_시장"],"score":r["종합점수"],"relative_rank":r["상대순위"],"opinion":r["수급·질적 종합의견"]} for r in rows[:10] if r["판정"].startswith("🟢 적극매수")]
        save_json(WATCHLIST_FILE,active); bar.empty(); status.empty()
        st.success(f"{len(rows)}종목 분석 완료 · {regime} · 적극매수 기준 {floor}점 + 상위 {pct}%")

with tabs[2]:
    st.subheader("🏆 TOP10")
    rows=st.session_state.get("kr_rows",[])
    if not rows:
        st.info("먼저 '종목분석' 탭에서 분석을 실행하세요.")
    else:
        top10=rows[:10]
        cols=["종목명","현재가","종합점수","추세","가격위치","거래량","수급·질적 종합의견","상대순위","과열","판정"]
        df=pd.DataFrame(top10)[cols].copy()
        st.dataframe(
            df.style.format({"현재가":"{:,.0f}","종합점수":"{:.1f}","추세":"{:.1f}","가격위치":"{:.1f}","거래량":"{:.1f}"})
            .map(color_opinion,subset=["수급·질적 종합의견"])
            .map(color_judgment,subset=["판정"]),
            use_container_width=True,hide_index=True,height=455
        )
        active=[x for x in top10 if x["판정"].startswith("🟢 적극매수")]
        c1,c2,c3=st.columns(3)
        c1.metric("TOP10",len(top10)); c2.metric("적극매수",len(active)); c3.metric("시장상태",st.session_state.get("kr_regime",kospi_regime()))
        st.caption("수급·질적 종합의견 = 외국인수급 30% + 기관수급 30% + 상대강도 25% + 펀더멘털 15%. 네 항목은 내부 계산만 하고 화면에는 하나의 의견으로 표시합니다.")

with tabs[3]:
    st.subheader("🔔 카카오 자동감시 준비")
    st.write("TOP10 중 최종 판정이 '🟢 적극매수'인 종목만 data/korea_watchlist.json에 저장합니다.")
    st.warning("🔵 매수후보 / 🟡 관찰 종목은 카카오 '매수' 알림 대상이 아닙니다.")

with tabs[4]:
    st.markdown("""
### V3.1 판정 구조
- 종합점수 = 추세 30% + 가격위치 15% + 거래량 15% + 수급·질적 종합 40%
- 수급·질적 종합 = 외국인 30% + 기관 30% + 상대강도 25% + 펀더멘털 15%
- 강세장: 78점 이상 + 상위 10%
- 중립장: 78점 이상 + 상위 5%
- 약세장: 82점 이상 + 상위 3%
- 🟢 적극매수 / 🔵 매수후보 / 🟡 관찰 / 🟠 대기 / 🔴 제외
- 종목코드와 시장은 화면에서 숨기고 종목명을 맨 앞에 표시합니다.
- 외국인·기관 수급 값이 없으면 임의로 만들지 않고 '수급 데이터 대기'로 표시합니다.
""")
