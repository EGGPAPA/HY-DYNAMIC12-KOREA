from datetime import date, timedelta

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

# 현재 V3.9 내장 후보군과 동일한 핵심 유니버스(가격·거래량 기반 1차 백테스트용)
UNIVERSE = [
    ("005930", "삼성전자", "KOSPI"), ("000660", "SK하이닉스", "KOSPI"),
    ("035420", "NAVER", "KOSPI"), ("035720", "카카오", "KOSPI"),
    ("005380", "현대차", "KOSPI"), ("000270", "기아", "KOSPI"),
    ("207940", "삼성바이오로직스", "KOSPI"), ("068270", "셀트리온", "KOSPI"),
    ("373220", "LG에너지솔루션", "KOSPI"), ("006400", "삼성SDI", "KOSPI"),
    ("005490", "POSCO홀딩스", "KOSPI"), ("051910", "LG화학", "KOSPI"),
    ("012450", "한화에어로스페이스", "KOSPI"), ("042660", "한화오션", "KOSPI"),
    ("009540", "HD한국조선해양", "KOSPI"), ("034020", "두산에너빌리티", "KOSPI"),
    ("105560", "KB금융", "KOSPI"), ("055550", "신한지주", "KOSPI"),
    ("086790", "하나금융지주", "KOSPI"), ("316140", "우리금융지주", "KOSPI"),
    ("028260", "삼성물산", "KOSPI"), ("066570", "LG전자", "KOSPI"),
    ("003670", "포스코퓨처엠", "KOSPI"), ("323410", "카카오뱅크", "KOSPI"),
    ("247540", "에코프로비엠", "KOSDAQ"), ("086520", "에코프로", "KOSDAQ"),
    ("196170", "알테오젠", "KOSDAQ"), ("028300", "HLB", "KOSDAQ"),
    ("058470", "리노공업", "KOSDAQ"), ("403870", "HPSP", "KOSDAQ"),
    ("214150", "클래시스", "KOSDAQ"), ("039030", "이오테크닉스", "KOSDAQ"),
]


def _symbol(code, market):
    return f"{code}.KS" if market == "KOSPI" else f"{code}.KQ"


