import os
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


# ============================================================
# HY DYNAMIC12 KOREA V3.4
# - KOSPI + KOSDAQ 전체시장 1차 스크리닝
# - pykrx 명시적 *_by_ticker API 사용
# - 외국인/기관 수급 자동수집
# - 외국인 + 기관 + 상대강도 + 펀더멘털 = 하나의 종합의견
# - 최종 TOP12
# ============================================================

st.set_page_config(
    page_title="HY DYNAMIC12 KOREA V3.4",
    page_icon="🇰🇷",
    layout="wide",
)

SEOUL = ZoneInfo("Asia/Seoul")
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

FLOW_FILE = Path("investor_flow.csv")
EXPORT_FILE = Path("export_history.csv")
WATCHLIST_FILE = DATA_DIR / "korea_watchlist.json"

DEEP_CANDIDATE_COUNT = 120
FINAL_TOP_N = 12
MIN_TRADING_VALUE = 2_000_000_000
MIN_MARKET_CAP = 100_000_000_000


def save_json(path, obj):
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def market_open():
    now = datetime.now(SEOUL)
    return now.weekday() < 5 and time(9, 0) <= now.time() <= time(15, 30)


def clip(x, lo, hi):
    return float(np.clip(float(x), lo, hi))


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


def load_flow_fallback():
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


@st.cache_data(ttl=900)
def yf_history(ticker, period="1y"):
    try:
        return yf.Ticker(ticker).history(period=period, auto_adjust=True).dropna()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=1800)
def yf_batch_history(tickers):
    if not tickers:
        return pd.DataFrame()
    try:
        return yf.download(
            tickers=list(tickers),
            period="6mo",
            interval="1d",
            auto_adjust=True,
            group_by="ticker",
            threads=True,
            progress=False,
        )
    except Exception:
        return pd.DataFrame()


def latest_business_day():
    if PYKRX_OK:
        try:
            return stock.get_nearest_business_day_in_a_week()
        except Exception:
            pass

    d = datetime.now(SEOUL).date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def normalize_ticker_index(df):
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy()
    x.index = x.index.astype(str).str.zfill(6)
    x.index.name = "종목코드"
    return x


def safe_numeric(df, columns):
    out = df.copy()
    for c in columns:
        if c not in out.columns:
            out[c] = 0
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0)
    return out


@st.cache_data(ttl=1800)
def fetch_krx_market_snapshot():
    """
    V3.4 FIX:
    pykrx의 get_market_ohlcv_by_ticker()는 최근 KRX 응답 형식 변화에 따라
    내부에서 ['시가','고가','저가','종가'] 컬럼을 찾다가 실패할 수 있습니다.
    따라서 전체시장 1차 스크리닝에는 OHLCV API를 사용하지 않고,
    get_market_cap_by_ticker()의 종가/거래량/거래대금/시가총액을 사용합니다.
    """
    if not PYKRX_OK:
        return pd.DataFrame(), "pykrx 미설치"

    date = latest_business_day()
    frames = []

    try:
        for market in ["KOSPI", "KOSDAQ"]:
            cap = stock.get_market_cap_by_ticker(date, market=market)
            fund = stock.get_market_fundamental_by_ticker(date, market=market)

            cap = normalize_ticker_index(cap)
            fund = normalize_ticker_index(fund)

            if cap.empty:
                continue

            # market_cap API 자체에 종가/거래량/거래대금/시가총액이 포함됨
            cap = safe_numeric(
                cap,
                ["종가", "거래량", "거래대금", "시가총액"],
            )

            df = cap[["종가", "거래량", "거래대금", "시가총액"]].copy()

            # 등락률은 1차 필터의 보조값이므로, KRX OHLCV 호출 없이
            # yfinance 정밀분석 단계에서 실제 추세/수익률을 다시 계산함.
            df["등락률"] = 0.0

            if not fund.empty:
                keep = [
                    c for c in ["PER", "PBR", "EPS", "BPS", "DIV", "DPS"]
                    if c in fund.columns
                ]
                if keep:
                    df = df.join(fund[keep], how="left")

            df = df.reset_index()
            df["시장"] = market

            names = []
            for code in df["종목코드"]:
                try:
                    names.append(stock.get_market_ticker_name(code))
                except Exception:
                    names.append(code)
            df["종목명"] = names
            frames.append(df)

        if not frames:
            return pd.DataFrame(), f"KRX 전종목 시총/거래대금 조회 실패 ({date})"

        all_df = pd.concat(frames, ignore_index=True)

        # 투자자별 순매수: 실패하더라도 전체 분석은 계속 진행하고
        # investor_flow.csv가 있으면 fallback으로 보완
        all_df["외국인순매수"] = 0.0
        all_df["기관순매수"] = 0.0

        try:
            foreign = stock.get_market_net_purchases_of_equities_by_ticker(
                date, date, "ALL", "외국인"
            )
            foreign = normalize_ticker_index(foreign)
            if not foreign.empty and "순매수거래대금" in foreign.columns:
                foreign_map = pd.to_numeric(
                    foreign["순매수거래대금"], errors="coerce"
                ).fillna(0).to_dict()
                all_df["외국인순매수"] = (
                    all_df["종목코드"].map(foreign_map).fillna(0)
                )
        except Exception:
            pass

        try:
            inst = stock.get_market_net_purchases_of_equities_by_ticker(
                date, date, "ALL", "기관합계"
            )
            inst = normalize_ticker_index(inst)
            if not inst.empty and "순매수거래대금" in inst.columns:
                inst_map = pd.to_numeric(
                    inst["순매수거래대금"], errors="coerce"
                ).fillna(0).to_dict()
                all_df["기관순매수"] = (
                    all_df["종목코드"].map(inst_map).fillna(0)
                )
        except Exception:
            pass

        return all_df, f"KRX 자동수집 성공 · 기준일 {date} · OHLCV 우회모드"

    except Exception as e:
        return pd.DataFrame(), f"KRX 자동수집 실패: {type(e).__name__}: {e}"


