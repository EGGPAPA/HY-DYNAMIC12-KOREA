import json
from pathlib import Path
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

try:
    from pykrx import stock
    PYKRX_OK = True
except Exception:
    stock = None
    PYKRX_OK = False

st.set_page_config(page_title="HY DYNAMIC12 KOREA V3.9", page_icon="🇰🇷", layout="wide")

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
MIN_AVG_VALUE = 2_000_000_000  # 최근 20일 평균 거래대금 20억원

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
    if not EXPORT_FILE.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(EXPORT_FILE)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        for c in ["export_yoy", "semi_yoy"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["date"]).sort_values("date")
    except Exception:
        return pd.DataFrame()


def load_csv_universe():
    if not UNIVERSE_FILE.exists():
        return pd.DataFrame(columns=["종목코드", "종목명", "시장"])
    try:
        df = pd.read_csv(UNIVERSE_FILE, dtype={"종목코드": str})
        df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
        return df[["종목코드", "종목명", "시장"]].copy()
    except Exception:
        return pd.DataFrame(columns=["종목코드", "종목명", "시장"])


@st.cache_data(ttl=3600)
def get_full_universe():
    """
    KRX ticker-list를 우선 사용.
    실패하거나 너무 적게 조회되면 CSV + 기본 대형주/주도주 후보군을 합쳐
    TOP12가 비는 문제를 방지합니다.
    """
    rows = []
    source = ""
    date = latest_business_day()

    if PYKRX_OK:
        try:
            for market in ["KOSPI", "KOSDAQ"]:
                tickers = stock.get_market_ticker_list(date, market=market)
                for code in tickers:
                    try:
                        name = stock.get_market_ticker_name(code)
                    except Exception:
                        name = code
                    rows.append((str(code).zfill(6), name, market))
            if len(rows) >= 100:
                return pd.DataFrame(rows, columns=["종목코드","종목명","시장"]), f"KRX 전체 종목목록 · {date}"
        except Exception:
            rows = []

    # fallback: 사용자의 CSV + 내장 후보군
    merged = []
    seen = set()

    fb = load_csv_universe()
    if not fb.empty:
        for _, r in fb.iterrows():
            code = str(r["종목코드"]).zfill(6)
            if code not in seen:
                merged.append((code, str(r["종목명"]), str(r["시장"]).upper()))
                seen.add(code)

    for code, name, market in FALLBACK_UNIVERSE:
        if code not in seen:
            merged.append((code, name, market))
            seen.add(code)

    return pd.DataFrame(merged, columns=["종목코드","종목명","시장"]), "CSV + 기본 후보군 fallback"


def load_flow_csv():
    if not FLOW_FILE.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(FLOW_FILE, dtype={"종목코드": str})
        df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
        for c in ["외국인순매수", "기관순매수"]:
            if c not in df.columns:
                df[c] = 0
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=1800)
def get_auto_flow():
    """
    투자자 순매수 API만 독립적으로 시도.
    실패하면 앱 전체를 중단하지 않고 CSV fallback을 사용합니다.
    """
    date = latest_business_day()
    flow = {}
    auto_ok = False
    msg = ""

    if PYKRX_OK:
        try:
            for investor, key in [("외국인", "외국인순매수"), ("기관합계", "기관순매수")]:
                total = {}
                for market in ["KOSPI", "KOSDAQ"]:
                    df = stock.get_market_net_purchases_of_equities_by_ticker(
                        date, date, market, investor
                    )
                    if df is not None and not df.empty and "순매수거래대금" in df.columns:
                        s = pd.to_numeric(df["순매수거래대금"], errors="coerce").fillna(0)
                        for code, val in s.items():
                            total[str(code).zfill(6)] = float(val)
                for code, val in total.items():
                    flow.setdefault(code, {})[key] = val
            auto_ok = len(flow) > 0
            if auto_ok:
                msg = f"KRX 투자자 수급 자동수집 · {date}"
        except Exception as e:
            msg = f"KRX 수급 자동수집 실패: {type(e).__name__}"

    fb = load_flow_csv()
    if not fb.empty:
        for _, r in fb.iterrows():
            code = str(r["종목코드"]).zfill(6)
            flow.setdefault(code, {})
            for key in ["외국인순매수", "기관순매수"]:
                if float(flow[code].get(key, 0) or 0) == 0:
                    flow[code][key] = float(r.get(key, 0) or 0)
        msg += (" + " if msg else "") + "CSV 보완"

    return flow, auto_ok, msg or "수급 데이터 없음"


