import json
from pathlib import Path
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
from korea_holdings_ui import install_holdings_tab

try:
    from pykrx import stock
    PYKRX_OK = True
except Exception:
    stock = None
    PYKRX_OK = False

st.set_page_config(page_title="HY DYNAMIC12 KOREA V3.9", page_icon="🇰🇷", layout="wide")
install_holdings_tab()

SEOUL = ZoneInfo("Asia/Seoul")
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

UNIVERSE_FILE = Path("korea_universe.csv")
FLOW_FILE = Path("investor_flow.csv")
EXPORT_FILE = Path("export_history.csv")
WATCHLIST_FILE = DATA_DIR / "korea_watchlist.json"

FINAL_TOP_N = 12
DEEP_CANDIDATE_COUNT = 120
YF_CHUNK = 180
MIN_PRICE = 1000
MIN_AVG_VALUE = 2_000_000_000

FALLBACK_UNIVERSE = [
    ("005930","삼성전자","KOSPI"), ("000660","SK하이닉스","KOSPI"),
    ("035420","NAVER","KOSPI"), ("035720","카카오","KOSPI"),
    ("005380","현대차","KOSPI"), ("000270","기아","KOSPI"),
    ("207940","삼성바이오로직스","KOSPI"), ("068270","셀트리온","KOSPI"),
    ("373220","LG에너지솔루션","KOSPI"), ("006400","삼성SDI","KOSPI"),
    ("005490","POSCO홀딩스","KOSPI"), ("051910","LG화학","KOSPI"),
    ("012450","한화에어로스페이스","KOSPI"), ("042660","한화오션","KOSPI"),
    ("009540","HD한국조선해양","KOSPI"), ("034020","두산에너빌리티","KOSPI"),
    ("105560","KB금융","KOSPI"), ("055550","신한지주","KOSPI"),
    ("086790","하나금융지주","KOSPI"), ("316140","우리금융지주","KOSPI"),
    ("028260","삼성물산","KOSPI"), ("066570","LG전자","KOSPI"),
    ("003670","포스코퓨처엠","KOSPI"), ("323410","카카오뱅크","KOSPI"),
    ("247540","에코프로비엠","KOSDAQ"), ("086520","에코프로","KOSDAQ"),
    ("196170","알테오젠","KOSDAQ"), ("028300","HLB","KOSDAQ"),
    ("058470","리노공업","KOSDAQ"), ("403870","HPSP","KOSDAQ"),
    ("214150","클래시스","KOSDAQ"), ("039030","이오테크닉스","KOSDAQ"),
]


def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def market_open():
    now = datetime.now(SEOUL)
    return now.weekday() < 5 and time(9, 0) <= now.time() <= time(15, 30)


def clip(x, lo, hi):
    return float(np.clip(float(x), lo, hi))


def latest_business_day():
    d = datetime.now(SEOUL).date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def load_export_history():
    if not EXPORT_FILE.exists(): return pd.DataFrame()
    try:
        df = pd.read_csv(EXPORT_FILE); df["date"] = pd.to_datetime(df["date"], errors="coerce")
        for c in ["export_yoy", "semi_yoy"]:
            if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["date"]).sort_values("date")
    except Exception: return pd.DataFrame()


def load_csv_universe():
    if not UNIVERSE_FILE.exists(): return pd.DataFrame(columns=["종목코드", "종목명", "시장"])
    try:
        df = pd.read_csv(UNIVERSE_FILE, dtype={"종목코드": str}); df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
        return df[["종목코드", "종목명", "시장"]].copy()
    except Exception: return pd.DataFrame(columns=["종목코드", "종목명", "시장"])