def apply_flow_fallback(df):
    fb = load_flow_fallback()

    if df.empty or fb.empty:
        return df, False

    x = df.copy()
    m = fb.set_index("종목코드")

    for c in ["외국인순매수", "기관순매수"]:
        if c not in x.columns:
            x[c] = 0

        repl = x["종목코드"].map(m[c])

        x[c] = np.where(
            pd.to_numeric(x[c], errors="coerce").fillna(0) == 0,
            pd.to_numeric(repl, errors="coerce").fillna(0),
            pd.to_numeric(x[c], errors="coerce").fillna(0),
        )

    return x, True


def percentile_score(series):
    s = pd.to_numeric(series, errors="coerce").fillna(0)
    return s.rank(pct=True, method="average") * 100


def preliminary_screen(df):
    d = safe_numeric(
        df,
        [
            "종가",
            "거래대금",
            "시가총액",
            "등락률",
            "외국인순매수",
            "기관순매수",
        ],
    )

    eligible = d[
        (d["종가"] > 0)
        & (d["거래대금"] >= MIN_TRADING_VALUE)
        & (d["시가총액"] >= MIN_MARKET_CAP)
    ].copy()

    if eligible.empty:
        return eligible, eligible

    eligible["유동성백분위"] = percentile_score(eligible["거래대금"])
    eligible["외국인백분위"] = percentile_score(eligible["외국인순매수"])
    eligible["기관백분위"] = percentile_score(eligible["기관순매수"])
    eligible["당일강도백분위"] = percentile_score(eligible["등락률"])

    eligible["1차점수"] = (
        eligible["유동성백분위"] * 0.30
        + eligible["외국인백분위"] * 0.25
        + eligible["기관백분위"] * 0.25
        + eligible["당일강도백분위"] * 0.20
    )

    candidates = (
        eligible.sort_values("1차점수", ascending=False)
        .head(DEEP_CANDIDATE_COUNT)
        .copy()
    )

    return eligible, candidates


def fundamental_score(row):
    score = 50.0

    per = float(row.get("PER", 0) or 0)
    pbr = float(row.get("PBR", 0) or 0)
    eps = float(row.get("EPS", 0) or 0)

    if per > 0:
        if per <= 15:
            score += 15
        elif per <= 25:
            score += 8
        elif per <= 40:
            score += 2
        else:
            score -= 8

    if pbr > 0:
        if pbr <= 1.5:
            score += 10
        elif pbr <= 3:
            score += 4
        elif pbr >= 6:
            score -= 8

    if eps > 0:
        score += 8
    elif eps < 0:
        score -= 10

    return clip(score, 0, 100)


def extract_history(batch, symbol):
    if batch.empty:
        return pd.DataFrame()

    try:
        if isinstance(batch.columns, pd.MultiIndex):
            level0 = list(batch.columns.get_level_values(0))
            if symbol in level0:
                return batch[symbol].dropna(how="all")

            # yfinance 버전에 따라 ticker가 두 번째 level일 수 있음
            level1 = list(batch.columns.get_level_values(1))
            if symbol in level1:
                return batch.xs(symbol, axis=1, level=1).dropna(how="all")

        return batch.dropna(how="all")
    except Exception:
        return pd.DataFrame()


