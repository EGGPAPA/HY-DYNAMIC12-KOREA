import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, time
from zoneinfo import ZoneInfo
from pathlib import Path
import json

st.set_page_config(page_title="HY DYNAMIC12 KOREA V3.0", page_icon="🇰🇷", layout="wide")
DATA = Path("data"); DATA.mkdir(exist_ok=True)
WATCH = DATA / "korea_watchlist.json"
SEOUL = ZoneInfo("Asia/Seoul")

# ---- 공식 데이터 입력/연결 지점 ----
# KRX 투자자별 매매/외국인 보유 데이터는 korea_data.py의 CSV 로더로 연결.
# 관세청 수출 데이터는 export_history.csv에 월별로 누적.
from korea_data import load_investor_flow, load_export_history

def save_json(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def market_open():
    now = datetime.now(SEOUL)
    return now.weekday() < 5 and time(9,0) <= now.time() <= time(15,30)

@st.cache_data(ttl=900)
def px(ticker, period="1y"):
    d = yf.Ticker(ticker).history(period=period, auto_adjust=True)
    return d.dropna()

def kospi_regime():
    d = px("^KS11")
    if len(d) < 200: return "중립장"
    c=d["Close"]; p=float(c.iloc[-1]); m50=float(c.tail(50).mean()); m200=float(c.tail(200).mean())
    r20=(p/float(c.iloc[-21])-1)*100
    exp=load_export_history()
    export_ok = False
    semi_ok = False
    if not exp.empty:
        last=exp.iloc[-1]
        export_ok=float(last.get("export_yoy",0))>0
        semi_ok=float(last.get("semi_yoy",0))>0
    if p>m50>m200 and r20>0 and export_ok: return "강세장"
    if p<m50 and p<m200 and r20<0 and not export_ok: return "약세장"
    return "중립장"

def score_one(code, market, foreign_net=0, inst_net=0):
    suffix=".KS" if market=="KOSPI" else ".KQ"
    d=px(code+suffix)
    if len(d)<120: return None
    c=d["Close"]; v=d["Volume"]
    p=float(c.iloc[-1]); ma20=float(c.tail(20).mean()); ma60=float(c.tail(60).mean())
    r20=(p/float(c.iloc[-21])-1)*100
    r60=(p/float(c.iloc[-61])-1)*100
    volr=float(v.tail(5).mean()/max(v.tail(20).mean(),1))
    high120=float(c.tail(120).max())
    highpos=p/high120*100

    trend=min(25,max(0, 12 + (8 if p>ma20>ma60 else 0) + np.clip(r20,-5,10)/2))
    price=min(15,max(0, 15-abs(highpos-92)*0.6))
    volume=min(15,max(0, 7 + (volr-1)*8))
    fscore=min(12,max(0, 6 + np.sign(foreign_net)*min(6,abs(foreign_net)/1e9)))
    iscore=min(12,max(0, 6 + np.sign(inst_net)*min(6,abs(inst_net)/1e9)))
    relative=min(10,max(0,5+r60/4))
    fundamental=8.0  # 재무 API 연결 전 중립값. 화면에 명시.
    total=float(np.clip(trend+price+volume+fscore+iscore+relative+fundamental,0,100))
    overheated = highpos>=99 and r20>=15
    return {"종목코드":code,"시장":market,"현재가":round(p,0),"종합점수":round(total,1),
            "추세":round(trend,1),"가격위치":round(price,1),"거래량":round(volume,1),
            "외국인수급":round(fscore,1),"기관수급":round(iscore,1),"상대강도":round(relative,1),
            "펀더멘털":fundamental,"20일수익률(%)":round(r20,1),"60일수익률(%)":round(r60,1),
            "외국인순매수":foreign_net,"기관순매수":inst_net,"과열":"과열" if overheated else "정상"}

def apply_relative(rows, regime):
    rows=sorted(rows,key=lambda x:x["종합점수"],reverse=True); n=len(rows)
    if regime=="강세장": floor, pct=78,10
    elif regime=="약세장": floor,pct=82,3
    else: floor,pct=78,5
    for i,r in enumerate(rows,1):
        rankpct=i/max(n,1)*100
        r["상대순위"]=f"상위 {rankpct:.1f}%"
        flow=(r["외국인순매수"]>0 or r["기관순매수"]>0)
        trend=r["추세"]>=15
        active=r["종합점수"]>=floor and rankpct<=pct and flow and trend and r["과열"]=="정상"
        if active: sig="적극매수"
        elif r["종합점수"]>=75: sig="매수후보"
        elif r["종합점수"]>=65: sig="관찰"
        else: sig="현금대기"
        r["판정"]=f"{sig} ({r['종합점수']:.1f}점 · {r['상대순위']})"
    return rows,floor,pct

st.title("🇰🇷 HY DYNAMIC12 KOREA V3.0")
st.caption("KOSPI · KOSDAQ 상대평가 + 외국인/기관 수급 + KOSPI vs 수출 + 과열필터")
tabs=st.tabs(["🌐 시장환경","🔎 종목분석","🏆 TOP12","🔔 카카오 준비","⚙️ 설정"])

with tabs[0]:
    regime=kospi_regime()
    st.metric("현재 시장 레짐", regime)
    st.metric("한국 정규장", "OPEN" if market_open() else "CLOSED", "09:00~15:30 KST")
    exp=load_export_history()
    if not exp.empty:
        st.subheader("KOSPI vs 한국 수출 YoY")
        kd=px("^KS11","5y")
        k=pd.DataFrame({"KOSPI":kd["Close"]})
        k.index=k.index.tz_localize(None) if getattr(k.index,"tz",None) else k.index
        monthly=k.resample("ME").last()
        monthly["KOSPI YoY"]=monthly["KOSPI"].pct_change(12)*100
        e=exp.copy(); e["date"]=pd.to_datetime(e["date"]); e=e.set_index("date")
        chart=monthly[["KOSPI YoY"]].join(e[["export_yoy","semi_yoy"]],how="outer").sort_index().ffill()
        st.line_chart(chart.rename(columns={"export_yoy":"수출 YoY","semi_yoy":"반도체 수출 YoY"}))
        st.dataframe(exp.tail(12),use_container_width=True,hide_index=True)
    else:
        st.warning("export_history.csv에 관세청 월별 수출 데이터를 넣으면 그래프가 활성화됩니다.")

with tabs[1]:
    st.info("korea_universe.csv에 종목코드/종목명/시장(KOSPI 또는 KOSDAQ)을 넣고, investor_flow.csv에 KRX 수급을 넣으세요.")
    if st.button("① KOSPI·KOSDAQ 분석 실행",type="primary",use_container_width=True):
        u=Path("korea_universe.csv")
        if not u.exists():
            st.error("korea_universe.csv가 없습니다.")
        else:
            uni=pd.read_csv(u,dtype={"종목코드":str}); flow=load_investor_flow()
            fmap={}
            if not flow.empty:
                for _,x in flow.iterrows():
                    fmap[str(x["종목코드"]).zfill(6)]=(float(x.get("외국인순매수",0)),float(x.get("기관순매수",0)))
            rows=[]; bar=st.progress(0)
            for j,x in uni.iterrows():
                code=str(x["종목코드"]).zfill(6); f,i=fmap.get(code,(0,0))
                try:
                    r=score_one(code,str(x["시장"]).upper(),f,i)
                    if r: r["종목명"]=x.get("종목명",code); rows.append(r)
                except Exception: pass
                bar.progress((j+1)/len(uni))
            regime=kospi_regime(); rows,floor,pct=apply_relative(rows,regime)
            st.session_state["kr_rows"]=rows
            save_json(WATCH,[r for r in rows[:12] if r["판정"].startswith("적극매수")])
            st.success(f"{len(rows)}종목 분석 · {regime} · 적극매수 기준 {floor}점+상위 {pct}%")
            st.dataframe(pd.DataFrame(rows[:30]),use_container_width=True,hide_index=True)

with tabs[2]:
    rows=st.session_state.get("kr_rows",[])
    if rows:
        top=rows[:12]
        st.dataframe(pd.DataFrame(top),use_container_width=True,hide_index=True)
        active=[x for x in top if x["판정"].startswith("적극매수")]
        st.metric("적극매수 종목",len(active))
        if not active: st.info("현재 적극매수 없음 — 매수후보는 추적만 합니다.")
    else:
        st.info("먼저 종목분석을 실행하세요.")

with tabs[3]:
    st.subheader("카카오 자동감시 연결 준비")
    st.write("적극매수 종목만 data/korea_watchlist.json에 저장됩니다.")
    st.write("다음 단계에서 한국 정규장용 GitHub Actions + Kakao monitor를 연결합니다.")
    st.warning("매수후보는 카카오 매수 알림 대상이 아닙니다.")

with tabs[4]:
    st.markdown("""
**V3.0 적극매수 기준**
- 강세장: 78점 이상 + 상위 10%
- 중립장: 78점 이상 + 상위 5%
- 약세장: 82점 이상 + 상위 3%
- 외국인 또는 기관 순매수 + 추세 통과 + 과열 아님
- 수출은 개별 종목 직접점수가 아니라 시장 레짐 필터에 사용
- 펀더멘털은 현재 중립 8점이며, 후속 버전에서 실제 재무 데이터로 교체
""")
