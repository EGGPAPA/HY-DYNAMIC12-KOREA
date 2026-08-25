import base64
import json
import os
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from monthly_ma5_ui import render_monthly_ma5_tab
from individual_stock_ma5_backtest_ui import render_individual_stock_ma5_backtest

REPO="EGGPAPA/HY-DYNAMIC12-KOREA";BRANCH="main";HOLDINGS_PATH="holdings.json";API_URL=f"https://api.github.com/repos/{REPO}/contents/{HOLDINGS_PATH}";KIS_BASE_URL="https://openapi.koreainvestment.com:9443"
def won(v):
    try:return f"{int(round(float(v))):,}원"
    except:return "-"
def secret_value(name,default=""):
    try:
        value=st.secrets.get(name,default)
        if value:return str(value).strip()
    except Exception:pass
    return os.getenv(name,default).strip()
def github_pat():return secret_value("GITHUB_PAT")
def headers():
    h={"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","Cache-Control":"no-cache"}
    if github_pat():h["Authorization"]=f"Bearer {github_pat()}"
    return h
def load_holdings():
    r=requests.get(API_URL,headers=headers(),params={"ref":BRANCH},timeout=20)
    if r.status_code==404:return [],None
    if r.status_code!=200:raise RuntimeError(f"holdings.json 읽기 실패: HTTP {r.status_code} / {r.text[:250]}")
    d=r.json();return json.loads(base64.b64decode(d["content"]).decode("utf-8") or "[]"),d.get("sha")
def save_holdings(rows,sha,message):
    if not github_pat():raise RuntimeError("Streamlit Secrets에 GITHUB_PAT를 등록하세요.")
    p={"message":message,"content":base64.b64encode(json.dumps(rows,ensure_ascii=False,indent=2).encode()).decode(),"branch":BRANCH}
    if sha:p["sha"]=sha
    r=requests.put(API_URL,headers=headers(),json=p,timeout=20)
    if r.status_code not in (200,201):raise RuntimeError(f"holdings.json 저장 실패: HTTP {r.status_code} / {r.text[:250]}")
def find_active(rows,code):
    c=code.strip().zfill(6)
    for i,row in enumerate(rows):
        if str(row.get("ticker","")).zfill(6)==c and str(row.get("status","holding")).lower()!="closed":return i,row
    return None,None
def kis_ready():return bool(secret_value("KIS_APP_KEY") and secret_value("KIS_APP_SECRET"))
@st.cache_data(ttl=60*60*20,show_spinner=False)
def kis_access_token(k,s):
    try:
        r=requests.post(f"{KIS_BASE_URL}/oauth2/tokenP",json={"grant_type":"client_credentials","appkey":k,"appsecret":s},timeout=10);return r.json().get("access_token") if r.ok else None
    except:return None
@st.cache_data(ttl=10,show_spinner=False)
def get_kis_price(code):
    k=secret_value("KIS_APP_KEY");s=secret_value("KIS_APP_SECRET");t=kis_access_token(k,s) if k and s else None
    if not t:return None
    try:
        r=requests.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",headers={"authorization":f"Bearer {t}","appkey":k,"appsecret":s,"tr_id":"FHKST01010100","custtype":"P"},params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":str(code).zfill(6)},timeout=10);v=(r.json().get("output") or {}).get("stck_prpr");return float(v) if r.ok and v and float(v)>0 else None
    except:return None
def yf_symbol(code,market):return f"{str(code).zfill(6)}.{'KQ' if str(market).upper()=='KOSDAQ' else 'KS'}"
@st.cache_data(ttl=60,show_spinner=False)
def get_yahoo_price(code,market):
    try:
        h=yf.Ticker(yf_symbol(code,market)).history(period="5d",interval="1d",auto_adjust=False);s=pd.to_numeric(h["Close"],errors="coerce").dropna();return float(s.iloc[-1]) if not s.empty else None
    except:return None
def get_current_price(code,market):
    p=get_kis_price(code)
    if p is not None:return p,"KIS"
    p=get_yahoo_price(code,market);return (p,"Yahoo") if p is not None else (None,"없음")
def normalized_purchases(row):
    ps=row.get("purchases")
    if isinstance(ps,list) and ps:return [p for p in ps if float(p.get("price",0) or 0)>0 and float(p.get("quantity",0) or 0)>0]
    a=float(row.get("average_price",0) or 0);q=float(row.get("quantity",0) or 0);return [{"price":a,"quantity":q,"executed_at":row.get("updated_at",""),"source":"기존보유"}] if a>0 and q>0 else []
def calc_position(ps):
    q=sum(float(p["quantity"]) for p in ps);c=sum(float(p["price"])*float(p["quantity"]) for p in ps);return q,c,c/q if q else 0