def deep_analyze(eligible, candidates):
    if candidates.empty:
        return []

    symbol_map = {}
    symbols = []

    for _, r in candidates.iterrows():
        suffix = ".KS" if r["시장"] == "KOSPI" else ".KQ"
        symbol = f'{r["종목코드"]}{suffix}'
        symbol_map[r["종목코드"]] = symbol
        symbols.append(symbol)

    batch = yf_batch_history(tuple(symbols))

    f_pct = eligible.set_index("종목코드")["외국인백분위"].to_dict()
    i_pct = eligible.set_index("종목코드")["기관백분위"].to_dict()

    rows = []

    for _, r in candidates.iterrows():
        code = r["종목코드"]
        hist = extract_history(batch, symbol_map[code])

        if len(hist) < 61:
            continue

        close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
        volume = pd.to_numeric(hist["Volume"], errors="coerce").dropna()

        if len(close) < 61 or len(volume) < 20:
            continue

        price = float(close.iloc[-1])
        ma20 = float(close.tail(20).mean())
        ma60 = float(close.tail(60).mean())

        r20 = (price / float(close.iloc[-21]) - 1) * 100
        r60 = (price / float(close.iloc[-61]) - 1) * 100

        vol_ratio = float(
            volume.tail(5).mean()
            / max(float(volume.tail(20).mean()), 1.0)
        )

        high120 = float(close.tail(min(120, len(close))).max())
        high_pos = price / high120 * 100 if high120 > 0 else 0

        trend = clip(
            45
            + (18 if price > ma20 > ma60 else 0)
            + clip(r20, -15, 20) * 1.2
            + clip(r60, -25, 35) * 0.35,
            0,
            100,
        )

        price_pos = clip(
            100 - abs(high_pos - 92) * 4,
            0,
            100,
        )

        volume_score = clip(
            50 + (vol_ratio - 1) * 35,
            0,
            100,
        )

        rs = clip(
            50
            + clip(r20, -20, 30) * 1.1
            + clip(r60, -30, 50) * 0.45,
            0,
            100,
        )

        fund = fundamental_score(r)

        quality = (
            float(f_pct.get(code, 50)) * 0.30
            + float(i_pct.get(code, 50)) * 0.30
            + rs * 0.25
            + fund * 0.15
        )

        if quality >= 80:
            opinion = "🟢 강한 매수우위"
        elif quality >= 68:
            opinion = "🔵 매수우위"
        elif quality >= 55:
            opinion = "🟡 중립·개선중"
        elif quality >= 42:
            opinion = "🟠 수급 혼조"
        else:
            opinion = "🔴 매도우위"

        total = (
            trend * 0.30
            + price_pos * 0.15
            + volume_score * 0.15
            + quality * 0.40
        )

        overheated = high_pos >= 99 and r20 >= 15

        rows.append({
            "종목명": r["종목명"],
            "현재가": int(round(price)),
            "등락률": round(float(r.get("등락률", 0)), 2),
            "종합점수": round(total, 1),
            "추세": round(trend, 1),
            "가격위치": round(price_pos, 1),
            "거래량": round(volume_score, 1),
            "수급·질적 종합의견": opinion,
            "_수급질적점수": round(quality, 1),
            "_종목코드": code,
            "_시장": r["시장"],
            "과열": "⚠️ 과열" if overheated else "정상",
        })

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
    rows = sorted(
        rows,
        key=lambda x: x["종합점수"],
        reverse=True,
    )
    n = len(rows)

    if regime == "강세장":
        floor, active_pct = 78.0, 10.0
    elif regime == "약세장":
        floor, active_pct = 82.0, 3.0
    else:
        floor, active_pct = 78.0, 5.0

    for i, r in enumerate(rows, 1):
        pct = i / max(n, 1) * 100
        r["상대순위"] = f"상위 {pct:.1f}%"

        quality = float(r["_수급질적점수"])

        active = (
            r["종합점수"] >= floor
            and pct <= active_pct
            and quality >= 68
            and r["과열"] == "정상"
        )

        if active:
            judgment = "🟢 적극매수"
        elif r["종합점수"] >= 75 and quality >= 55:
            judgment = "🔵 매수후보"
        elif r["종합점수"] >= 65:
            judgment = "🟡 관찰"
        elif r["종합점수"] >= 55:
            judgment = "🟠 대기"
        else:
            judgment = "🔴 제외"

        if r["과열"] != "정상" and judgment in (
            "🟢 적극매수",
            "🔵 매수후보",
        ):
            judgment = "🟡 관찰"

        r["판정"] = judgment

    return rows, floor, active_pct