@st.cache_data(ttl=3600)
def get_full_universe():
    rows=[]; date=latest_business_day()
    if PYKRX_OK:
        try:
            for market in ["KOSPI","KOSDAQ"]:
                for code in stock.get_market_ticker_list(date, market=market):
                    try: name=stock.get_market_ticker_name(code)
                    except Exception: name=code
                    rows.append((str(code).zfill(6),name,market))
            if len(rows)>=100: return pd.DataFrame(rows,columns=["종목코드","종목명","시장"]),f"KRX 전체 종목목록 · {date}"
        except Exception: rows=[]
    merged=[]; seen=set(); fb=load_csv_universe()
    if not fb.empty:
        for _,r in fb.iterrows():
            code=str(r["종목코드"]).zfill(6)
            if code not in seen: merged.append((code,str(r["종목명"]),str(r["시장"]).upper())); seen.add(code)
    for code,name,market in FALLBACK_UNIVERSE:
        if code not in seen: merged.append((code,name,market)); seen.add(code)
    return pd.DataFrame(merged,columns=["종목코드","종목명","시장"]),"CSV + 기본 후보군 fallback"


def load_flow_csv():
    if not FLOW_FILE.exists(): return pd.DataFrame()
    try:
        df=pd.read_csv(FLOW_FILE,dtype={"종목코드":str}); df["종목코드"]=df["종목코드"].astype(str).str.zfill(6)
        for c in ["외국인순매수","기관순매수"]:
            if c not in df.columns: df[c]=0
            df[c]=pd.to_numeric(df[c],errors="coerce").fillna(0)
        return df
    except Exception: return pd.DataFrame()


@st.cache_data(ttl=1800)
def get_auto_flow():
    date=latest_business_day(); flow={}; auto_ok=False; msg=""
    if PYKRX_OK:
        try:
            for investor,key in [("외국인","외국인순매수"),("기관합계","기관순매수")]:
                total={}
                for market in ["KOSPI","KOSDAQ"]:
                    df=stock.get_market_net_purchases_of_equities_by_ticker(date,date,market,investor)
                    if df is not None and not df.empty and "순매수거래대금" in df.columns:
                        for code,val in pd.to_numeric(df["순매수거래대금"],errors="coerce").fillna(0).items(): total[str(code).zfill(6)]=float(val)
                for code,val in total.items(): flow.setdefault(code,{})[key]=val
            auto_ok=len(flow)>0
            if auto_ok: msg=f"KRX 투자자 수급 자동수집 · {date}"
        except Exception as e: msg=f"KRX 수급 자동수집 실패: {type(e).__name__}"
    fb=load_flow_csv()
    if not fb.empty:
        for _,r in fb.iterrows():
            code=str(r["종목코드"]).zfill(6); flow.setdefault(code,{})
            for key in ["외국인순매수","기관순매수"]:
                if float(flow[code].get(key,0) or 0)==0: flow[code][key]=float(r.get(key,0) or 0)
        msg+=(" + " if msg else "")+"CSV 보완"
    return flow,auto_ok,msg or "수급 데이터 없음"


def yf_symbol(code,market): return f"{code}.KS" if market=="KOSPI" else f"{code}.KQ"

@st.cache_data(ttl=900)
def yf_history(ticker,period="1y"):
    try: return yf.Ticker(ticker).history(period=period,interval="1d",auto_adjust=True).dropna()
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=1200)
def get_single_history(symbol,period="6mo"):
    try: return yf.Ticker(symbol).history(period=period,interval="1d",auto_adjust=True).dropna()
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=1200)
def download_chunk(symbols,period="3mo"):
    if not symbols:return pd.DataFrame()
    try:return yf.download(tickers=list(symbols),period=period,interval="1d",auto_adjust=True,group_by="ticker",threads=True,progress=False)
    except Exception:return pd.DataFrame()


def extract_one(batch,symbol):
    if batch.empty:return pd.DataFrame()
    try:
        if isinstance(batch.columns,pd.MultiIndex):
            l0=batch.columns.get_level_values(0); l1=batch.columns.get_level_values(1)
            if symbol in l0:return batch[symbol].dropna(how="all")
            if symbol in l1:return batch.xs(symbol,axis=1,level=1).dropna(how="all")
        return batch.dropna(how="all")
    except Exception:return pd.DataFrame()


