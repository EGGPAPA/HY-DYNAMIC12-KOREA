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
    # 0% 부근=50점, 강한 상승=고득점, 강한 하락=저득점
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
    vals = []
    details = []
    for symbol in symbols:
        s = _history(symbol, "6mo")
        r1 = _ret(s, 21)
        r3 = _ret(s, 63)
        if r1 is None or r3 is None:
            continue
        score = _momentum_score(r1, r3)
        vals.append(score)
        details.append((symbol, r1, r3, score))
    return (sum(vals) / len(vals) if vals else 50.0), details


def _chart_score():
    s = _history(SK_SYMBOL, "1y")
    if len(s) < 160:
        return 50.0, "데이터 부족"
    p = float(s.iloc[-1])
    ma20 = float(s.tail(20).mean())
    ma60 = float(s.tail(60).mean())
    ma120 = float(s.tail(120).mean())
    ma160 = float(s.tail(160).mean())
    r1 = _ret(s, 21) or 0.0
    r3 = _ret(s, 63) or 0.0
    score = 35
    score += 12 if p > ma20 else 0
    score += 14 if p > ma60 else 0
    score += 14 if p > ma120 else 0
    score += 15 if p > ma160 else 0
    score += _clip(10 + r1 * 35 + r3 * 20, 0, 10)
    text = f"현재가가 20/60/120/160일선 중 {sum([p>ma20,p>ma60,p>ma120,p>ma160])}개 위"
    return _clip(score), text


@st.cache_data(ttl=3600, show_spinner=False)
def _fundamental_score():
    try:
        info = yf.Ticker(SK_SYMBOL).info or {}
        revenue_growth = info.get("revenueGrowth")
        earnings_growth = info.get("earningsGrowth")
        roe = info.get("returnOnEquity")
        forward_pe = info.get("forwardPE")
        score = 50.0
        notes = []
        if isinstance(revenue_growth, (int, float)):
            score += _clip(revenue_growth * 60, -12, 12)
            notes.append(f"매출성장 {revenue_growth*100:+.1f}%")
        if isinstance(earnings_growth, (int, float)):
            score += _clip(earnings_growth * 35, -15, 15)
            notes.append(f"이익성장 {earnings_growth*100:+.1f}%")
        if isinstance(roe, (int, float)):
            score += _clip((roe - 0.10) * 35, -8, 10)
            notes.append(f"ROE {roe*100:.1f}%")
        if isinstance(forward_pe, (int, float)) and forward_pe > 0:
            score += 5 if forward_pe <= 15 else (2 if forward_pe <= 25 else -4)
            notes.append(f"Fwd PER {forward_pe:.1f}")
        return _clip(score), " · ".join(notes) if notes else "Yahoo 재무데이터 제한"
    except Exception:
        return 50.0, "재무데이터 조회 실패"


@st.cache_data(ttl=900, show_spinner=False)
def get_skhynix_assessment():
    chart, chart_note = _chart_score()
    semi, semi_detail = _basket_score(SEMIS)
    ai, ai_detail = _basket_score(AI_INFRA)
    p7, p7_detail = _basket_score(P7)
    fundamental, fundamental_note = _fundamental_score()

    total = chart * 0.30 + semi * 0.25 + ai * 0.20 + p7 * 0.15 + fundamental * 0.10
    if total >= 80:
        verdict = "🟢 적극매수 후보"
    elif total >= 70:
        verdict = "🟢 매수/보유 우위"
    elif total >= 58:
        verdict = "🟡 보유 · 눌림 매수 대기"
    elif total >= 45:
        verdict = "🟠 중립 · 비중확대 보류"
    else:
        verdict = "🔴 비중축소/매도 검토"

    return {
        "chart": round(chart, 1),
        "semi": round(semi, 1),
        "ai": round(ai, 1),
        "p7": round(p7, 1),
        "fundamental": round(fundamental, 1),
        "total": round(total, 1),
        "verdict": verdict,
        "chart_note": chart_note,
        "fundamental_note": fundamental_note,
        "p7_available": len(p7_detail),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def render_skhynix_assessment():
    st.markdown("### 🧠 SK하이닉스 종합판단")
    st.caption("차트만 보지 않고 반도체·AI 인프라·월가 P7 흐름·기업 재무를 함께 계산합니다. P7 = SanDisk, Marvell, Micron, Intel, Dell, AMD, Broadcom.")
    try:
        a = get_skhynix_assessment()
        c1, c2, c3 = st.columns([1, 1, 2])
        c1.metric("종합점수", f"{a['total']:.1f}점")
        c2.metric("최종판단", a["verdict"])
        c3.caption(f"자동 갱신: 약 15분 · 계산 {a['updated']} · P7 {a['p7_available']}/7 종목 반영")

        df = pd.DataFrame([
            {"평가축": "차트/추세", "비중": "30%", "점수": a["chart"]},
            {"평가축": "반도체/HBM 환경", "비중": "25%", "점수": a["semi"]},
            {"평가축": "AI 인프라 수요", "비중": "20%", "점수": a["ai"]},
            {"평가축": "월가 P7", "비중": "15%", "점수": a["p7"]},
            {"평가축": "기업/실적", "비중": "10%", "점수": a["fundamental"]},
        ])
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={"점수": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f")})
        st.caption(f"차트: {a['chart_note']} · 기업/실적: {a['fundamental_note']}")
        st.info("반도체/HBM 점수는 SOXX·SMH·Micron, AI 수요는 NVDA·Broadcom·AMD·Micron·Dell의 시장 흐름을 대용지표로 사용합니다. HBM 계약/가격과 뉴스 자체를 직접 판독하는 점수는 아니므로 매도선·실적 발표와 함께 보세요.")
    except Exception as e:
        st.warning(f"SK하이닉스 종합판단을 계산하지 못했습니다: {e}")