@st.cache_data(ttl=3600, show_spinner=False)
def _download(start_date, end_date):
    syms = [_symbol(c, m) for c, _, m in UNIVERSE]
    data = yf.download(
        syms,
        start=str(start_date),
        end=str(end_date + timedelta(days=1)),
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    return data


def _one(data, symbol):
    try:
        if isinstance(data.columns, pd.MultiIndex):
            if symbol in data.columns.get_level_values(0):
                return data[symbol].dropna(how="all")
            if symbol in data.columns.get_level_values(1):
                return data.xs(symbol, axis=1, level=1).dropna(how="all")
        return data.dropna(how="all")
    except Exception:
        return pd.DataFrame()


def _rank_on_date(histories, dt):
    rows = []
    for code, name, market in UNIVERSE:
        h = histories.get(code)
        if h is None or h.empty:
            continue
        h = h.loc[h.index <= dt]
        if len(h) < 61:
            continue
        close = pd.to_numeric(h["Close"], errors="coerce").dropna()
        vol = pd.to_numeric(h["Volume"], errors="coerce").dropna()
        if len(close) < 61 or len(vol) < 20:
            continue
        p = float(close.iloc[-1])
        ma20 = float(close.tail(20).mean())
        ma60 = float(close.tail(60).mean())
        r20 = p / float(close.iloc[-21]) - 1
        r60 = p / float(close.iloc[-61]) - 1
        v20 = float((close.tail(20) * vol.tail(20)).mean())
        vol_ratio = float(vol.tail(5).mean() / max(float(vol.tail(20).mean()), 1))
        trend = 1.0 if p > ma20 > ma60 else 0.0
        rows.append({
            "code": code, "name": name, "market": market, "price": p,
            "r20": r20, "r60": r60, "value20": v20, "vol_ratio": vol_ratio,
            "trend": trend,
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for c in ["r20", "r60", "value20", "vol_ratio"]:
        df[c + "_pct"] = df[c].rank(pct=True)
    # V3.9의 가격/유동성/추세 특성을 과거시점 데이터만으로 재현한 proxy 점수
    df["score"] = (
        df["value20_pct"] * 25
        + df["r20_pct"] * 25
        + df["r60_pct"] * 20
        + df["vol_ratio_pct"] * 10
        + df["trend"] * 20
    )
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def _simulate(data, start_date, end_date, top_n, hold_days, stop_pct, cost_pct):
    histories = {}
    for code, _, market in UNIVERSE:
        h = _one(data, _symbol(code, market))
        if not h.empty:
            idx = pd.to_datetime(h.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
            h = h.copy(); h.index = idx
            histories[code] = h

    kospi = yf.download("^KS11", start=str(start_date), end=str(end_date + timedelta(days=1)), auto_adjust=True, progress=False)
    if not kospi.empty:
        kospi.index = pd.to_datetime(kospi.index).tz_localize(None) if getattr(pd.to_datetime(kospi.index), "tz", None) is not None else pd.to_datetime(kospi.index)

    all_dates = sorted(set().union(*[set(h.index) for h in histories.values()])) if histories else []
    dates = [d for d in all_dates if pd.Timestamp(start_date) <= d <= pd.Timestamp(end_date)]
    if not dates:
        return pd.DataFrame(), pd.DataFrame(), {}

    # 매주 첫 거래일에 선정 후 다음 거래일 종가 진입, hold_days 동안 추적
    weekly = []
    seen = set()
    for d in dates:
        key = (d.isocalendar().year, d.isocalendar().week)
        if key not in seen:
            seen.add(key); weekly.append(d)

    trades = []
    for signal_date in weekly:
        rank = _rank_on_date(histories, signal_date)
        if rank.empty:
            continue
        picks = rank.head(top_n)
        for _, r in picks.iterrows():
            h = histories[r["code"]]
            future = h.loc[h.index > signal_date].head(hold_days + 1)
            if len(future) < 2:
                continue
            entry_date = future.index[0]
            entry = float(future["Close"].iloc[0])
            stop = entry * (1 - stop_pct / 100)
            exit_date = future.index[-1]
            exit_price = float(future["Close"].iloc[-1])
            reason = f"{hold_days}일 보유"
            for i in range(1, len(future)):
                low = float(future["Low"].iloc[i]) if "Low" in future.columns else float(future["Close"].iloc[i])
                if low <= stop:
                    exit_date = future.index[i]
                    exit_price = stop
                    reason = "손절"
                    break
            gross = exit_price / entry - 1
            net = gross - cost_pct / 100
            trades.append({
                "신호일": signal_date.date(), "매수일": entry_date.date(), "매도일": exit_date.date(),
                "종목코드": r["code"], "종목명": r["name"], "점수": round(float(r["score"]), 1),
                "매수가": round(entry), "매도가": round(exit_price), "수익률(%)": round(net * 100, 2), "청산사유": reason,
            })

    tdf = pd.DataFrame(trades)
    if tdf.empty:
        return tdf, pd.DataFrame(), {}

    # 매주 동일비중 포트폴리오 수익률로 누적곡선 생성
    week_ret = tdf.groupby("신호일")["수익률(%)"].mean().sort_index() / 100
    equity = (1 + week_ret).cumprod()
    eq = pd.DataFrame({"HY DYNAMIC12": equity.values}, index=pd.to_datetime(equity.index))

    if not kospi.empty:
        k = kospi["Close"]
        if isinstance(k, pd.DataFrame):
            k = k.iloc[:, 0]
        k = k.loc[(k.index >= eq.index.min()) & (k.index <= pd.Timestamp(end_date))]
        if not k.empty:
            k = k / float(k.iloc[0])
            eq = eq.join(k.rename("KOSPI"), how="outer").ffill()

    curve = eq["HY DYNAMIC12"].dropna()
    total = float(curve.iloc[-1] - 1) if not curve.empty else 0.0
    years = max((pd.Timestamp(end_date) - pd.Timestamp(start_date)).days / 365.25, 1/365.25)
    cagr = (float(curve.iloc[-1]) ** (1 / years) - 1) if not curve.empty else 0.0
    dd = curve / curve.cummax() - 1
    mdd = float(dd.min()) if not dd.empty else 0.0
    win = float((tdf["수익률(%)"] > 0).mean())
    avg = float(tdf["수익률(%)"].mean())
    stats = {"total": total, "cagr": cagr, "mdd": mdd, "win": win, "avg": avg, "n": len(tdf)}
    return tdf, eq, stats


def render_backtest_tab():
    st.subheader("📊 한국장 백테스트")
    st.caption("V3.9의 가격·거래량·추세 구조를 과거 시점 데이터만 사용해 검증합니다. 현재 버전은 과거 외국인/기관 수급과 시점별 재무데이터를 제외한 1차 proxy 백테스트입니다.")

    today = date.today()
    c1, c2, c3 = st.columns(3)
    start = c1.date_input("시작일", value=today - timedelta(days=365*3), max_value=today)
    end = c2.date_input("종료일", value=today, max_value=today)
    top_n = c3.selectbox("주간 선정 종목수", [1, 3, 5, 12], index=1)

    d1, d2, d3 = st.columns(3)
    hold_days = d1.selectbox("보유기간(거래일)", [5, 10, 20, 40], index=2)
    stop_pct = d2.number_input("손절률(%)", min_value=1.0, max_value=20.0, value=3.0, step=0.5)
    cost_pct = d3.number_input("왕복 비용+슬리피지(%)", min_value=0.0, max_value=2.0, value=0.30, step=0.05)

    if st.button("▶ 백테스트 실행", type="primary", use_container_width=True, key="kr_backtest_run"):
        if start >= end:
            st.error("시작일은 종료일보다 앞서야 합니다.")
            return
        with st.spinner("과거 데이터 다운로드 및 백테스트 중..."):
            data = _download(start - timedelta(days=120), end)
            trades, equity, stats = _simulate(data, start, end, top_n, hold_days, stop_pct, cost_pct)
        if trades.empty:
            st.warning("백테스트 거래가 생성되지 않았습니다. 기간을 넓혀 다시 실행해 보세요.")
            return

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("누적수익률", f"{stats['total']*100:.1f}%")
        m2.metric("CAGR", f"{stats['cagr']*100:.1f}%")
        m3.metric("MDD", f"{stats['mdd']*100:.1f}%")
        m4.metric("승률", f"{stats['win']*100:.1f}%")
        m5.metric("거래수", f"{stats['n']:,}")

        st.markdown("### 누적 성과")
        st.line_chart(equity)
        st.markdown("### 거래내역")
        st.dataframe(trades.sort_values("매수일", ascending=False), use_container_width=True, hide_index=True)
        st.info("이 결과는 1차 검증용입니다. 다음 버전에서는 KRX 과거 투자자 수급·시점별 재무자료를 추가해 현재 판정점수와 더 가깝게 확장할 수 있습니다.")