def build_market_screen(universe,flow_map,progress=None):
    rows=[]; total=len(universe)
    for start in range(0,total,YF_CHUNK):
        part=universe.iloc[start:start+YF_CHUNK].copy(); symbols=[yf_symbol(r["종목코드"],r["시장"]) for _,r in part.iterrows()]; batch=download_chunk(tuple(symbols),"3mo")
        for _,r in part.iterrows():
            code=str(r["종목코드"]).zfill(6); h=extract_one(batch,yf_symbol(code,r["시장"]))
            if len(h)<22:continue
            close=pd.to_numeric(h["Close"],errors="coerce").dropna(); volume=pd.to_numeric(h["Volume"],errors="coerce").dropna()
            if len(close)<22 or len(volume)<20:continue
            price=float(close.iloc[-1]); avg_value=float((close.tail(20)*volume.tail(20)).mean())
            if price<MIN_PRICE or avg_value<MIN_AVG_VALUE:continue
            fm=flow_map.get(code,{})
            rows.append({"종목코드":code,"종목명":r["종목명"],"시장":r["시장"],"현재가":price,"평균거래대금":avg_value,"등락률":(price/float(close.iloc[-2])-1)*100,"20일수익률":(price/float(close.iloc[-21])-1)*100,"외국인순매수":float(fm.get("외국인순매수",0) or 0),"기관순매수":float(fm.get("기관순매수",0) or 0)})
        if progress is not None:progress.progress(min((start+len(part))/max(total,1),1.0))
    df=pd.DataFrame(rows)
    if df.empty:return df
    for c in ["평균거래대금","등락률","20일수익률","외국인순매수","기관순매수"]:df[c]=pd.to_numeric(df[c],errors="coerce").fillna(0)
    df["유동성백분위"]=df["평균거래대금"].rank(pct=True)*100; df["당일강도백분위"]=df["등락률"].rank(pct=True)*100; df["20일강도백분위"]=df["20일수익률"].rank(pct=True)*100
    flow_present=(df["외국인순매수"].abs().sum()+df["기관순매수"].abs().sum())>0
    if flow_present:
        df["외국인백분위"]=df["외국인순매수"].rank(pct=True)*100; df["기관백분위"]=df["기관순매수"].rank(pct=True)*100
        df["1차점수"]=df["유동성백분위"]*.25+df["당일강도백분위"]*.15+df["20일강도백분위"]*.15+df["외국인백분위"]*.225+df["기관백분위"]*.225
    else:
        df["외국인백분위"]=50.;df["기관백분위"]=50.;df["1차점수"]=df["유동성백분위"]*.40+df["당일강도백분위"]*.25+df["20일강도백분위"]*.35
    return df.sort_values("1차점수",ascending=False).reset_index(drop=True)


def fundamental_score(code,market):
    score=50.
    try:
        info=yf.Ticker(yf_symbol(code,market)).info or {}; pe=info.get("trailingPE");pbr=info.get("priceToBook");roe=info.get("returnOnEquity");growth=info.get("earningsGrowth")
        if isinstance(pe,(int,float)) and pe>0:score+=12 if pe<=15 else (6 if pe<=25 else (-8 if pe>=50 else 0))
        if isinstance(pbr,(int,float)) and pbr>0:score+=8 if pbr<=1.5 else (3 if pbr<=3 else (-6 if pbr>=6 else 0))
        if isinstance(roe,(int,float)):score+=clip((roe*100-8)*.8,-10,15)
        if isinstance(growth,(int,float)):score+=clip(growth*100*.35,-10,12)
    except Exception:pass
    return clip(score,0,100)


