from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

try:
    from pykrx import stock
except Exception:
    stock = None


SEOUL = ZoneInfo("Asia/Seoul")
EXPORT_FILE = Path("export_history.csv")
SECTOR_ETFS = {
    "반도체": "091160.KS",
    "자동차": "091180.KS",
    "금융": "091170.KS",
    "헬스케어": "143860.KS",
    "2차전지": "305720.KS",
}
BREADTH_SAMPLE = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "005380.KS": "현대차",
    "000270.KS": "기아", "105560.KS": "KB금융", "055550.KS": "신한지주",
    "035420.KS": "NAVER", "035720.KS": "카카오", "068270.KS": "셀트리온",
    "207940.KS": "삼성바이오", "012450.KS": "한화에어로", "042660.KS": "한화오션",
    "247540.KQ": "에코프로비엠", "086520.KQ": "에코프로", "196170.KQ": "알테오젠",
    "058470.KQ": "리노공업", "214150.KQ": "클래시스", "039030.KQ": "이오테크닉스",
}


def _business_day(offset=0):
    day = datetime.now(SEOUL).date() - timedelta(days=offset)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


@st.cache_data(ttl=900, show_spinner=False)
def _history(symbol, period="6mo"):
    try:
        frame = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
        if frame is None or frame.empty:
            return pd.DataFrame()
        frame = frame.copy()
        frame.index = pd.to_datetime(frame.index)
        if getattr(frame.index, "tz", None) is not None:
            frame.index = frame.index.tz_localize(None)
        return frame.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


