import math
from datetime import datetime

import pandas as pd
import streamlit as st
import yfinance as yf

SK_SYMBOL = "000660.KS"
P7 = ["SNDK", "MRVL", "MU", "INTC", "DELL", "AMD", "AVGO"]
SEMIS = ["SOXX", "SMH", "MU"]
AI_INFRA = ["NVDA", "AVGO", "AMD", "MU", "DELL"]


def _clip(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


def _momentum_score(ret_1m, ret_3m):
    return _clip(50 + ret_1m * 180 + ret_3m * 90)


@st.cache_data(ttl=900, show_spinner=False)
def _history(symbol, period="1y"):
    try:
        h = yf.download(symbol, period=period, interval="1d", auto_adjust=True, progress=False, threads=False)
        if h is None or h.empty:
            return pd.Series(dtype=float)
        close = h["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        return pd.to_numeric(close, errors="coerce").dropna()
    except Exception:
        return pd.Series(dtype=float)


def _ret(s, days):
    if s is None or len(s) <= days:
        return None
    try:
        return float(s.iloc[-1] / s.iloc[-days - 1] - 1)
    except Exception:
        return None


def _basket_score(symbols):
    vals, details = [], []
    for symbol in symbols:
        s = _history(symbol, "6mo")
        r1, r3 = _ret(s, 21), _ret(s, 63)
        if r1 is None or r3 is None:
            continue
        score = _momentum_score(r1, r3)
        vals.append(score)
        details.append((symbol, r1, r3, score))
    return (sum(vals) / len(vals) if vals else 50.0), details


def _chart_score():
    s = _history(SK_SYMBOL, "1y")
    if len(s) < 160:
        return 50.0, "데이터 부족", {}
    p = float(s.iloc[-1]); ma20 = float(s.tail(20).mean()); ma40 = float(s.tail(40).mean())
    ma60 = float(s.tail(60).mean()); ma120 = float(s.tail(120).mean()); ma160 = float(s.tail(160).mean())
    prev40 = float(s.iloc[-21:-1].tail(40).mean()) if len(s) >= 61 else ma40
    r1, r3 = _ret(s, 21) or 0.0, _ret(s, 63) or 0.0
    score = 35 + (12 if p > ma20 else 0) + (14 if p > ma60 else 0) + (14 if p > ma120 else 0) + (15 if p > ma160 else 0)
    score += _clip(10 + r1 * 35 + r3 * 20, 0, 10)
    text = f"현재가가 20/60/120/160일선 중 {sum([p>ma20,p>ma60,p>ma120,p>ma160])}개 위"
    return _clip(score), text, {"price":p,"ma20":ma20,"ma40":ma40,"ma60":ma60,"ma120":ma120,"ma160":ma160,"ma40_rising":ma40>prev40,"r1":r1,"r3":r3}


@st.cache_data(ttl=3600, show_spinner=False)
def _fundamental_score():
    try:
        info = yf.Ticker(SK_SYMBOL).info or {}
        rg, eg, roe, pe = info.get("revenueGrowth"), info.get("earningsGrowth"), info.get("returnOnEquity"), info.get("forwardPE")
        score, notes = 50.0, []
        if isinstance(rg,(int,float)): score += _clip(rg*60,-12,12); notes.append(f"매출성장 {rg*100:+.1f}%")
        if isinstance(eg,(int,float)): score += _clip(eg*35,-15,15); notes.append(f"이익성장 {eg*100:+.1f}%")
        if isinstance(roe,(int,float)): score += _clip((roe-.10)*35,-8,10); notes.append(f"ROE {roe*100:.1f}%")
        if isinstance(pe,(int,float)) and pe>0: score += 5 if pe<=15 else (2 if pe<=25 else -4); notes.append(f"Fwd PER {pe:.1f}")
        return _clip(score), " · ".join(notes) if notes else "Yahoo 재무데이터 제한"
    except Exception:
        return 50.0, "재무데이터 조회 실패"


def _split_signals(total, t):
    p, ma40, ma160, rising = t.get("price",0), t.get("ma40",0), t.get("ma160",0), t.get("ma40_rising",False)
    if total>=58 and p>=ma160: hold,hn="🟢 계속 보유","장기 추세가 살아 있고 종합점수도 보유 기준을 충족합니다."
    elif total>=45 and p>=ma160: hold,hn="🟡 보유 · 경계","장기선 위이지만 종합 모멘텀이 약해져 비중 확대는 보류합니다."
    elif p<ma160 or total<45: hold,hn="🔴 비중축소/매도 검토","160일선 이탈 또는 종합점수 약화가 확인됐습니다."
    else: hold,hn="🟡 보유 관찰","방향성이 명확해질 때까지 기존 물량 중심으로 관찰합니다."
    gap=(p/ma40-1) if ma40 else 0
    if total>=70 and p>=ma160 and rising and -.03<=gap<=.025: buy,bn="🟢 매수 가능","40일선 부근의 눌림 구간이며 장기 추세와 종합점수가 강합니다."
    elif total>=58 and p>=ma160:
        buy="🟡 눌림 매수 대기"
        bn=f"현재가가 40일선보다 {abs(gap)*100:.1f}% 낮습니다. 40일선 재회복을 확인한 뒤 매수를 검토합니다." if gap<-.03 else (f"현재가가 40일선보다 {gap*100:.1f}% 높아 추격매수보다 조정을 기다립니다." if gap>.025 else "40일선 부근이지만 종합점수 70점 미만이라 추가 확인 후 매수합니다.")
    else: buy,bn="🟠 신규매수 보류","종합점수 또는 장기 추세가 신규매수 기준을 충족하지 않습니다."
    return hold,hn,buy,bn,gap


def _price_plan(t):
    p, m40, m60, m160 = t.get("price",0),t.get("ma40",0),t.get("ma60",0),t.get("ma160",0)
    # 실전 참고선: 1차는 현재 조정구간, 2차는 60일선/160일선 사이의 더 깊은 눌림,
    # 추세회복은 40일선 상향 돌파 확인, 위험선은 160일선 3% 하회.
    first = min(m40*0.97, p*1.02) if m40 and p else p
    second = max(m160*1.05, min(m60*0.97, p*0.94)) if m160 else p*0.94
    recovery = m40*1.005 if m40 else p
    risk = m160*0.97 if m160 else p*0.85
    return {"first":first,"second":second,"recovery":recovery,"risk":risk}


@st.cache_data(ttl=900, show_spinner=False)
def get_skhynix_assessment():
    chart,chart_note,t=_chart_score(); semi,_=_basket_score(SEMIS); ai,_=_basket_score(AI_INFRA); p7,p7d=_basket_score(P7); fundamental,fn=_fundamental_score()
    total=chart*.30+semi*.25+ai*.20+p7*.15+fundamental*.10
    hold,hn,buy,bn,gap=_split_signals(total,t)
    return {"chart":round(chart,1),"semi":round(semi,1),"ai":round(ai,1),"p7":round(p7,1),"fundamental":round(fundamental,1),"total":round(total,1),"hold":hold,"hold_note":hn,"buy":buy,"buy_note":bn,"gap40":gap,"technical":t,"price_plan":_price_plan(t),"chart_note":chart_note,"fundamental_note":fn,"p7_available":len(p7d),"updated":datetime.now().strftime("%Y-%m-%d %H:%M")}


def render_skhynix_assessment():
    st.markdown("### 🧠 SK하이닉스 종합판단")
    st.caption("보유 여부와 신규/추가매수 시점을 분리합니다. 차트·반도체/HBM·AI 인프라·월가 P7·기업 재무를 함께 계산합니다.")
    try:
        a=get_skhynix_assessment(); c1,c2,c3=st.columns([1,1.5,1.5]); c1.metric("종합점수",f"{a['total']:.1f}점"); c2.metric("① 보유 판단",a['hold']); c3.metric("② 신규/추가매수",a['buy'])
        st.success(f"보유: {a['hold_note']}") if "계속" in a['hold'] else st.warning(f"보유: {a['hold_note']}"); st.info(f"매수: {a['buy_note']}")
        t=a['technical']
        if t: st.caption(f"현재가 {t['price']:,.0f}원 · 40일선 {t['ma40']:,.0f}원 · 160일선 {t['ma160']:,.0f}원 · 40일선 대비 {a['gap40']*100:+.1f}%")

        st.markdown("#### 🎯 실전 가격 가이드")
        q=a['price_plan']; p1,p2,p3,p4=st.columns(4)
        p1.metric("1차 분할매수 참고",f"{q['first']:,.0f}원")
        p2.metric("2차 분할매수 참고",f"{q['second']:,.0f}원")
        p3.metric("추세회복 확인선",f"{q['recovery']:,.0f}원")
        p4.metric("비중축소 경계선",f"{q['risk']:,.0f}원")
        st.caption("1·2차 가격은 한 번에 전액 매수하는 목표가가 아니라 분할매수 관찰선입니다. 추세회복선은 40일선 상향 재돌파 확인용, 비중축소 경계선은 160일선 약 3% 하회 기준입니다.")

        df=pd.DataFrame([{"평가축":"차트/추세","비중":"30%","점수":a['chart']},{"평가축":"반도체/HBM 환경","비중":"25%","점수":a['semi']},{"평가축":"AI 인프라 수요","비중":"20%","점수":a['ai']},{"평가축":"월가 P7","비중":"15%","점수":a['p7']},{"평가축":"기업/실적","비중":"10%","점수":a['fundamental']}])
        st.dataframe(df,use_container_width=True,hide_index=True,column_config={"점수":st.column_config.ProgressColumn(min_value=0,max_value=100,format="%.1f")})
        st.caption(f"차트: {a['chart_note']} · 기업/실적: {a['fundamental_note']} · 계산 {a['updated']} · P7 {a['p7_available']}/7")
        st.info("가격 가이드는 이동평균선과 현재 추세에서 자동 계산되는 참고선입니다. 실제 주문은 종합점수·반도체/HBM·AI/P7 환경과 함께 판단하세요.")
    except Exception as e:
        st.warning(f"SK하이닉스 종합판단을 계산하지 못했습니다: {e}")