def deep_analyze(screen):
    candidates=screen.head(max(DEEP_CANDIDATE_COUNT,FINAL_TOP_N*4)).copy(); symbols=[yf_symbol(r["종목코드"],r["시장"]) for _,r in candidates.iterrows()];batch=download_chunk(tuple(symbols),"6mo");rows=[]
    flow_present=(screen["외국인순매수"].abs().sum()+screen["기관순매수"].abs().sum())>0
    for _,r in candidates.iterrows():
        code=r["종목코드"];h=extract_one(batch,yf_symbol(code,r["시장"]))
        if len(h)<61:h=get_single_history(yf_symbol(code,r["시장"]),"6mo")
        if len(h)<61:continue
        close=pd.to_numeric(h["Close"],errors="coerce").dropna();volume=pd.to_numeric(h["Volume"],errors="coerce").dropna()
        if len(close)<61 or len(volume)<20:continue
        price=float(close.iloc[-1]);ma20=float(close.tail(20).mean());ma60=float(close.tail(60).mean());r20=(price/float(close.iloc[-21])-1)*100;r60=(price/float(close.iloc[-61])-1)*100;vol_ratio=float(volume.tail(5).mean()/max(float(volume.tail(20).mean()),1));high=float(close.tail(min(120,len(close))).max());high_pos=price/high*100 if high>0 else 0
        trend=clip(45+(18 if price>ma20>ma60 else 0)+clip(r20,-15,20)*1.2+clip(r60,-25,35)*.35,0,100);price_pos=clip(100-abs(high_pos-92)*4,0,100);volume_score=clip(50+(vol_ratio-1)*35,0,100);rs=clip(50+clip(r20,-20,30)*1.1+clip(r60,-30,50)*.45,0,100);fund=fundamental_score(code,r["시장"])
        quality=float(r["외국인백분위"])*.30+float(r["기관백분위"])*.30+rs*.25+fund*.15 if flow_present else min(rs*.625+fund*.375,67.9)
        opinion="⚪ 실제 수급 데이터 대기" if not flow_present else ("🟢 강한 매수우위" if quality>=80 else ("🔵 매수우위" if quality>=68 else ("🟡 중립·개선중" if quality>=55 else ("🟠 수급 혼조" if quality>=42 else "🔴 매도우위"))))
        total=trend*.30+price_pos*.15+volume_score*.15+quality*.40;overheated=high_pos>=99 and r20>=15;buy1=int(round(max(price*.93,min(price*.995,ma20))/100.)*100);support2=ma60 if ma60<buy1 else buy1*.97;buy2=int(round(max(price*.88,min(buy1*.97,support2))/100.)*100);stop=int(round((buy2*.97)/100.)*100)
        rows.append({"종목명":r["종목명"],"현재가":int(round(price)),"등락률":round(float(r["등락률"]),2),"종합점수":round(total,1),"수급·질적 종합의견":opinion,"_수급질적점수":round(quality,1),"_실제수급":flow_present,"_종목코드":code,"_시장":r["시장"],"과열":"⚠️ 과열" if overheated else "정상","1차 매수가":buy1,"2차 매수가":buy2,"손절가(3%)":stop})
        if len(rows)>=DEEP_CANDIDATE_COUNT:break
    return rows


def kospi_regime():
    d=yf_history("^KS11","1y")
    if len(d)<200:return "중립장"
    c=d["Close"].dropna();p=float(c.iloc[-1]);ma50=float(c.tail(50).mean());ma200=float(c.tail(200).mean());r20=(p/float(c.iloc[-21])-1)*100;exp=load_export_history();export_ok=None
    if not exp.empty and "export_yoy" in exp.columns:
        s=exp["export_yoy"].dropna()
        if not s.empty:export_ok=float(s.iloc[-1])>0
    if p>ma50>ma200 and r20>0 and export_ok is not False:return "강세장"
    if p<ma50 and p<ma200 and r20<0 and export_ok is False:return "약세장"
    return "중립장"