def sell_guide(avg,current):
    if avg<=0:return None,None,None,None,"판단불가","평균매수가 확인"
    s,a,b,c=avg*.97,avg*1.15,avg*1.20,avg*1.25
    if current is None:state,act="시세없음","현재가 갱신 필요"
    elif current<=s:state,act="🔴 손절선 이탈","손절/비중축소 검토"
    elif current>=c:state,act="🟣 3차 익절 구간","+25% 이상 · 분할익절/추세보유"
    elif current>=b:state,act="🔵 2차 익절 구간","+20% 이상 · 추가익절 검토"
    elif current>=a:state,act="🟡 1차 익절 구간","+15% 이상 · 일부익절 검토"
    else:state,act="🟢 보유 구간","보유 유지"
    return s,a,b,c,state,act

def render_holdings_tab():
    st.subheader("💼 보유종목 관리");st.caption("일반계좌에 등록한 모든 보유종목을 선택해 평가하고 매수·매도를 기록합니다. 연금 ETF는 연금저축에서 별도 관리합니다.")
    notice=st.session_state.pop("kr_holding_save_notice",None)
    if notice:st.success(notice)
    try:rows,sha=load_holdings()
    except Exception as e:st.error(str(e));rows,sha=[],None
    active=[x for x in rows if str(x.get("status","holding")).lower()!="closed" and x.get("enabled",True)]
    c1,c2,c3=st.columns(3);c1.metric("보유종목",len(active));c2.metric("GitHub 저장","준비됨" if github_pat() else "PAT 미설정");c3.metric("시세","KIS 우선" if kis_ready() else "Yahoo")
    details=[]
    if active:
        view=[]
        for r in active:
            ps=normalized_purchases(r);q,cost,avg=calc_position(ps);p,src=get_current_price(str(r.get("ticker","")).zfill(6),r.get("market","KOSPI"));val=p*q if p else None;pnl=val-cost if val is not None else None;ret=pnl/cost*100 if pnl is not None and cost else None;s,a,b,d,state,act=sell_guide(avg,p);details.append((r,q,cost,avg,p,src,val,pnl,ret,s,a,b,d,state,act));view.append({"종목코드":r.get("ticker"),"종목명":r.get("name"),"시장":r.get("market"),"평균매수가":won(avg),"수량":q,"현재가":won(p),"평가금액":won(val),"수익금":won(pnl),"수익률":f"{ret:+.2f}%" if ret is not None else "-","손절(-3%)":won(s),"1차(+15%)":won(a),"2차(+20%)":won(b),"3차(+25%)":won(d),"상태":state,"매도판단":act})
        st.dataframe(pd.DataFrame(view),use_container_width=True,hide_index=True)
        labels=[f"{x[0].get('name')} ({str(x[0].get('ticker','')).zfill(6)})" for x in details];selected=st.selectbox("🔎 평가할 보유종목",labels);x=details[labels.index(selected)];r,q,cost,avg,p,src,val,pnl,ret,s,a,b,d,state,act=x
        st.markdown(f"### 🧠 {r.get('name')} 종합판단");m1,m2,m3,m4=st.columns(4);m1.metric("현재가",won(p));m2.metric("평균매수가",won(avg));m3.metric("평가손익",won(pnl));m4.metric("수익률",f"{ret:+.2f}%" if ret is not None else "-");st.info(f"현재 판단: **{state} · {act}**");st.markdown("#### 🎯 실전 가격 가이드");g1,g2,g3,g4=st.columns(4);g1.metric("손절 기준",won(s));g2.metric("1차 익절",won(a));g3.metric("2차 익절",won(b));g4.metric("3차 익절",won(d));st.caption(f"시세 출처: {src} · 선택한 보유종목 기준 자동 평가")
        st.markdown("### 💸 매도 체결 등록")
        sell_labels=[f"{z[0].get('name')} ({str(z[0].get('ticker','')).zfill(6)})" for z in details]
        with st.form("kr_hold_sell_form"):
            sell_sel=st.selectbox("매도 종목",sell_labels);sx=details[sell_labels.index(sell_sel)];sr,sq,scost,savg,*_=sx;u,v=st.columns(2);sell_price=u.number_input("실제 체결 매도가(원)",min_value=0.0,step=1000.0);sell_qty=v.number_input("매도 수량",min_value=0.0,max_value=float(sq),step=1.0);sell_ok=st.form_submit_button("💸 매도 등록",type="primary",use_container_width=True)
        if sell_ok:
            if sell_price<=0 or sell_qty<=0:st.error("매도가와 매도수량을 입력하세요.")
            else:
                rows,sha=load_holdings();idx,old=find_active(rows,str(sr.get("ticker","")));ps=normalized_purchases(old);oq,ocost,oavg=calc_position(ps);sell_qty=min(float(sell_qty),oq);realized=(float(sell_price)-oavg)*sell_qty;realized_rate=(float(sell_price)/oavg-1)*100 if oavg else 0;now=datetime.now(timezone.utc).isoformat();sales=old.get("sales",[]) if isinstance(old.get("sales",[]),list) else [];sales.append({"price":float(sell_price),"quantity":sell_qty,"average_cost":oavg,"realized_pnl":realized,"realized_return_pct":realized_rate,"executed_at":now});remain=oq-sell_qty;old["sales"]=sales;old["quantity"]=remain;old["updated_at"]=now
                if remain<=0:old.update({"quantity":0,"status":"closed","enabled":False,"closed_at":now})
                rows[idx]=old;save_holdings(rows,sha,f"Register Korea sell {sr.get('ticker')}");st.success(f"매도 등록 완료 · 실현손익 {won(realized)} · 수익률 {realized_rate:+.2f}%" + (" · 전량매도 완료" if remain<=0 else f" · 잔여 {remain:g}주"));st.rerun()
    else:st.info("현재 등록된 보유종목이 없습니다.")
    with st.form("kr_hold_buy_form"):
        a,b,c=st.columns([1,2,1]);code=a.text_input("종목코드").strip();name=b.text_input("종목명").strip();market=c.selectbox("시장",["KOSPI","KOSDAQ"]);d,e=st.columns(2);price=d.number_input("실제 체결 매수가(원)",min_value=0.0,step=1000.0);qty=e.number_input("매수 수량",min_value=0.0,step=1.0);ok=st.form_submit_button("➕ 보유 등록 / 추가 매수",type="primary",use_container_width=True)
    if ok:
        code="".join(ch for ch in code if ch.isdigit()).zfill(6)
        missing=[]
        if len(code)!=6 or code=="000000":missing.append("6자리 종목코드")
        if price<=0:missing.append("실제 체결 매수가")
        if qty<=0:missing.append("매수 수량")
        if missing:
            st.error("저장되지 않았습니다. " + ", ".join(missing) + "을(를) 확인하세요.")
        else:
            try:
                rows,sha=load_holdings();idx,old=find_active(rows,code);now=datetime.now(timezone.utc).isoformat();trade={"price":float(price),"quantity":float(qty),"executed_at":now,"source":"추가매수" if old else "신규매수"}
                if old is None:
                    ps=[trade];nq,_,na=calc_position(ps);rows.append({"ticker":code,"name":name or code,"market":market,"status":"holding","average_price":na,"quantity":nq,"purchases":ps,"sales":[],"enabled":True,"updated_at":now})
                else:
                    ps=normalized_purchases(old)+[trade];nq,_,na=calc_position(ps);old.update({"name":name or old.get("name"),"market":market,"average_price":na,"quantity":nq,"purchases":ps,"updated_at":now});rows[idx]=old
                save_holdings(rows,sha,f"Update Korea holding {code}")
                st.session_state["kr_holding_save_notice"]=f"{name or code} ({code}) 저장 완료 · 평균매수가 {won(na)} · 총 {nq:g}주"
                st.rerun()
            except Exception as e:
                st.error(f"보유종목 저장 실패: {e}")
    closed=[x for x in rows if str(x.get("status","")).lower()=="closed" or x.get("sales")]
    if closed:
        with st.expander("📕 매도완료 / 거래기록"):
            history=[]
            for rr in closed:
                for sale in rr.get("sales",[]) or []:history.append({"종목":rr.get("name"),"종목코드":rr.get("ticker"),"매도가":won(sale.get("price")),"매도수량":sale.get("quantity"),"매도시 평균단가":won(sale.get("average_cost")),"실현손익":won(sale.get("realized_pnl")),"실현수익률":f"{float(sale.get('realized_return_pct',0)):+.2f}%","매도일":str(sale.get("executed_at",''))[:10]})
            if history:st.dataframe(pd.DataFrame(history),use_container_width=True,hide_index=True)