def yf_symbol(code, market):
    return f"{code}.KS" if market == "KOSPI" else f"{code}.KQ"


@st.cache_data(ttl=900)
def yf_history(ticker, period="1y"):
    """단일 지수/종목 히스토리 조회용."""
    try:
        d = yf.Ticker(ticker).history(
            period=period,
            interval="1d",
            auto_adjust=True,
        )
        return d.dropna()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=1200)
def get_single_history(symbol, period="6mo"):
    """배치조회에서 누락된 종목을 개별 재조회하여 TOP12 부족을 방지."""
    try:
        d = yf.Ticker(symbol).history(
            period=period,
            interval="1d",
            auto_adjust=True,
        )
        return d.dropna()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=1200)
def download_chunk(symbols, period="3mo"):
    if not symbols:
        return pd.DataFrame()
    try:
        return yf.download(
            tickers=list(symbols),
            period=period,
            interval="1d",
            auto_adjust=True,
            group_by="ticker",
            threads=True,
            progress=False,
        )
    except Exception:
        return pd.DataFrame()


def extract_one(batch, symbol):
    if batch.empty:
        return pd.DataFrame()
    try:
        if isinstance(batch.columns, pd.MultiIndex):
            l0 = batch.columns.get_level_values(0)
            l1 = batch.columns.get_level_values(1)
            if symbol in l0:
                return batch[symbol].dropna(how="all")
            if symbol in l1:
                return batch.xs(symbol, axis=1, level=1).dropna(how="all")
        return batch.dropna(how="all")
    except Exception:
        return pd.DataFrame()