def apply_relative(rows,regime):
    rows=sorted(rows,key=lambda x:x["종합점수"],reverse=True);n=len(rows);floor,active_pct=((78,10) if regime=="강세장" else ((82,3) if regime=="약세장" else (78,5)))
    for i,r in enumerate(rows,1):
        pct=i/max(n,1)*100;r["상대순위"]=f"상위 {pct:.1f}%";quality=float(r["_수급질적점수"]);active=r["_실제수급"] and r["종합점수"]>=floor and pct<=active_pct and quality>=68 and r["과열"]=="정상"
        judgment="🟢 적극매수" if active else ("🔵 매수후보" if r["_실제수급"] and r["종합점수"]>=75 and quality>=55 else ("🟡 관찰" if r["종합점수"]>=65 else ("🟠 대기" if r["종합점수"]>=55 else "🔴 제외")))
        if r["과열"]!="정상" and judgment in ("🟢 적극매수","🔵 매수후보"):judgment="🟡 관찰"
        r.update({"판정":judgment,"KOREA점수":r["종합점수"],"판정점수":r["종합점수"],"시장상태":regime,"수급대응":"대기" if not r.get("_실제수급",False) else ("통과" if quality>=55 else "대기")})
    return rows,floor,active_pct


st.title("🇰🇷 HY DYNAMIC12 KOREA V3.9")
st.caption("KOSPI · KOSDAQ 전체시장 + KRX 종목목록/수급 + yfinance 가격·거래량 + KOSPI vs 수출 · USA판 형식 TOP12 · 적극매수 가격대 · 적극매수 종목 점멸 · 최종 3종목 후보")
tabs=st.tabs(["🌐 시장환경","🔎 전체시장 분석","🏆 TOP12","🔔 카카오 준비","⚙️ 설정"])

with tabs[0]:
    regime=kospi_regime();c1,c2=st.columns(2);c1.metric("현재 시장 레짐",regime);c2.metric("한국 정규장","OPEN" if market_open() else "CLOSED","09:00~15:30 KST")
    st.subheader("KOSPI vs 한국 수출 YoY");kd=yf_history("^KS11","5y");exp=load_export_history()
    if not kd.empty:
        k=pd.DataFrame({"KOSPI":kd["Close"]});
        if getattr(k.index,"tz",None) is not None:k.index=k.index.tz_localize(None)
        m=k.resample("ME").last();m["KOSPI YoY"]=m["KOSPI"].pct_change(12)*100
        if not exp.empty:
            e=exp.set_index("date");use=[c for c in ["export_yoy","semi_yoy"] if c in e.columns];chart=m[["KOSPI YoY"]].join(e[use],how="outer").sort_index();chart=chart.rename(columns={"export_yoy":"수출 YoY","semi_yoy":"반도체 수출 YoY"});st.line_chart(chart)
        else:st.line_chart(m[["KOSPI YoY"]])

