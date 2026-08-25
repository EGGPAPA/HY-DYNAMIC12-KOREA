import base64
import json
import os
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from korea_live_price import get_live_price, price_source_label

DEFAULT_KOREA_TICKER = "292150.KS"
DEFAULT_SP_TICKER = "360750.KS"
REPO = "EGGPAPA/HY-DYNAMIC12-KOREA"
BRANCH = "main"
PENSION_PATH = "pension_holdings.json"
PENSION_API = f"https://api.github.com/repos/{REPO}/contents/{PENSION_PATH}"


def _won(x):
    try: return f"{int(round(float(x))):,}원"
    except Exception: return "-"


def _return_color(value):
    text = str(value).strip().replace(",", "").replace("원", "").replace("%", "")
    if text in {"", "-"}:
        return "color: #a0a8b8; font-weight: 700"
    try:
        number = float(text)
    except Exception:
        return "color: #f0f2f6; font-weight: 700"
    if number > 0:
        return "color: #ff4b4b; font-weight: 800"
    if number < 0:
        return "color: #4da3ff; font-weight: 800"
    return "color: #f0f2f6; font-weight: 700"


def _colored_metric(slot, label, value, number):
    color = "#ff4b4b" if number > 0 else ("#4da3ff" if number < 0 else "#f0f2f6")
    slot.markdown(
        f"""
        <div style="border:1px solid #354052;border-radius:12px;padding:16px 18px;min-height:86px;background:#1d2735">
          <div style="font-size:14px;font-weight:700;color:#f0f2f6;margin-bottom:8px">{label}</div>
          <div style="font-size:29px;font-weight:800;color:{color};line-height:1.15">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _secret(name, default=""):
    try:
        v = st.secrets.get(name, default)
        if v: return str(v).strip()
    except Exception:
        pass
    return os.getenv(name, default).strip()


def _gh_headers():
    h = {"Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"}
    pat = _secret("GITHUB_PAT")
    if pat: h["Authorization"] = f"Bearer {pat}"
    return h


def _load_pension():
    default = {"monthly":500000,"korea_ticker":DEFAULT_KOREA_TICKER,"korea_qty":0.0,"korea_avg":0,"sp_ticker":DEFAULT_SP_TICKER,"sp_qty":0.0,"sp_avg":0,"safe_now":0}
    try:
        r = requests.get(PENSION_API, headers=_gh_headers(), params={"ref":BRANCH}, timeout=15)
        if r.status_code != 200: return default, None
        d = r.json()
        saved = json.loads(base64.b64decode(d["content"]).decode("utf-8"))
        default.update(saved)
        return default, d.get("sha")
    except Exception:
        return default, None


def _save_pension(data, sha):
    pat = _secret("GITHUB_PAT")
    if not pat:
        raise RuntimeError("Streamlit Secrets의 GITHUB_PAT가 필요합니다.")
    payload = {
        "message":"Update pension holdings",
        "content":base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode(),
        "branch":BRANCH,
    }
    if sha: payload["sha"] = sha
    r = requests.put(PENSION_API, headers=_gh_headers(), json=payload, timeout=20)
    if r.status_code not in (200,201):
        raise RuntimeError(f"연금 보유정보 저장 실패: HTTP {r.status_code}")


@st.cache_data(ttl=10, show_spinner=False)
def _auto_price(ticker):
    """Fetch a live KIS quote first, with Yahoo fast-info as fallback."""
    try:
        symbol = str(ticker).strip().upper()
        code = symbol.split(".")[0]
        market = "KOSDAQ" if symbol.endswith(".KQ") else "KOSPI"
        price = get_live_price(code, market)
        if price is not None and float(price) > 0:
            asof = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return float(price), asof
    except Exception:
        pass
    return None, None


def _holding(name, qty, avg, current, target):
    cost=float(qty)*float(avg); value=float(qty)*float(current); profit=value-cost; rate=profit/cost*100 if cost>0 else 0.0
    return {"자산":name,"보유수량":qty,"평균매수가":_won(avg),"현재가":_won(current),"매입금액":_won(cost),"평가금액":_won(value),"수익금":_won(profit),"수익률":f"{rate:+.2f}%","목표비중":f"{target:.0f}%","value":value}


@st.fragment(run_every="10s")
def render_pension_manager_tab():
    saved, saved_sha = _load_pension()
    st.subheader("🏦 연금저축 · 월간 실행판")
    st.caption("입력값은 GitHub에 저장되어 새로고침·재배포 후에도 유지됩니다. 월급 후 정액매수하고 현재가는 자동 갱신합니다.")

    monthly = st.number_input("월 납입액", min_value=0, step=10000, value=int(saved.get("monthly",500000)), format="%d", key="pension_monthly")
    st.markdown("### 📒 보유자산 입력")
    t1,t2=st.columns(2)
    with t1:
        korea_ticker=st.text_input("KOREA TOP10 티커", value=str(saved.get("korea_ticker",DEFAULT_KOREA_TICKER)), key="pension_korea_ticker")
    with t2:
        sp_ticker=st.text_input("S&P500 ETF 티커", value=str(saved.get("sp_ticker",DEFAULT_SP_TICKER)), key="pension_sp_ticker")

    korea_auto,korea_asof=_auto_price(korea_ticker.strip()); sp_auto,sp_asof=_auto_price(sp_ticker.strip())
    c1,c2,c3=st.columns(3)
    with c1:
        st.markdown("**KOREA TOP10**")
        korea_qty=st.number_input("KOREA TOP10 보유수량",min_value=0.0,step=1.0,value=float(saved.get("korea_qty",0)),key="pension_korea_qty")
        korea_avg=st.number_input("KOREA TOP10 평균매수가",min_value=0,step=100,value=int(saved.get("korea_avg",0)),format="%d",key="pension_korea_avg")
        if korea_auto is not None:
            st.metric("자동 현재가",_won(korea_auto));st.caption(f"조회 {korea_asof} · {price_source_label()} · {korea_ticker}");korea_price=korea_auto
        else:
            st.error("현재가 자동조회 실패");korea_price=st.number_input("KOREA TOP10 수동 현재가",min_value=0,step=100,value=0,format="%d")
    with c2:
        st.markdown("**S&P500 ETF**")
        sp_qty=st.number_input("S&P500 ETF 보유수량",min_value=0.0,step=1.0,value=float(saved.get("sp_qty",0)),key="pension_sp_qty")
        sp_avg=st.number_input("S&P500 ETF 평균매수가",min_value=0,step=100,value=int(saved.get("sp_avg",0)),format="%d",key="pension_sp_avg")
        if sp_auto is not None:
            st.metric("자동 현재가",_won(sp_auto));st.caption(f"조회 {sp_asof} · {price_source_label()} · {sp_ticker}");sp_price=sp_auto
        else:
            st.error("현재가 자동조회 실패");sp_price=st.number_input("S&P500 ETF 수동 현재가",min_value=0,step=100,value=0,format="%d")
    with c3:
        st.markdown("**안전자산 / 신호**")
        safe_text=st.text_input("채권·현금성 평가액",value=_won(saved.get("safe_now",0)),help="예: 128,532원",key="pension_safe_text")
        safe_digits="".join(ch for ch in safe_text if ch.isdigit());safe_now=int(safe_digits) if safe_digits else 0
        st.caption(f"입력금액: **{_won(safe_now)}**")
        korea_signal=st.selectbox("KOREA TOP10 MA5 참고신호",["🟢 MA5 위 · 상승","🟡 MA5 위 · 횡보","🟠 MA5 부근","🔴 MA5 1개월 이탈","🔴 2개월 이탈 · MA5 하락","🚀 MA5 재돌파"])
        if st.button("🔄 현재가 다시 조회",use_container_width=True):
            _auto_price.clear()
            get_live_price.clear()
            st.session_state["pension_price_refreshed_at"]=datetime.now().strftime("%H:%M:%S")
            st.rerun()
        if st.session_state.get("pension_price_refreshed_at"):
            st.success(f"현재가 재조회 완료 · {st.session_state['pension_price_refreshed_at']}")

    save_data={"monthly":int(monthly),"korea_ticker":korea_ticker.strip(),"korea_qty":float(korea_qty),"korea_avg":int(korea_avg),"sp_ticker":sp_ticker.strip(),"sp_qty":float(sp_qty),"sp_avg":int(sp_avg),"safe_now":int(safe_now)}
    if st.button("💾 연금 보유정보 저장",type="primary",use_container_width=True):
        try:
            _save_pension(save_data,saved_sha);st.success("저장 완료 · 새로고침/재배포 후에도 유지됩니다.");st.rerun()
        except Exception as e: st.error(str(e))

    korea=_holding("KOREA TOP10",korea_qty,korea_avg,korea_price,30);sp=_holding("S&P500",sp_qty,sp_avg,sp_price,50);invested_total=korea["value"]+sp["value"]+float(safe_now)
    display_rows=[]
    for h in (sp,korea):
        row={k:v for k,v in h.items() if k!="value"};row["현재비중"]=f"{(h['value']/invested_total*100 if invested_total else 0):.1f}%";display_rows.append(row)
    display_rows.append({"자산":"채권·현금성","보유수량":"-","평균매수가":"-","현재가":"-","매입금액":"-","평가금액":_won(safe_now),"수익금":"-","수익률":"-","목표비중":"20%","현재비중":f"{(safe_now/invested_total*100 if invested_total else 0):.1f}%"})
    st.markdown("### 💼 현재 연금 포트폴리오")
    portfolio_table=pd.DataFrame(display_rows)
    styled_portfolio=(
        portfolio_table.style
        .map(_return_color,subset=["수익금","수익률"])
        .set_properties(**{"font-size":"15px","padding":"9px 10px"})
        .hide(axis="index")
    )
    st.dataframe(styled_portfolio,use_container_width=True,hide_index=True)
    p1,p2,p3=st.columns(3)
    p1.metric("총 평가액",_won(invested_total))
    stock_profit=(korea["value"]-korea_qty*korea_avg)+(sp["value"]-sp_qty*sp_avg)
    stock_cost=korea_qty*korea_avg+sp_qty*sp_avg
    stock_return=((korea["value"]+sp["value"])/stock_cost-1)*100 if stock_cost else 0
    _colored_metric(p2,"주식 수익금",_won(stock_profit),stock_profit)
    _colored_metric(p3,"주식 수익률",f"{stock_return:+.2f}%",stock_return)
    st.markdown("### 🎯 장기 목표비중");st.write("S&P500 **50%** · KOREA TOP10 **30%** · 채권·현금성 **20%**")
    target_values={"sp":(invested_total+monthly)*.50,"korea":(invested_total+monthly)*.30,"safe":(invested_total+monthly)*.20};gaps={"sp":max(0,target_values["sp"]-sp["value"]),"korea":max(0,target_values["korea"]-korea["value"]),"safe":max(0,target_values["safe"]-safe_now)};gap_sum=sum(gaps.values())
    if gap_sum>0:sp_buy=monthly*gaps["sp"]/gap_sum;korea_buy=monthly*gaps["korea"]/gap_sum;safe_buy=monthly-sp_buy-korea_buy
    else:sp_buy,korea_buy,safe_buy=monthly*.50,monthly*.30,monthly*.20
    st.markdown("### ⚡ 이번 달 정기매수");a,b,c=st.columns(3);a.metric("S&P500",_won(sp_buy));b.metric("KOREA TOP10",_won(korea_buy));c.metric("채권·현금성",_won(safe_buy));st.success("월급 후 정기매수: **주가 등락과 관계없이 실행**")
    if "2개월 이탈" in korea_signal:st.warning("MA5 방어신호: 정기적립은 유지하되 기존 KOREA TOP10 전술비중 조정 여부를 월말에 점검하세요.")
    elif "1개월 이탈" in korea_signal or "MA5 부근" in korea_signal:st.info("MA5 주의신호: 정기적립은 그대로 실행하고 기존 보유분은 관찰합니다.")
    else:st.info("MA5 추세 양호: 정기적립과 기존 보유를 유지합니다.")
    st.markdown("### 📅 운용 원칙");st.write("① 월급 후 월 50만원 정기매수 ② 단기 주가 변동으로 매수일 변경하지 않음 ③ 매도보다 신규 납입금으로 50:30:20 목표비중 조정 ④ MA5는 기존 보유자산 위험관리 참고 신호")
