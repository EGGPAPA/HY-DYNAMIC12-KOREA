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
def compact_quantity(v):
    try:return f"{float(v):,.4f}".rstrip("0").rstrip(".")
    except:return "-"
def profit_text_color(value):
    try:
        number=float(str(value).replace(",","").replace("원","").replace("%","").replace("+","").strip())
    except:return ""
    if number>0:return "color:#ff4b4b;font-weight:700"
    if number<0:return "color:#4da3ff;font-weight:700"
    return "color:#aab2bf"
def secret_value(name,default=""):
    try:
        value=st.secrets.get(name,default)
        if value:return str(value).strip()
    except Exception:pass
    return os.getenv(name,default).strip()
def github_pat():return secret_value("GITHUB_PAT")
def kakao_ready():return bool(secret_value("KAKAO_REST_API_KEY") and secret_value("KAKAO_REFRESH_TOKEN"))
def refresh_kakao_token():
    data={"grant_type":"refresh_token","client_id":secret_value("KAKAO_REST_API_KEY"),"refresh_token":secret_value("KAKAO_REFRESH_TOKEN")}
    client_secret=secret_value("KAKAO_CLIENT_SECRET")
    if client_secret:data["client_secret"]=client_secret
    response=requests.post("https://kauth.kakao.com/oauth/token",data=data,timeout=20)
    if not response.ok:raise RuntimeError(f"카카오 토큰 갱신 실패: HTTP {response.status_code}")
    token=response.json().get("access_token")
    if not token:raise RuntimeError("카카오 access_token을 받지 못했습니다.")
    return token
def send_kakao_message(text):
    token=refresh_kakao_token()
    app_url=secret_value("KOREA_APP_URL","https://github.com/EGGPAPA/HY-DYNAMIC12-KOREA")
    template={"object_type":"text","text":text,"link":{"web_url":app_url,"mobile_web_url":app_url},"button_title":"보유종목 확인"}
    response=requests.post("https://kapi.kakao.com/v2/api/talk/memo/default/send",headers={"Authorization":f"Bearer {token}"},data={"template_object":json.dumps(template,ensure_ascii=False)},timeout=20)
    if not response.ok:raise RuntimeError(f"카카오 메시지 전송 실패: HTTP {response.status_code}")
def holding_price_alert_message(items):
    lines=["🔔 보유종목 가격 단계 알림",""]
    for item in items:
        lines.append(f"{item['event']} · {item['name']} ({item['code']})")
        lines.append(f"현재가 {won(item['price'])} / 기준가 {won(item['level'])} / 평균매수가 {won(item['average'])}")
        lines.append(f"수익률 {item['return']:+.2f}% · {item['action']}")
        lines.append("")
    lines.append("※ 자동감시 참고 알림이며 주문은 직접 확인 후 실행하세요.")
    return "\n".join(lines)
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

@st.cache_data(ttl=900,show_spinner=False)
def get_holding_assessment(code,market):
    symbol=yf_symbol(code,market)
    try:
        ticker=yf.Ticker(symbol)
        hist=ticker.history(period="1y",interval="1d",auto_adjust=False)
        close=pd.to_numeric(hist.get("Close"),errors="coerce").dropna()
        if len(close)<160:
            return None
        price=float(close.iloc[-1])
        ma20=float(close.tail(20).mean());ma40=float(close.tail(40).mean())
        ma60=float(close.tail(60).mean());ma120=float(close.tail(120).mean());ma160=float(close.tail(160).mean())
        ret20=(price/float(close.iloc[-21])-1)*100 if len(close)>21 else 0
        technical=sum([
            20 if price>ma20 else 0,
            20 if ma20>ma60 else 0,
            20 if price>ma120 else 0,
            20 if ret20>0 else 0,
            20 if price>ma160 else 0,
        ])
        fundamental=50.0;fundamental_note="기업지표 일부 미확인"
        try:
            info=ticker.info or {}
            pe=info.get("trailingPE");pbr=info.get("priceToBook");roe=info.get("returnOnEquity");growth=info.get("earningsGrowth")
            if isinstance(pe,(int,float)) and pe>0:fundamental+=10 if pe<=20 else (-6 if pe>=50 else 0)
            if isinstance(pbr,(int,float)) and pbr>0:fundamental+=8 if pbr<=2 else (-5 if pbr>=6 else 0)
            if isinstance(roe,(int,float)):fundamental+=max(-10,min(15,(roe*100-8)*.7))
            if isinstance(growth,(int,float)):fundamental+=max(-10,min(15,growth*100*.35))
            fundamental=max(0,min(100,fundamental));fundamental_note=f"PER {pe or '-'} · PBR {pbr or '-'} · ROE {roe if roe is not None else '-'}"
        except Exception:
            pass
        total=technical*.65+fundamental*.35
        return {"price":price,"ma20":ma20,"ma40":ma40,"ma60":ma60,"ma120":ma120,"ma160":ma160,
                "ret20":ret20,"technical":technical,"fundamental":fundamental,"total":total,
                "fundamental_note":fundamental_note}
    except Exception:
        return None