with tabs[1]:
    st.subheader("🔎 KOSPI + KOSDAQ 전체시장 분석");st.info("KRX에서는 종목목록·투자자수급만 받고, 전 종목 가격/거래량은 yfinance 배치조회로 계산합니다.")
    if st.button("① 전체시장 자동분석 실행",type="primary",use_container_width=True):
        universe,uni_source=get_full_universe()
        if universe.empty:st.error("KOSPI/KOSDAQ 종목목록을 가져오지 못했습니다.")
        else:
            flow_map,flow_auto_ok,flow_msg=get_auto_flow();st.write(f"종목목록: **{len(universe):,}개** · {uni_source}");st.write(f"수급: **{flow_msg}**");bar=st.progress(0)
            with st.spinner("전체시장 가격·거래량 1차 스크리닝 중..."):screen=build_market_screen(universe,flow_map,bar)
            bar.empty()
            if screen.empty:st.error("가격/유동성 조건을 통과한 종목이 없습니다.")
            else:
                st.session_state["eligible_count"]=len(screen);st.session_state["candidate_count"]=min(DEEP_CANDIDATE_COUNT,len(screen));st.session_state["flow_auto_ok"]=flow_auto_ok;st.session_state["flow_msg"]=flow_msg;st.write(f"전체 적격종목 **{len(screen):,}개** → 정밀분석 후보 **{min(DEEP_CANDIDATE_COUNT,len(screen))}개**")
                with st.spinner("상위 후보 정밀분석 중..."):rows=deep_analyze(screen)
                regime=kospi_regime();rows,floor,active_pct=apply_relative(rows,regime);st.session_state["kr_rows"]=rows;st.session_state["kr_regime"]=regime;top=rows[:FINAL_TOP_N];active_watch=[]
                for r in top:
                    if r["판정"].startswith("🟢 적극매수"):active_watch.append({"ticker":r["_종목코드"],"name":r["종목명"],"market":r["_시장"],"score":r["종합점수"],"relative_rank":r["상대순위"],"opinion":r["수급·질적 종합의견"]})
                save_json(WATCHLIST_FILE,active_watch);st.success(f"정밀분석 {len(rows)}개 완료 · {regime} · 적극매수 기준 {floor}점 + 상위 {active_pct}%")
                if not flow_auto_ok:st.warning("KRX 자동수급이 확보되지 않아 적극매수 판정은 잠금 상태입니다. 실제 수급 확보 전에는 관찰/대기만 표시합니다.")

with tabs[2]:
    st.subheader("🏆 TOP12");rows=st.session_state.get("kr_rows",[])
    if not rows:st.info("먼저 '전체시장 분석'에서 자동분석을 실행하세요.")
    else:
        top=rows[:FINAL_TOP_N];display=pd.DataFrame([{"순위":i,"종목":r["종목명"],"현재가(원)":r["현재가"],"KOREA점수":r["KOREA점수"],"판정점수":r["판정점수"],"시장상태":r["시장상태"],"상대순위":r["상대순위"],"수급대응":r["수급대응"],"과열":r["과열"],"판정":r["판정"],"1차매수가(원)":r["1차 매수가"],"2차매수가(원)":r["2차 매수가"],"손절가(3%)(원)":r["손절가(3%)"]} for i,r in enumerate(top,1)]);st.dataframe(display,use_container_width=True,hide_index=True)
        st.caption("1차/2차 매수가는 20일선·60일선과 눌림목을 반영한 적극매수 가격대입니다. 손절가는 2차 매수가 대비 -3%입니다.");st.markdown("## 최종 3종목 후보");active=[r for r in top if str(r["판정"]).startswith("🟢 적극매수")]
        if not active:st.warning("현재 적극매수 종목이 없어 3종목을 억지로 선정하지 않습니다. 매수후보는 추적만 합니다.")
        else:st.dataframe(pd.DataFrame([{"종목":r["종목명"],"판정":r["판정"],"종합점수":r["종합점수"],"1차매수가":r["1차 매수가"],"2차매수가":r["2차 매수가"],"손절가(3%)":r["손절가(3%)"]} for r in active[:3]]),use_container_width=True,hide_index=True)

with tabs[3]:
    st.subheader("🔔 카카오 자동감시 준비");st.write("TOP12 중 🟢 적극매수 종목만 data/korea_watchlist.json에 저장합니다.")
    if not st.session_state.get("flow_auto_ok",False):st.warning("현재 자동수급이 확보되지 않으면 적극매수 카카오 알림은 생성하지 않습니다.")

with tabs[4]:
    st.markdown("""### V3.9 핵심
- TOP12 / KOSPI + KOSDAQ 전체시장
- yfinance 가격·거래량 스크리닝
- KRX 투자자 순매수 독립 수집
- 실제 수급 미확보 시 적극매수 잠금
- 💼 보유종목 영구저장/추가매수 평균단가/전량매도 관리 추가
""")