def _load_backtest_universe():
    try:
        import app as _app;universe,_=_app.get_full_universe();return universe
    except Exception:
        try:
            df=pd.read_csv("korea_universe.csv",dtype={"종목코드":str});df["종목코드"]=df["종목코드"].astype(str).str.zfill(6);return df
        except Exception:return pd.DataFrame(columns=["종목코드","종목명","시장"])
def install_holdings_tab():
    if getattr(st,"_hy_korea_holdings_tab_installed",False):return
    original_tabs=st.tabs;original_dataframe=st.dataframe
    def wrapped_tabs(labels,*args,**kwargs):
        labels=list(labels)
        if "💼 보유종목" in labels:return original_tabs(labels,*args,**kwargs)
        containers=original_tabs(labels+["🔥 5개월선 돌파","📈 개별종목 5개월선 백테스트","💼 보유종목"],*args,**kwargs)
        with containers[-3]:render_monthly_ma5_tab()
        with containers[-2]:render_individual_stock_ma5_backtest(_load_backtest_universe())
        with containers[-1]:render_holdings_tab()
        return containers[:-3]
    def wrapped_dataframe(data=None,*args,**kwargs):
        try:
            if isinstance(data,pd.DataFrame) and "KOREA점수" in data.columns:data=data.drop(columns=["KOREA점수"])
        except:pass
        return original_dataframe(data,*args,**kwargs)
    st.tabs=wrapped_tabs;st.dataframe=wrapped_dataframe;st._hy_korea_holdings_tab_installed=True