def render_holding_assessment(row,current):
    code=str(row.get("ticker","")).zfill(6);market=row.get("market","KOSPI");name=row.get("name") or code
    st.markdown(f"### 🧠 {name} 상세 종합판단")
    st.caption("선택한 보유종목의 차트·추세·모멘텀·기업지표를 공통 기준으로 계산합니다.")
    a=get_holding_assessment(code,market)
    if not a:
        st.warning("상세 종합판단에 필요한 160일 이상 가격 데이터를 가져오지 못했습니다.")
        return
    price=float(current) if current is not None else a["price"]
    gap40=(price/a["ma40"]-1)*100 if a["ma40"] else 0
    risk=price<a["ma160"]*.97
    if risk or a["total"]<45:hold="🔴 비중축소 검토";hold_note="160일선 위험구간 또는 종합점수 약화"
    elif a["total"]>=70 and price>=a["ma60"]:hold="🟢 계속 보유";hold_note="중장기 추세와 종합점수가 양호"
    else:hold="🟡 보유·점검";hold_note="보유는 유지하되 추세 회복 여부 확인"
    if risk:buy="🔴 추가매수 중지"
    elif 0<=gap40<=3 and a["total"]>=65:buy="🟢 1차 분할 검토"
    elif gap40>7:buy="🟡 눌림 매수 대기"
    elif gap40<0:buy="🟠 40일선 회복 대기"
    else:buy="🟠 소량 접근"
    c1,c2,c3=st.columns([1,1.4,1.4]);c1.metric("종합점수",f"{a['total']:.1f}점");c2.metric("① 보유 판단",hold);c3.metric("② 신규/추가매수",buy)
    st.info(f"보유 근거: **{hold_note}** · 현재가의 40일선 갭 **{gap40:+.1f}%**")
    first=min(price,a["ma40"]*1.01);second=min(first,a["ma60"]*.98);recovery=a["ma40"]*1.005;risk_line=a["ma160"]*.97
    st.markdown("#### 🎯 선택 종목 실전 가격 가이드")
    g1,g2,g3,g4=st.columns(4);g1.metric("1차 분할 참고",won(first));g2.metric("2차 분할 참고",won(second));g3.metric("추세회복 확인선",won(recovery));g4.metric("비중축소 경계선",won(risk_line))
    factors=pd.DataFrame([
        {"평가항목":"기술·추세","비중":"65%","점수":round(a["technical"],1)},
        {"평가항목":"기업지표","비중":"35%","점수":round(a["fundamental"],1)},
    ])
    st.dataframe(factors,use_container_width=True,hide_index=True,column_config={"점수":st.column_config.ProgressColumn(min_value=0,max_value=100,format="%.1f")})
    st.caption(f"현재가 {price:,.0f}원 · 20일선 {a['ma20']:,.0f}원 · 60일선 {a['ma60']:,.0f}원 · 160일선 {a['ma160']:,.0f}원 · 20일 수익률 {a['ret20']:+.1f}% · {a['fundamental_note']}")