def _last_close(symbol, period="3mo"):
    frame = _history(symbol, period)
    if frame.empty:
        return None, None, frame
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if close.empty:
        return None, None, frame
    change20 = (float(close.iloc[-1]) / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else None
    return float(close.iloc[-1]), change20, frame


@st.cache_data(ttl=1800, show_spinner=False)
def _market_breadth():
    if stock is None:
        return _sample_breadth()
    for offset in range(8):
        date = _business_day(offset).strftime("%Y%m%d")
        frames = []
        try:
            for market in ("KOSPI", "KOSDAQ"):
                part = stock.get_market_ohlcv_by_ticker(date, market=market)
                if part is not None and not part.empty:
                    part = part.copy()
                    part["시장"] = market
                    frames.append(part)
            if not frames:
                continue
            data = pd.concat(frames)
            changes = pd.to_numeric(data.get("등락률"), errors="coerce").dropna()
            if changes.empty:
                continue
            rising = int((changes > 0).sum())
            falling = int((changes < 0).sum())
            flat = int((changes == 0).sum())
            ratio = rising / max(rising + falling, 1) * 100
            return {"date": date, "rising": rising, "falling": falling, "flat": flat, "ratio": ratio}
        except Exception:
            continue
    return _sample_breadth()


def _sample_breadth():
    """Fallback breadth based on a disclosed representative liquid-stock sample."""
    try:
        data = yf.download(
            list(BREADTH_SAMPLE), period="5d", interval="1d", auto_adjust=True,
            progress=False, threads=True,
        )
        close = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data[["Close"]]
        close = close.dropna(how="all").ffill()
        if close is None or close.empty or len(close) < 2:
            return {}
        changes = close.pct_change(fill_method=None).iloc[-1].dropna() * 100
        rising = int((changes > 0).sum())
        falling = int((changes < 0).sum())
        flat = int((changes == 0).sum())
        if rising + falling == 0:
            return {}
        return {
            "date": pd.Timestamp(close.index[-1]).strftime("%Y%m%d"),
            "rising": rising, "falling": falling, "flat": flat,
            "ratio": rising / max(rising + falling, 1) * 100,
            "source": f"대표 유동성 종목 {len(changes)}개 표본",
        }
    except Exception:
        return {}


@st.cache_data(ttl=1800, show_spinner=False)
def _investor_flow():
    if stock is None:
        return {}
    end = _business_day()
    start = end - timedelta(days=35)
    try:
        frame = stock.get_market_trading_value_by_date(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "KOSPI"
        )
        if frame is None or frame.empty:
            return {}
        result = {}
        for label, candidates in {
            "외국인": ("외국인합계", "외국인"),
            "기관": ("기관합계", "기관"),
        }.items():
            column = next((c for c in candidates if c in frame.columns), None)
            if column is None:
                continue
            values = pd.to_numeric(frame[column], errors="coerce").dropna()
            result[f"{label}5"] = float(values.tail(5).sum())
            result[f"{label}20"] = float(values.tail(20).sum())
        result["date"] = pd.Timestamp(frame.index[-1]).strftime("%Y-%m-%d")
        return result
    except Exception:
        pass
    for offset in range(8):
        date = _business_day(offset).strftime("%Y%m%d")
        try:
            result = {"date": f"{date[:4]}-{date[4:6]}-{date[6:]}", "period_days": 1}
            for investor, label in (("외국인", "외국인"), ("기관합계", "기관")):
                total = 0.0
                for market in ("KOSPI", "KOSDAQ"):
                    frame = stock.get_market_net_purchases_of_equities_by_ticker(
                        date, date, market, investor
                    )
                    if frame is not None and not frame.empty and "순매수거래대금" in frame:
                        total += float(pd.to_numeric(frame["순매수거래대금"], errors="coerce").fillna(0).sum())
                result[f"{label}20"] = total
            if any(abs(result.get(k, 0)) > 0 for k in ("외국인20", "기관20")):
                return result
        except Exception:
            continue
    return {}


@st.cache_data(ttl=3600, show_spinner=False)
def _sector_strength():
    rows = []
    for name, ticker in SECTOR_ETFS.items():
        _, change20, _ = _last_close(ticker, "3mo")
        if change20 is not None:
            rows.append({"업종": name, "20일 수익률(%)": round(change20, 2)})
    return pd.DataFrame(rows).sort_values("20일 수익률(%)", ascending=False) if rows else pd.DataFrame()


def _export_history():
    if not EXPORT_FILE.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_csv(EXPORT_FILE)
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in ("export_yoy", "semi_yoy"):
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.dropna(subset=["date"]).sort_values("date")
    except Exception:
        return pd.DataFrame()


def _risk_summary(breadth, usd_change, vix, vix_change, kospi_change, kosdaq_change, flow):
    score = 50
    reasons = []
    valid_breadth = bool(breadth and breadth.get("rising", 0) + breadth.get("falling", 0) > 0)
    if valid_breadth:
        if breadth["ratio"] >= 60:
            score -= 12
            reasons.append("상승 종목 확산")
        elif breadth["ratio"] <= 40:
            score += 15
            reasons.append("하락 종목 우세")
    if usd_change is not None:
        if usd_change >= 2:
            score += 12
            reasons.append("원화 약세")
        elif usd_change <= -2:
            score -= 8
            reasons.append("원화 강세")
    if vix is not None:
        if vix >= 30:
            score += 20
            reasons.append("VIX 고위험")
        elif vix >= 20:
            score += 9
            reasons.append("변동성 경계")
        elif vix < 16:
            score -= 7
    if vix_change is not None and vix_change >= 20:
        score += 8
        reasons.append("변동성 급등")
    foreign20 = flow.get("외국인20") if flow else None
    if foreign20 is not None:
        if foreign20 > 0:
            score -= 8
            reasons.append("외국인 20일 순매수")
        elif foreign20 < 0:
            score += 10
            reasons.append("외국인 20일 순매도")
    if kospi_change is not None and kosdaq_change is not None and kosdaq_change < kospi_change - 5:
        score += 5
        reasons.append("중소형주 상대약세")
    score = int(np.clip(score, 0, 100))
    if score <= 32:
        label, weight = "🟢 우호적", "신규매수 정상 집행"
    elif score <= 55:
        label, weight = "🔵 중립", "분할매수·종목선별"
    elif score <= 72:
        label, weight = "🟡 경계", "신규매수 비중 50% 이하"
    else:
        label, weight = "🔴 위험", "현금 비중 확대·신규매수 보류"
    return score, label, weight, reasons


def _money(value):
    if value is None:
        return "자료 없음"
    return f"{value / 100_000_000_000:+.2f}천억"


def render_market_environment(market_is_open=False):
    breadth = _market_breadth()
    flow = _investor_flow()
    kospi, kospi20, kospi_frame = _last_close("^KS11", "1y")
    kosdaq, kosdaq20, _ = _last_close("^KQ11", "3mo")
    usdkrw, usd20, _ = _last_close("KRW=X", "3mo")
    vix, vix20, _ = _last_close("^VIX", "3mo")
    us10y, us10y20, _ = _last_close("^TNX", "3mo")

    score, label, action, reasons = _risk_summary(
        breadth, usd20, vix, vix20, kospi20, kosdaq20, flow
    )

    top1, top2, top3 = st.columns([1, 1, 1.15])
    top1.metric("시장 위험점수", f"{score}/100", label)
    top2.metric("한국 정규장", "OPEN" if market_is_open else "CLOSED", "09:00~15:30 KST")
    top3.metric("오늘의 대응", action)
    if reasons:
        st.caption("판단 근거 · " + " · ".join(reasons))

    st.markdown("### 시장 내부 건강도")
    b1, b2, b3, b4 = st.columns(4)
    valid_breadth = bool(breadth and breadth.get("rising", 0) + breadth.get("falling", 0) > 0)
    b1.metric("상승 종목 비율", f"{breadth.get('ratio', 0):.1f}%" if valid_breadth else "자료 없음")
    b2.metric("상승 / 하락", f"{breadth.get('rising', 0):,} / {breadth.get('falling', 0):,}" if valid_breadth else "자료 없음")
    b3.metric("KOSPI 20일", f"{kospi20:+.1f}%" if kospi20 is not None else "자료 없음")
    b4.metric("KOSDAQ 20일", f"{kosdaq20:+.1f}%" if kosdaq20 is not None else "자료 없음")
    if valid_breadth:
        source = breadth.get("source", "KRX KOSPI·KOSDAQ 전체 종목")
        st.caption(f"시장 폭 기준일: {breadth['date']} · {source} · 상승 종목 비율은 보합을 제외해 계산")

    st.markdown("### 수급·환율·글로벌 위험")
    r1, r2, r3, r4, r5 = st.columns(5)
    flow_days = flow.get("period_days", 20) if flow else 20
    r1.metric(f"외국인 {flow_days}일", _money(flow.get("외국인20") if flow else None))
    r2.metric(f"기관 {flow_days}일", _money(flow.get("기관20") if flow else None))
    r3.metric("원/달러", f"{usdkrw:,.1f}원" if usdkrw else "자료 없음", f"20일 {usd20:+.1f}%" if usd20 is not None else None)
    r4.metric("VIX", f"{vix:.1f}" if vix else "자료 없음", f"20일 {vix20:+.1f}%" if vix20 is not None else None)
    r5.metric("미국 10년물", f"{us10y:.2f}%" if us10y else "자료 없음", f"20일 {us10y20:+.1f}%" if us10y20 is not None else None)
    if flow:
        market_note = "KOSPI·KOSDAQ" if flow_days == 1 else "KOSPI"
        st.caption(f"KRX 수급 기준일: {flow.get('date', '확인 불가')} · 금액은 {market_note} 누적 순매수")

    sectors = _sector_strength()
    if not sectors.empty:
        st.markdown("### 주요 업종 20일 상대강도")
        left, right = st.columns([1.6, 1])
        left.bar_chart(sectors.set_index("업종"), horizontal=True)
        right.dataframe(sectors, use_container_width=True, hide_index=True)

    st.markdown("### KOSPI와 한국 수출")
    exports = _export_history()
    if not kospi_frame.empty:
        k = pd.DataFrame({"KOSPI": kospi_frame["Close"]})
        monthly = k.resample("ME").last()
        monthly["KOSPI YoY"] = monthly["KOSPI"].pct_change(12) * 100
        chart = monthly[["KOSPI YoY"]]
        if not exports.empty:
            e = exports.set_index("date")
            columns = [c for c in ("export_yoy", "semi_yoy") if c in e.columns]
            chart = chart.join(e[columns], how="outer").sort_index().rename(
                columns={"export_yoy": "수출 YoY", "semi_yoy": "반도체 수출 YoY"}
            )
        st.line_chart(chart)
        latest = pd.Timestamp(kospi_frame.index[-1]).strftime("%Y-%m-%d")
        st.caption(f"가격: Yahoo Finance 조정주가 · 최근 가격 {latest} · 화면 갱신 {datetime.now(SEOUL):%Y-%m-%d %H:%M KST}")
    else:
        st.error("KOSPI 가격 데이터를 불러오지 못했습니다.")

    st.info("위험점수는 시장 폭·환율·VIX·외국인 수급·KOSDAQ 상대강도를 단순 합산한 보조지표이며 매매를 자동 결정하지 않습니다.")