def color_judgment(v):
    s = str(v)
    if "적극매수" in s:
        return "background-color:#153d2a;color:#59e391;font-weight:700"
    if "매수후보" in s:
        return "background-color:#173a63;color:#74b9ff;font-weight:700"
    if "관찰" in s:
        return "background-color:#594a12;color:#ffd65a;font-weight:700"
    if "대기" in s:
        return "background-color:#5b3511;color:#ffad4d;font-weight:700"
    if "제외" in s:
        return "background-color:#5b2020;color:#ff7777;font-weight:700"
    return ""


def color_opinion(v):
    s = str(v)
    if "강한 매수우위" in s:
        return "color:#59e391;font-weight:700"
    if "매수우위" in s:
        return "color:#74b9ff;font-weight:700"
    if "중립" in s:
        return "color:#ffd65a;font-weight:700"
    if "혼조" in s:
        return "color:#ffad4d;font-weight:700"
    if "매도우위" in s:
        return "color:#ff7777;font-weight:700"
    return ""


st.title("🇰🇷 HY DYNAMIC12 KOREA V3.4")
st.caption(
    "KOSPI · KOSDAQ 전체시장 + KRX 외국인/기관 수급 + "
    "상대강도 + 펀더멘털 + KOSPI vs 수출"
)

tabs = st.tabs([
    "🌐 시장환경",
    "🔎 전체시장 분석",
    "🏆 TOP12",
    "🔔 카카오 준비",
    "⚙️ 설정",
])


with tabs[0]:
    regime = kospi_regime()

    c1, c2 = st.columns(2)
    c1.metric("현재 시장 레짐", regime)
    c2.metric(
        "한국 정규장",
        "OPEN" if market_open() else "CLOSED",
        "09:00~15:30 KST",
    )

    st.subheader("KOSPI vs 한국 수출 YoY")

    kd = yf_history("^KS11", "5y")
    exp = load_export_history()

    if not kd.empty:
        k = pd.DataFrame({"KOSPI": kd["Close"]})
        if getattr(k.index, "tz", None) is not None:
            k.index = k.index.tz_localize(None)

        monthly = k.resample("ME").last()
        monthly["KOSPI YoY"] = (
            monthly["KOSPI"].pct_change(12) * 100
        )

        if not exp.empty:
            e = exp.set_index("date")
            use = [
                c for c in ["export_yoy", "semi_yoy"]
                if c in e.columns
            ]

            chart = monthly[["KOSPI YoY"]].join(
                e[use],
                how="outer",
            ).sort_index()

            chart = chart.rename(columns={
                "export_yoy": "수출 YoY",
                "semi_yoy": "반도체 수출 YoY",
            })

            st.line_chart(chart)
        else:
            st.line_chart(monthly[["KOSPI YoY"]])


with tabs[1]:
    st.subheader("🔎 KOSPI + KOSDAQ 전체시장 분석")

    st.info(
        "전 종목을 유동성·외국인·기관·당일강도로 1차 스크리닝한 뒤 "
        f"상위 {DEEP_CANDIDATE_COUNT}종목을 정밀분석합니다."
    )

    if st.button(
        "① 전체시장 자동분석 실행",
        type="primary",
        use_container_width=True,
    ):
        with st.spinner("KRX 전체시장·수급 데이터 수집 중..."):
            snapshot, source_msg = fetch_krx_market_snapshot()

        if snapshot.empty:
            st.error(source_msg)
            st.warning(
                "requirements.txt에 pykrx가 설치되어 있는지 확인하세요. "
                "이번 V3.4은 KRX_ID/KRX_PW 없이 공개 pykrx 조회를 우선 사용합니다."
            )
        else:
            snapshot, fallback_used = apply_flow_fallback(snapshot)

            eligible, candidates = preliminary_screen(snapshot)

            if eligible.empty:
                st.error("유동성/시총 조건을 통과한 종목이 없습니다.")
            else:
                st.session_state["krx_source"] = (
                    source_msg
                    + (" + CSV 보완" if fallback_used else "")
                )
                st.session_state["eligible_count"] = len(eligible)
                st.session_state["candidate_count"] = len(candidates)

                st.write(
                    f"전체 적격종목 **{len(eligible):,}개** → "
                    f"정밀분석 후보 **{len(candidates):,}개**"
                )

                with st.spinner(
                    "가격추세·상대강도·펀더멘털·수급 정밀분석 중..."
                ):
                    rows = deep_analyze(
                        eligible,
                        candidates,
                    )

                regime = kospi_regime()
                rows, floor, active_pct = apply_relative(
                    rows,
                    regime,
                )

                st.session_state["kr_rows"] = rows
                st.session_state["kr_regime"] = regime

                active_watch = []
                for r in rows[:FINAL_TOP_N]:
                    if r["판정"].startswith("🟢 적극매수"):
                        active_watch.append({
                            "ticker": r["_종목코드"],
                            "name": r["종목명"],
                            "market": r["_시장"],
                            "score": r["종합점수"],
                            "relative_rank": r["상대순위"],
                            "opinion": r["수급·질적 종합의견"],
                        })

                save_json(WATCHLIST_FILE, active_watch)

                st.success(
                    f"정밀분석 {len(rows)}종목 완료 · {regime} · "
                    f"적극매수 기준 {floor:.0f}점 + 상위 {active_pct:.0f}%"
                )

                st.caption(
                    st.session_state["krx_source"]
                )