def holding_snapshot(active):
    details=[];view=[];price_alerts=[]
    for row in active:
        purchases=normalized_purchases(row)
        quantity,cost,average=calc_position(purchases)
        price,source=get_current_price(str(row.get("ticker","")).zfill(6),row.get("market","KOSPI"))
        value=price*quantity if price else None
        profit=value-cost if value is not None else None
        return_rate=profit/cost*100 if profit is not None and cost else None
        stop,take1,take2,take3,state,action=sell_guide(average,price)
        details.append((row,quantity,cost,average,price,source,value,profit,return_rate,stop,take1,take2,take3,state,action))
        code=str(row.get("ticker","")).zfill(6);name=row.get("name") or code
        levels=[
            ("stop","🔴 손절선 이탈",stop,"손절/비중축소 검토",price<=stop),
            ("take15","🟡 1차(+15%) 도달",take1,"일부익절 검토",price>=take1),
            ("take20","🔵 2차(+20%) 도달",take2,"추가익절 검토",price>=take2),
            ("take25","🟣 3차(+25%) 도달",take3,"분할익절/추세보유",price>=take3),
        ] if price is not None else []
        price_alerts.extend({
            "id":f"{code}:{key}","code":code,"name":name,"event":event,
            "price":price,"level":level,"average":average,"return":return_rate,"action":alert_action,
        } for key,event,level,alert_action,reached in levels if reached)
        view.append({
            "종목코드":row.get("ticker"),"종목명":row.get("name"),"시장":row.get("market"),
            "평균매수가":won(average),"수량":compact_quantity(quantity),"현재가":won(price),
            "평가금액":won(value),"수익금":won(profit),
            "수익률":f"{return_rate:+.2f}%" if return_rate is not None else "-",
            "손절(-3%)":won(stop),"1차(+15%)":won(take1),"2차(+20%)":won(take2),"3차(+25%)":won(take3),
            "상태":state,"매도판단":action,
        })
    return details,view,price_alerts


@st.fragment(run_every="10s")
def render_live_holdings_table(active):
    _,view,price_alerts=holding_snapshot(active)
    view_df=pd.DataFrame(view)
    profit_cols=[col for col in ("수익금","수익률") if col in view_df.columns]
    styled_view=view_df.style.map(profit_text_color,subset=profit_cols) if profit_cols else view_df
    st.dataframe(styled_view,use_container_width=True,hide_index=True)
    if kakao_ready():
        today=(datetime.now(timezone.utc)+pd.Timedelta(hours=9)).strftime("%Y-%m-%d")
        state_key=f"holding_price_alerts_{today}"
        sent=set(st.session_state.get(state_key,[]))
        fresh=[item for item in price_alerts if item["id"] not in sent]
        if fresh:
            try:
                send_kakao_message(holding_price_alert_message(fresh))
                sent.update(item["id"] for item in fresh)
                st.session_state[state_key]=sorted(sent)
                st.session_state.pop("holding_price_alert_error",None)
            except Exception as exc:
                st.session_state["holding_price_alert_error"]=str(exc)


def render_holdings_tab():
    st.subheader("💼 보유종목 관리");st.caption("상단 보유종목 표의 현재가·평가손익만 10초마다 자동 갱신합니다. 나머지 화면은 그대로 유지됩니다.")
    notice=st.session_state.pop("kr_holding_save_notice",None)
    if notice:st.success(notice)
    try:rows,sha=load_holdings()
    except Exception as e:st.error(str(e));rows,sha=[],None
    active=[x for x in rows if str(x.get("status","holding")).lower()!="closed" and x.get("enabled",True)]
    c1,c2,c3=st.columns(3);c1.metric("보유종목",len(active));c2.metric("GitHub 저장","준비됨" if github_pat() else "PAT 미설정");c3.metric("시세","KIS 우선" if kis_ready() else "Yahoo")
    details=[]
    if active:
        render_live_holdings_table(active)
        details,_,_=holding_snapshot(active)
        labels=[f"{x[0].get('name')} ({str(x[0].get('ticker','')).zfill(6)})" for x in details];selected=st.selectbox("🔎 평가할 보유종목",labels);x=details[labels.index(selected)];r,q,cost,avg,p,src,val,pnl,ret,s,a,b,d,state,act=x
        st.markdown(f"### 🧠 {r.get('name')} 종합판단");m1,m2,m3,m4=st.columns(4);m1.metric("현재가",won(p));m2.metric("평균매수가",won(avg));m3.metric("평가손익",won(pnl));m4.metric("수익률",f"{ret:+.2f}%" if ret is not None else "-");st.info(f"현재 판단: **{state} · {act}**");st.markdown("#### 🎯 실전 가격 가이드");g1,g2,g3,g4=st.columns(4);g1.metric("손절 기준",won(s));g2.metric("1차 익절",won(a));g3.metric("2차 익절",won(b));g4.metric("3차 익절",won(d));st.caption(f"시세 출처: {src} · 선택한 보유종목 기준 자동 평가")
        render_holding_assessment(r,p)
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