def build_market_screen(universe, flow_map, progress=None):
    """
    KOSPI+KOSDAQ 전체 종목을 yfinance로 3개월 배치조회.
    1차평가: 평균거래대금 + 당일강도 + 외국인/기관 수급.
    """
    rows = []
    total = len(universe)

    for start in range(0, total, YF_CHUNK):
        part = universe.iloc[start:start + YF_CHUNK].copy()
        symbols = [yf_symbol(r["종목코드"], r["시장"]) for _, r in part.iterrows()]
        batch = download_chunk(tuple(symbols), "3mo")

        for _, r in part.iterrows():
            code = str(r["종목코드"]).zfill(6)
            symbol = yf_symbol(code, r["시장"])
            h = extract_one(batch, symbol)

            if len(h) < 22:
                continue

            close = pd.to_numeric(h["Close"], errors="coerce").dropna()
            volume = pd.to_numeric(h["Volume"], errors="coerce").dropna()

            if len(close) < 22 or len(volume) < 20:
                continue

            price = float(close.iloc[-1])
            if price < MIN_PRICE:
                continue

            avg_value = float((close.tail(20) * volume.tail(20)).mean())
            if avg_value < MIN_AVG_VALUE:
                continue

            day_ret = (price / float(close.iloc[-2]) - 1) * 100
            r20 = (price / float(close.iloc[-21]) - 1) * 100

            fm = flow_map.get(code, {})
            foreign = float(fm.get("외국인순매수", 0) or 0)
            inst = float(fm.get("기관순매수", 0) or 0)

            rows.append({
                "종목코드": code,
                "종목명": r["종목명"],
                "시장": r["시장"],
                "현재가": price,
                "평균거래대금": avg_value,
                "등락률": day_ret,
                "20일수익률": r20,
                "외국인순매수": foreign,
                "기관순매수": inst,
            })

        if progress is not None:
            progress.progress(min((start + len(part)) / max(total, 1), 1.0))

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    for c in ["평균거래대금", "등락률", "20일수익률", "외국인순매수", "기관순매수"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    df["유동성백분위"] = df["평균거래대금"].rank(pct=True) * 100
    df["당일강도백분위"] = df["등락률"].rank(pct=True) * 100
    df["20일강도백분위"] = df["20일수익률"].rank(pct=True) * 100

    flow_present = (
        (df["외국인순매수"].abs().sum() + df["기관순매수"].abs().sum()) > 0
    )

    if flow_present:
        df["외국인백분위"] = df["외국인순매수"].rank(pct=True) * 100
        df["기관백분위"] = df["기관순매수"].rank(pct=True) * 100
        df["1차점수"] = (
            df["유동성백분위"] * 0.25
            + df["당일강도백분위"] * 0.15
            + df["20일강도백분위"] * 0.15
            + df["외국인백분위"] * 0.225
            + df["기관백분위"] * 0.225
        )
    else:
        # 수급이 없을 때 임의로 0점을 주지 않고 가격/유동성으로만 후보 선정
        df["외국인백분위"] = 50.0
        df["기관백분위"] = 50.0
        df["1차점수"] = (
            df["유동성백분위"] * 0.40
            + df["당일강도백분위"] * 0.25
            + df["20일강도백분위"] * 0.35
        )

    return df.sort_values("1차점수", ascending=False).reset_index(drop=True)


def fundamental_score(code, market):
    """
    yfinance 기업정보를 이용한 보조 펀더멘털.
    값이 없으면 중립 50점.
    """
    ticker = yf_symbol(code, market)
    score = 50.0
    try:
        info = yf.Ticker(ticker).info or {}
        pe = info.get("trailingPE")
        pbr = info.get("priceToBook")
        roe = info.get("returnOnEquity")
        growth = info.get("earningsGrowth")

        if isinstance(pe, (int, float)) and pe > 0:
            score += 12 if pe <= 15 else (6 if pe <= 25 else (-8 if pe >= 50 else 0))
        if isinstance(pbr, (int, float)) and pbr > 0:
            score += 8 if pbr <= 1.5 else (3 if pbr <= 3 else (-6 if pbr >= 6 else 0))
        if isinstance(roe, (int, float)):
            score += clip((roe * 100 - 8) * 0.8, -10, 15)
        if isinstance(growth, (int, float)):
            score += clip(growth * 100 * 0.35, -10, 12)
    except Exception:
        pass
    return clip(score, 0, 100)


def deep_analyze(screen):
    """
    상위 후보를 정밀분석.
    배치조회에서 누락된 종목은 개별 재조회하고,
    최종적으로 최소 TOP12가 채워질 때까지 후보를 계속 확인합니다.
    """
    candidates = screen.head(max(DEEP_CANDIDATE_COUNT, FINAL_TOP_N * 4)).copy()
    symbols = [yf_symbol(r["종목코드"], r["시장"]) for _, r in candidates.iterrows()]
    batch = download_chunk(tuple(symbols), "6mo")
    rows = []

    flow_present = (
        (screen["외국인순매수"].abs().sum() + screen["기관순매수"].abs().sum()) > 0
    )

    for _, r in candidates.iterrows():
        code = r["종목코드"]
        symbol = yf_symbol(code, r["시장"])
        h = extract_one(batch, symbol)

        # 배치 데이터가 없거나 부족하면 개별 재조회
        if len(h) < 61:
            h = get_single_history(symbol, "6mo")

        if len(h) < 61:
            continue

        close = pd.to_numeric(h["Close"], errors="coerce").dropna()
        volume = pd.to_numeric(h["Volume"], errors="coerce").dropna()
        if len(close) < 61 or len(volume) < 20:
            continue

        price = float(close.iloc[-1])
        ma20 = float(close.tail(20).mean())
        ma60 = float(close.tail(60).mean())
        r20 = (price / float(close.iloc[-21]) - 1) * 100
        r60 = (price / float(close.iloc[-61]) - 1) * 100
        vol_ratio = float(volume.tail(5).mean() / max(float(volume.tail(20).mean()), 1))
        high = float(close.tail(min(120, len(close))).max())
        high_pos = price / high * 100 if high > 0 else 0

        trend = clip(
            45 + (18 if price > ma20 > ma60 else 0)
            + clip(r20, -15, 20) * 1.2
            + clip(r60, -25, 35) * 0.35,
            0, 100
        )
        price_pos = clip(100 - abs(high_pos - 92) * 4, 0, 100)
        volume_score = clip(50 + (vol_ratio - 1) * 35, 0, 100)
        rs = clip(50 + clip(r20, -20, 30) * 1.1 + clip(r60, -30, 50) * 0.45, 0, 100)
        fund = fundamental_score(code, r["시장"])

        if flow_present:
            foreign_pct = float(r["외국인백분위"])
            inst_pct = float(r["기관백분위"])
            quality = foreign_pct * 0.30 + inst_pct * 0.30 + rs * 0.25 + fund * 0.15
        else:
            # 실제 수급이 없으면 매수판정을 과도하게 만들지 않음
            quality = min(rs * 0.625 + fund * 0.375, 67.9)

        if not flow_present:
            opinion = "⚪ 실제 수급 데이터 대기"
        elif quality >= 80:
            opinion = "🟢 강한 매수우위"
        elif quality >= 68:
            opinion = "🔵 매수우위"
        elif quality >= 55:
            opinion = "🟡 중립·개선중"
        elif quality >= 42:
            opinion = "🟠 수급 혼조"
        else:
            opinion = "🔴 매도우위"

        total = trend * 0.30 + price_pos * 0.15 + volume_score * 0.15 + quality * 0.40
        overheated = high_pos >= 99 and r20 >= 15

        # 미국판 방식에 준한 '적극매수 가격대' 계산
        # 1차: 20일선/단기 눌림목. 현재가 대비 최대 약 -7% 범위.
        buy1_raw = max(price * 0.93, min(price * 0.995, ma20))
        buy1 = int(round(buy1_raw / 100.0) * 100)

        # 2차: 60일선 또는 1차 대비 추가 눌림. 현재가 대비 최대 약 -12% 범위.
        support2 = ma60 if ma60 < buy1 else buy1 * 0.97
        buy2_raw = max(price * 0.88, min(buy1 * 0.97, support2))
        buy2 = int(round(buy2_raw / 100.0) * 100)

        # 손절가: 2차 매수가 대비 -3%
        stop = int(round((buy2 * 0.97) / 100.0) * 100)

        rows.append({
            "종목명": r["종목명"],
            "현재가": int(round(price)),
            "등락률": round(float(r["등락률"]), 2),
            "종합점수": round(total, 1),
            "수급·질적 종합의견": opinion,
            "_수급질적점수": round(quality, 1),
            "_실제수급": flow_present,
            "_종목코드": code,
            "_시장": r["시장"],
            "과열": "⚠️ 과열" if overheated else "정상",
            "1차 매수가": buy1,
            "2차 매수가": buy2,
            "손절가(3%)": stop,
        })

        # 충분히 많은 종목은 분석하되 TOP12 확보가 우선
        if len(rows) >= DEEP_CANDIDATE_COUNT:
            break

    return rows


def kospi_regime():
    d = yf_history("^KS11", "1y")
    if len(d) < 200:
        return "중립장"
    c = d["Close"].dropna()
    p = float(c.iloc[-1])
    ma50 = float(c.tail(50).mean())
    ma200 = float(c.tail(200).mean())
    r20 = (p / float(c.iloc[-21]) - 1) * 100

    exp = load_export_history()
    export_ok = None
    if not exp.empty and "export_yoy" in exp.columns:
        s = exp["export_yoy"].dropna()
        if not s.empty:
            export_ok = float(s.iloc[-1]) > 0

    if p > ma50 > ma200 and r20 > 0 and export_ok is not False:
        return "강세장"
    if p < ma50 and p < ma200 and r20 < 0 and export_ok is False:
        return "약세장"
    return "중립장"


def apply_relative(rows, regime):
    rows = sorted(rows, key=lambda x: x["종합점수"], reverse=True)
    n = len(rows)
    floor, active_pct = ((78, 10) if regime == "강세장"
                         else ((82, 3) if regime == "약세장" else (78, 5)))

    for i, r in enumerate(rows, 1):
        pct = i / max(n, 1) * 100
        r["상대순위"] = f"상위 {pct:.1f}%"
        quality = float(r["_수급질적점수"])

        active = (
            r["_실제수급"]
            and r["종합점수"] >= floor
            and pct <= active_pct
            and quality >= 68
            and r["과열"] == "정상"
        )

        if active:
            judgment = "🟢 적극매수"
        elif r["_실제수급"] and r["종합점수"] >= 75 and quality >= 55:
            judgment = "🔵 매수후보"
        elif r["종합점수"] >= 65:
            judgment = "🟡 관찰"
        elif r["종합점수"] >= 55:
            judgment = "🟠 대기"
        else:
            judgment = "🔴 제외"

        if r["과열"] != "정상" and judgment in ("🟢 적극매수", "🔵 매수후보"):
            judgment = "🟡 관찰"

        r["판정"] = judgment
        r["KOREA점수"] = r["종합점수"]
        r["판정점수"] = r["종합점수"]
        r["시장상태"] = regime
        if not r.get("_실제수급", False):
            r["수급대응"] = "대기"
        elif float(r.get("_수급질적점수", 0)) >= 55:
            r["수급대응"] = "통과"
        else:
            r["수급대응"] = "대기"
    return rows, floor, active_pct


def color_judgment(v):
    # 판정 셀 전체 배경색은 사용하지 않습니다.
    # 등급별 색상은 판정 문자열 앞의 동그라미(🟢🔵🟡🟠🔴)에만 적용됩니다.
    return "color:#f2f2f2;font-weight:600"


def color_opinion(v):
    s = str(v)
    if "강한 매수우위" in s:
        return "color:#59e391;font-weight:700"
    if "매수우위" in s:
        return "color:#74b9ff;font-weight:700"
    if "중립" in s or "데이터 대기" in s:
        return "color:#ffd65a;font-weight:700"
    if "혼조" in s:
        return "color:#ffad4d;font-weight:700"
    if "매도우위" in s:
        return "color:#ff7777;font-weight:700"
    return ""


st.title("🇰🇷 HY DYNAMIC12 KOREA V3.9")
st.caption("KOSPI · KOSDAQ 전체시장 + KRX 종목목록/수급 + yfinance 가격·거래량 + KOSPI vs 수출 · USA판 형식 TOP12 · 적극매수 가격대 · 적극매수 종목 점멸 · 최종 3종목 후보")

tabs = st.tabs(["🌐 시장환경", "🔎 전체시장 분석", "🏆 TOP12", "🔔 카카오 준비", "⚙️ 설정"])

with tabs[0]:
    regime = kospi_regime()
    c1, c2 = st.columns(2)
    c1.metric("현재 시장 레짐", regime)
    c2.metric("한국 정규장", "OPEN" if market_open() else "CLOSED", "09:00~15:30 KST")

    st.subheader("KOSPI vs 한국 수출 YoY")
    kd = yf_history("^KS11", "5y")
    exp = load_export_history()

    if not kd.empty:
        k = pd.DataFrame({"KOSPI": kd["Close"]})
        if getattr(k.index, "tz", None) is not None:
            k.index = k.index.tz_localize(None)
        m = k.resample("ME").last()
        m["KOSPI YoY"] = m["KOSPI"].pct_change(12) * 100

        if not exp.empty:
            e = exp.set_index("date")
            use = [c for c in ["export_yoy", "semi_yoy"] if c in e.columns]
            chart = m[["KOSPI YoY"]].join(e[use], how="outer").sort_index()
            chart = chart.rename(columns={"export_yoy": "수출 YoY", "semi_yoy": "반도체 수출 YoY"})
            st.line_chart(chart)
        else:
            st.line_chart(m[["KOSPI YoY"]])

with tabs[1]:
    st.subheader("🔎 KOSPI + KOSDAQ 전체시장 분석")
    st.info("V3.6는 오류가 난 pykrx 전종목 OHLCV/시총 API를 사용하지 않습니다. KRX에서는 종목목록·투자자수급만 받고, 전 종목 가격/거래량은 yfinance 배치조회로 계산합니다.")

    if st.button("① 전체시장 자동분석 실행", type="primary", use_container_width=True):
        universe, uni_source = get_full_universe()
        if universe.empty:
            st.error("KOSPI/KOSDAQ 종목목록을 가져오지 못했습니다.")
        else:
            flow_map, flow_auto_ok, flow_msg = get_auto_flow()

            st.write(f"종목목록: **{len(universe):,}개** · {uni_source}")
            st.write(f"수급: **{flow_msg}**")

            bar = st.progress(0)
            with st.spinner("전체시장 가격·거래량 1차 스크리닝 중..."):
                screen = build_market_screen(universe, flow_map, bar)
            bar.empty()

            if screen.empty:
                st.error("가격/유동성 조건을 통과한 종목이 없습니다.")
            else:
                st.session_state["eligible_count"] = len(screen)
                st.session_state["candidate_count"] = min(DEEP_CANDIDATE_COUNT, len(screen))
                st.session_state["flow_auto_ok"] = flow_auto_ok
                st.session_state["flow_msg"] = flow_msg

                st.write(f"전체 적격종목 **{len(screen):,}개** → 정밀분석 후보 **{min(DEEP_CANDIDATE_COUNT, len(screen))}개**")

                with st.spinner("상위 후보 정밀분석 중..."):
                    rows = deep_analyze(screen)

                regime = kospi_regime()
                rows, floor, active_pct = apply_relative(rows, regime)
                st.session_state["kr_rows"] = rows
                st.session_state["kr_regime"] = regime

                top = rows[:FINAL_TOP_N]
                active_watch = []
                for r in top:
                    if r["판정"].startswith("🟢 적극매수"):
                        active_watch.append({
                            "ticker": r["_종목코드"], "name": r["종목명"],
                            "market": r["_시장"], "score": r["종합점수"],
                            "relative_rank": r["상대순위"],
                            "opinion": r["수급·질적 종합의견"],
                        })
                save_json(WATCHLIST_FILE, active_watch)

                st.success(f"정밀분석 {len(rows)}개 완료 · {regime} · 적극매수 기준 {floor}점 + 상위 {active_pct}%")
                if not flow_auto_ok:
                    st.warning("KRX 자동수급이 확보되지 않아 적극매수 판정은 잠금 상태입니다. 실제 수급 확보 전에는 관찰/대기만 표시합니다.")

with tabs[2]:
    st.subheader("🏆 TOP12")

    rows = st.session_state.get("kr_rows", [])
    if not rows:
        st.info("먼저 '전체시장 분석'에서 자동분석을 실행하세요.")
    else:
        top = rows[:FINAL_TOP_N]

        st.markdown(
            """
<style>
.hy-table-wrap {
    width: 100%;
    overflow-x: auto;
    border: 1px solid #2a2f38;
    border-radius: 10px;
}
.hy-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
    color: #f3f4f6;
    background: #0f1319;
}
.hy-table th {
    position: sticky;
    top: 0;
    background: #1a1f27;
    color: #d7dce3;
    padding: 10px 8px;
    border-bottom: 1px solid #333a45;
    text-align: right;
    white-space: nowrap;
}
.hy-table th:first-child,
.hy-table th:nth-child(2),
.hy-table td:first-child,
.hy-table td:nth-child(2) {
    text-align: left;
}
.hy-table td {
    padding: 10px 8px;
    border-bottom: 1px solid #242a33;
    text-align: right;
    white-space: nowrap;
}
.hy-table tr:hover td {
    background: #151b23;
}
.hy-stock {
    font-weight: 700;
    color: #f8fafc;
}
.hy-active-name {
    display: inline-block;
    font-weight: 800;
    color: #9df5b8;
    text-shadow: 0 0 6px rgba(91, 255, 146, 0.45);
    animation: hyBlink 1.35s ease-in-out infinite;
}
.hy-active-dot {
    color: #39e57c;
    margin-right: 6px;
    animation: hyPulse 1.35s ease-in-out infinite;
}
@keyframes hyBlink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.28; }
}
@keyframes hyPulse {
    0%, 100% {
        opacity: 1;
        text-shadow: 0 0 8px rgba(57,229,124,0.9);
    }
    50% {
        opacity: 0.35;
        text-shadow: 0 0 2px rgba(57,229,124,0.2);
    }
}
.hy-judge {
    color: #f3f4f6;
    font-weight: 700;
}
.hy-note {
    margin-top: 10px;
    color: #9ca3af;
    font-size: 13px;
}
</style>
""",
            unsafe_allow_html=True,
        )

        header = [
            "순위", "종목", "현재가(원)", "KOREA점수", "판정점수",
            "시장상태", "상대순위", "수급대응", "과열", "판정",
            "1차매수가(원)", "2차매수가(원)", "손절가(3%)(원)"
        ]

        html = ['<div class="hy-table-wrap"><table class="hy-table"><thead><tr>']
        for h in header:
            html.append(f"<th>{h}</th>")
        html.append("</tr></thead><tbody>")

        for idx, r in enumerate(top, 1):
            is_active = str(r.get("판정", "")).startswith("🟢 적극매수")

            if is_active:
                stock_html = (
                    f'<span class="hy-active-dot">●</span>'
                    f'<span class="hy-active-name">{r["종목명"]}</span>'
                )
            else:
                stock_html = f'<span class="hy-stock">{r["종목명"]}</span>'

            cells = [
                str(idx),
                stock_html,
                f'{r["현재가"]:,.0f}',
                f'{r.get("KOREA점수", r["종합점수"]):.1f}',
                f'{r.get("판정점수", r["종합점수"]):.1f}',
                str(r.get("시장상태", st.session_state.get("kr_regime","중립장"))),
                str(r["상대순위"]),
                str(r.get("수급대응","대기")),
                str(r["과열"]),
                f'<span class="hy-judge">{r["판정"]}</span>',
                f'{r["1차 매수가"]:,.0f}',
                f'{r["2차 매수가"]:,.0f}',
                f'{r["손절가(3%)"]:,.0f}',
            ]

            html.append("<tr>")
            for c in cells:
                html.append(f"<td>{c}</td>")
            html.append("</tr>")

        html.append("</tbody></table></div>")
        st.markdown("".join(html), unsafe_allow_html=True)

        st.markdown(
            '<div class="hy-note">🟢 적극매수 종목만 종목명이 부드럽게 점멸합니다. '
            '매수후보·관찰·대기·제외는 정적으로 표시됩니다.</div>',
            unsafe_allow_html=True,
        )

        st.caption(
            "판정 색상: 🟢 적극매수 · 🟡 매수후보 · 🔵 관찰 · ⚪ 현금대기/대기 · 🔴 제외 "
            "│ 셀 전체 배경색은 사용하지 않고 동그라미 색상만 유지합니다."
        )
        st.caption(
            "1차/2차 매수가는 20일선·60일선과 눌림목을 반영한 적극매수 가격대입니다. "
            "손절가는 2차 매수가 대비 -3%입니다."
        )

        st.markdown("## 최종 3종목 후보")
        active = [r for r in top if str(r["판정"]).startswith("🟢 적극매수")]

        if not active:
            st.warning(
                "현재 적극매수 종목이 없어 3종목을 억지로 선정하지 않습니다. "
                "매수후보는 추적만 합니다."
            )
        else:
            final3 = active[:3]
            final_df = pd.DataFrame([{
                "종목": r["종목명"],
                "판정": r["판정"],
                "종합점수": r["종합점수"],
                "1차매수가": r["1차 매수가"],
                "2차매수가": r["2차 매수가"],
                "손절가(3%)": r["손절가(3%)"],
            } for r in final3])
            st.dataframe(
                final_df.style.format({
                    "종합점수":"{:.1f}",
                    "1차매수가":"{:,.0f}",
                    "2차매수가":"{:,.0f}",
                    "손절가(3%)":"{:,.0f}",
                }),
                use_container_width=True,
                hide_index=True,
            )

with tabs[3]:
    st.subheader("🔔 카카오 자동감시 준비")
    st.write("TOP12 중 🟢 적극매수 종목만 data/korea_watchlist.json에 저장합니다.")
    if not st.session_state.get("flow_auto_ok", False):
        st.warning("현재 자동수급이 확보되지 않으면 적극매수 카카오 알림은 생성하지 않습니다.")

with tabs[4]:
    st.markdown("""
### V3.6 핵심
- TOP12
- KOSPI + KOSDAQ 전체 종목목록
- pykrx의 오류 구간인 전종목 OHLCV/시가총액 호출 완전 제거
- yfinance로 전체시장 가격/거래량 1차 스크리닝
- KRX 투자자 순매수 API는 독립 시도
- 외국인/기관 자동수급 실패 시 앱 전체를 중단하지 않음
- 실제 수급 미확보 시 적극매수는 잠금
- 수급·질적 종합의견은 외국인30 + 기관30 + 상대강도25 + 펀더멘털15
""")