with tabs[2]:
    st.subheader("🏆 TOP12 최종 투자후보")

    rows = st.session_state.get("kr_rows", [])

    if not rows:
        st.info(
            "먼저 '전체시장 분석' 탭에서 자동분석을 실행하세요."
        )
    else:
        top = rows[:FINAL_TOP_N]

        cols = [
            "종목명",
            "현재가",
            "등락률",
            "종합점수",
            "추세",
            "가격위치",
            "거래량",
            "수급·질적 종합의견",
            "상대순위",
            "과열",
            "판정",
        ]

        df = pd.DataFrame(top)[cols]

        st.dataframe(
            df.style
            .format({
                "현재가": "{:,.0f}",
                "등락률": "{:+.2f}%",
                "종합점수": "{:.1f}",
                "추세": "{:.1f}",
                "가격위치": "{:.1f}",
                "거래량": "{:.1f}",
            })
            .map(
                color_opinion,
                subset=["수급·질적 종합의견"],
            )
            .map(
                color_judgment,
                subset=["판정"],
            ),
            use_container_width=True,
            hide_index=True,
            height=520,
        )

        active = [
            x for x in top
            if x["판정"].startswith("🟢 적극매수")
        ]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("TOP12", len(top))
        c2.metric("적극매수", len(active))
        c3.metric(
            "시장상태",
            st.session_state.get(
                "kr_regime",
                "중립장",
            ),
        )
        c4.metric(
            "전체 적격종목",
            st.session_state.get(
                "eligible_count",
                0,
            ),
        )

        st.caption(
            "수급·질적 종합의견 = 외국인 30% + 기관 30% + "
            "상대강도 25% + 펀더멘털 15%. "
            "네 항목은 화면에 따로 노출하지 않고 하나의 의견으로 표시합니다."
        )


with tabs[3]:
    st.subheader("🔔 카카오 자동감시 준비")

    st.write(
        "TOP12 중 최종 판정이 **🟢 적극매수**인 종목만 "
        "data/korea_watchlist.json에 저장합니다."
    )

    st.warning(
        "🔵 매수후보 / 🟡 관찰 / 🟠 대기 종목은 "
        "실제 매수 카카오 알림 대상이 아닙니다."
    )


with tabs[4]:
    st.markdown(
        f"""
### V3.4

**전체시장**
- KOSPI + KOSDAQ 전체 종목 자동조회
- 거래대금 {MIN_TRADING_VALUE/1e8:.0f}억원 이상
- 시가총액 {MIN_MARKET_CAP/1e8:.0f}억원 이상
- 상위 {DEEP_CANDIDATE_COUNT}종목 정밀분석
- 최종 TOP{FINAL_TOP_N}

**수급·질적 종합의견**
- 외국인 30%
- 기관 30%
- 상대강도 25%
- 펀더멘털 15%

**최종점수**
- 추세 30%
- 가격위치 15%
- 거래량 15%
- 수급·질적 종합 40%

**적극매수**
- 강세장: 78점 이상 + 상위 10%
- 중립장: 78점 이상 + 상위 5%
- 약세장: 82점 이상 + 상위 3%
- 수급·질적 점수 68 이상
- 과열 아님
"""
    )

    if not PYKRX_OK:
        st.error(
            "pykrx가 설치되지 않았습니다. "
            "requirements.txt를 V3.4용 파일로 교체하세요."
        )
