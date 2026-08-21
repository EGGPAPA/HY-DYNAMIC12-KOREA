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
SECTOR_STOCKS = {
    "반도체": {
        "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "042700.KS": "한미반도체",
    },
    "자동차": {
        "005380.KS": "현대차", "000270.KS": "기아", "012330.KS": "현대모비스",
    },
    "금융": {
        "105560.KS": "KB금융", "055550.KS": "신한지주", "086790.KS": "하나금융지주",
    },
    "헬스케어": {
        "207940.KS": "삼성바이오로직스", "068270.KS": "셀트리온", "196170.KQ": "알테오젠",
    },
    "2차전지": {
        "373220.KS": "LG에너지솔루션", "006400.KS": "삼성SDI", "247540.KQ": "에코프로비엠",
    },
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
            raise ValueError("empty breadth sample")
        return {
            "date": pd.Timestamp(close.index[-1]).strftime("%Y%m%d"),
            "rising": rising, "falling": falling, "flat": flat,
            "ratio": rising / max(rising + falling, 1) * 100,
            "source": f"대표 유동성 종목 {len(changes)}개 표본",
        }
    except Exception:
        changes = []
        latest = None
        for symbol in BREADTH_SAMPLE:
            frame = _history(symbol, "5d")
            close = pd.to_numeric(frame.get("Close"), errors="coerce").dropna() if not frame.empty else pd.Series(dtype=float)
            if len(close) >= 2:
                changes.append((float(close.iloc[-1]) / float(close.iloc[-2]) - 1) * 100)
                latest = frame.index[-1]
        if not changes:
            return {}
        rising = sum(value > 0 for value in changes)
        falling = sum(value < 0 for value in changes)
        flat = sum(value == 0 for value in changes)
        if rising + falling == 0:
            return {}
        return {
            "date": pd.Timestamp(latest).strftime("%Y%m%d") if latest is not None else "확인 불가",
            "rising": rising, "falling": falling, "flat": flat,
            "ratio": rising / (rising + falling) * 100,
            "source": f"대표 유동성 종목 {len(changes)}개 개별조회 표본",
        }


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


@st.cache_data(ttl=1800, show_spinner=False)
def _sector_stock_candidates():
    rows = []
    for sector, members in SECTOR_STOCKS.items():
        for symbol, name in members.items():
            frame = _history(symbol, "6mo")
            if frame.empty:
                continue
            close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
            volume = pd.to_numeric(frame.get("Volume"), errors="coerce").dropna()
            if len(close) < 61:
                continue
            price = float(close.iloc[-1])
            ret20 = (price / float(close.iloc[-21]) - 1) * 100
            ret60 = (price / float(close.iloc[-61]) - 1) * 100
            ma20 = float(close.tail(20).mean())
            ma60 = float(close.tail(60).mean())
            gap20 = (price / ma20 - 1) * 100 if ma20 else 0
            volume_ratio = None
            if len(volume) >= 20 and float(volume.tail(20).mean()) > 0:
                volume_ratio = float(volume.tail(5).mean() / volume.tail(20).mean())
            trend_score = float(np.clip(
                50 + ret20 * 1.8 + ret60 * .55 + gap20 * 1.1
                + (8 if price > ma20 > ma60 else 3 if price > ma60 else -10),
                0, 100,
            ))
            if price > ma20 > ma60 and ret20 >= 5:
                trend = "🟢 강한 상승"
            elif price > ma60 and ret20 > 0:
                trend = "🔵 상승"
            elif price > ma60:
                trend = "⚪ 중립"
            else:
                trend = "🟡 약세"
            rows.append({
                "업종": sector, "종목": name, "종목코드": symbol.split(".")[0],
                "현재가(원)": round(price), "20일 수익률(%)": round(ret20, 2),
                "60일 수익률(%)": round(ret60, 2), "20일선 대비(%)": round(gap20, 2),
                "거래량 배수": round(volume_ratio, 2) if volume_ratio is not None else None,
                "종목 추세점수": round(trend_score), "종목 추세": trend,
            })
    return pd.DataFrame(rows)


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


def _grade(score):
    if score >= 67:
        return "🟢 우호적"
    if score >= 45:
        return "🔵 중립"
    return "🟡 경계"


def _evaluations(breadth, flow, kospi20, kosdaq20, usd20, vix, vix20, us10y, us10y20, sectors):
    items = []
    if kospi20 is not None and kosdaq20 is not None:
        value = (kospi20 + kosdaq20) / 2
        score = float(np.clip(50 + value * 3, 0, 100))
        text = f"KOSPI {kospi20:+.1f}% · KOSDAQ {kosdaq20:+.1f}%"
        items.append(("지수 추세", score, text, 1.25))
    valid_breadth = bool(breadth and breadth.get("rising", 0) + breadth.get("falling", 0) > 0)
    if valid_breadth:
        ratio = float(breadth["ratio"])
        text = f"상승 {breadth['rising']} · 하락 {breadth['falling']} · 상승비율 {ratio:.1f}%"
        items.append(("시장 확산", ratio, text, 1.2))
    if usd20 is not None:
        score = float(np.clip(50 - usd20 * 6, 0, 100))
        items.append(("환율 환경", score, f"원/달러 20일 {usd20:+.1f}%", 1.0))
    if vix is not None:
        score = 90 if vix < 16 else 72 if vix < 20 else 50 if vix < 25 else 28 if vix < 30 else 10
        if vix20 is not None:
            score = float(np.clip(score - max(vix20, 0) * .5, 0, 100))
        items.append(("변동성", score, f"VIX {vix:.1f}" + (f" · 20일 {vix20:+.1f}%" if vix20 is not None else ""), 1.1))
    if us10y is not None:
        score = float(np.clip(78 - max(us10y - 3.5, 0) * 18 - max(us10y20 or 0, 0) * .7, 0, 100))
        items.append(("금리 부담", score, f"미 10년물 {us10y:.2f}%" + (f" · 20일 {us10y20:+.1f}%" if us10y20 is not None else ""), .8))
    if sectors is not None and not sectors.empty:
        values = pd.to_numeric(sectors["20일 수익률(%)"], errors="coerce").dropna()
        if not values.empty:
            positive = float(values.gt(0).mean() * 100)
            median = float(values.median())
            score = float(np.clip(positive * .7 + np.clip(50 + median * 4, 0, 100) * .3, 0, 100))
            items.append(("업종 확산", score, f"상승 업종 {int(values.gt(0).sum())}/{len(values)} · 중앙값 {median:+.1f}%", .9))
    foreign = flow.get("외국인20") if flow else None
    institution = flow.get("기관20") if flow else None
    if foreign is not None and institution is not None:
        combined = float(foreign + institution)
        score = float(np.clip(50 + combined / 1_000_000_000_000 * 16, 0, 100))
        days = flow.get("period_days", 20)
        items.append(("투자자 수급", score, f"{days}일 외국인 {_money(foreign)} · 기관 {_money(institution)}", 1.1))
    return items


def _overall_evaluation(items):
    if not items:
        return 50, "판단보류", "평가 가능한 데이터가 부족합니다."
    total_weight = sum(item[3] for item in items)
    score = int(round(sum(item[1] * item[3] for item in items) / total_weight))
    if score >= 72:
        return score, "적극 가능", "추세와 위험환경이 우호적입니다. 과열 종목만 피하고 분할 진입합니다."
    if score >= 58:
        return score, "선별 매수", "환경은 대체로 양호하지만 종목별 추세 확인이 필요합니다."
    if score >= 43:
        return score, "분할매수", "상반된 신호가 공존합니다. 평소 계획의 절반 이하로 나눠 접근합니다."
    if score >= 30:
        return score, "관망 우선", "불리한 요인이 더 많습니다. 신규매수보다 현금과 기존 보유 관리가 우선입니다."
    return score, "위험 회피", "시장 내부와 글로벌 위험이 동시에 악화된 구간입니다. 신규매수를 보류합니다."


def render_market_environment(market_is_open=False):
    breadth = _market_breadth()
    flow = _investor_flow()
    kospi, kospi20, kospi_frame = _last_close("^KS11", "1y")
    kosdaq, kosdaq20, _ = _last_close("^KQ11", "3mo")
    usdkrw, usd20, _ = _last_close("KRW=X", "3mo")
    vix, vix20, _ = _last_close("^VIX", "3mo")
    us10y, us10y20, _ = _last_close("^TNX", "3mo")

    sectors = _sector_strength()
    evaluations = _evaluations(
        breadth, flow, kospi20, kosdaq20, usd20, vix, vix20, us10y, us10y20, sectors
    )
    score, action, summary = _overall_evaluation(evaluations)
    confidence = len(evaluations)

    top1, top2, top3, top4 = st.columns([1, .8, 1, 1.2])
    top1.metric("시장 종합평가", f"{score}/100", _grade(score))
    top2.metric("한국 정규장", "OPEN" if market_is_open else "CLOSED", "09:00~15:30 KST")
    top3.metric("오늘의 대응", action)
    top4.metric("평가 신뢰도", f"{confidence}/7", "수집된 평가 항목")
    st.info(summary)

    st.markdown("### 항목별 시장 평가")
    evaluation_frame = pd.DataFrame([
        {"평가 항목": name, "점수": round(item_score), "판정": _grade(item_score), "판단 근거": detail}
        for name, item_score, detail, _ in evaluations
    ]).sort_values("점수", ascending=False)
    st.dataframe(
        evaluation_frame,
        use_container_width=True,
        hide_index=True,
        column_config={"점수": st.column_config.ProgressColumn("점수", min_value=0, max_value=100, format="%d")},
    )
    missing = 7 - confidence
    if missing:
        st.caption(f"현재 {missing}개 평가 항목은 원천 데이터 지연으로 제외했습니다. 없는 값을 0점으로 처리하지 않습니다.")
    if breadth and breadth.get("source"):
        st.caption(f"시장 확산: {breadth['source']} · 기준일 {breadth.get('date', '확인 불가')}")

    if not sectors.empty:
        st.markdown("### 주도·부진 업종 평가")
        left, right = st.columns([1.6, 1])
        left.bar_chart(sectors.set_index("업종"), horizontal=True)
        sector_view = sectors.copy()
        sector_view["평가"] = sector_view["20일 수익률(%)"].map(lambda value: "🟢 강세" if value >= 3 else "🔵 중립" if value >= -3 else "🟡 약세")
        right.dataframe(sector_view, use_container_width=True, hide_index=True)

        candidates = _sector_stock_candidates()
        if not candidates.empty:
            sector_returns = sectors.set_index("업종")["20일 수익률(%)"].to_dict()
            candidates["업종 강도점수"] = candidates["업종"].map(
                lambda name: round(float(np.clip(50 + sector_returns.get(name, 0) * 4, 0, 100)))
            )
            candidates["종합 주도점수"] = (
                candidates["종목 추세점수"] * .7 + candidates["업종 강도점수"] * .3
            ).round().astype(int)
            candidates["종합 평가"] = candidates["종합 주도점수"].map(_grade)
            trend_priority = {"🟢 강한 상승": 0, "🔵 상승": 1, "⚪ 중립": 2, "🟡 약세": 3}
            candidates["_추세순서"] = candidates["종목 추세"].map(trend_priority).fillna(9)
            candidates = candidates.sort_values(
                ["_추세순서", "종합 주도점수"], ascending=[True, False]
            )
            candidates["업종내 순위"] = candidates.groupby("업종").cumcount() + 1

            strong = candidates[candidates["종목 추세"] == "🟢 강한 상승"].copy()
            if not strong.empty:
                top_rows = st.session_state.get("kr_rows", [])
                top_map = {
                    str(row.get("_종목코드", "")).zfill(6): row
                    for row in top_rows[:12]
                    if row.get("_종목코드")
                }

                def top12_link(row):
                    if not top_rows:
                        return "분석 전", "-", "🔎 관심 등록 전", "전체시장 분석을 실행하면 TOP12와 자동 비교"
                    matched = top_map.get(str(row["종목코드"]).zfill(6))
                    if matched:
                        decision = str(matched.get("판정", "TOP12"))
                        if decision.startswith("🟢") or decision.startswith("🟡"):
                            interest = "⭐ 최우선 관심"
                            reason = "강한 상승 + TOP12 정량평가 동시 충족"
                        else:
                            interest = "👀 교차검증 관심"
                            reason = "강한 상승이며 TOP12 포함, 판정 조건 추가 확인"
                        return "🏆 포함", decision, interest, reason
                    return "미포함", "-", "👀 모멘텀 관심", "가격 추세는 강하지만 TOP12 수급·펀더멘털 조건 미충족"

                links = strong.apply(top12_link, axis=1, result_type="expand")
                links.columns = ["TOP12", "TOP12 판정", "관심 등급", "관심 이유"]
                strong = pd.concat([strong.reset_index(drop=True), links.reset_index(drop=True)], axis=1)
                st.markdown("### 🟢 강한 상승 종목 모아보기")
                st.caption("강한 상승은 가격·업종 모멘텀 평가이며 TOP12는 수급·유동성·펀더멘털까지 보는 별도 평가입니다. 두 조건이 겹치면 관심 우선순위를 높입니다.")
                st.dataframe(
                    strong[[
                        "관심 등급", "종목", "업종", "TOP12", "TOP12 판정",
                        "현재가(원)", "20일 수익률(%)", "60일 수익률(%)",
                        "20일선 대비(%)", "거래량 배수", "종합 주도점수",
                        "종합 평가", "관심 이유",
                    ]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "현재가(원)": st.column_config.NumberColumn(format="%,d원"),
                        "종합 주도점수": st.column_config.ProgressColumn(
                            "종합 주도점수", min_value=0, max_value=100, format="%d"
                        ),
                    },
                )
                if not top_rows:
                    st.info("현재는 강한 상승 종목만 표시했습니다. ‘전체시장 분석’을 실행하면 이 표에 TOP12 포함 여부와 최우선 관심 종목이 자동 표시됩니다.")
                else:
                    priority_count = int(strong["관심 등급"].eq("⭐ 최우선 관심").sum())
                    if priority_count:
                        st.success(f"강한 상승과 TOP12를 동시에 충족한 최우선 관심 종목이 {priority_count}개 있습니다.")
                    else:
                        st.warning("현재 강한 상승 종목 중 TOP12 매수후보와 동시에 겹치는 종목은 없습니다. 모멘텀 관찰만 하고 추격매수는 피하세요.")
            else:
                st.info("현재 기준을 모두 충족하는 ‘강한 상승’ 종목은 없습니다. 일반 상승 종목은 아래 상세표에서 확인하세요.")

            st.markdown("### 업종별 대표 종목")
            st.caption("업종 ETF 강도 30%와 개별종목 추세 70%를 합산합니다. 업종이 약하면 개별종목 점수도 보수적으로 평가합니다.")
            summary_rows = []
            for sector in sectors["업종"]:
                group = candidates[candidates["업종"] == sector].sort_values(
                    "종합 주도점수", ascending=False
                ).head(3)
                if group.empty:
                    continue
                names = group.apply(
                    lambda row: f"{row['종목']} {row['종합 주도점수']}점", axis=1
                ).tolist()
                summary_rows.append({
                    "업종": sector,
                    "업종 20일(%)": round(sector_returns.get(sector, 0), 2),
                    "업종 평가": "🟢 강세" if sector_returns.get(sector, 0) >= 3 else "🔵 중립" if sector_returns.get(sector, 0) >= -3 else "🟡 약세",
                    "대표 1": names[0] if len(names) > 0 else "-",
                    "대표 2": names[1] if len(names) > 1 else "-",
                    "대표 3": names[2] if len(names) > 2 else "-",
                })
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

            with st.expander("대표 종목 상세 평가 보기 · 추세순 정렬", expanded=True):
                detail_columns = [
                    "업종", "업종내 순위", "종목", "종목코드", "현재가(원)",
                    "20일 수익률(%)", "60일 수익률(%)", "20일선 대비(%)",
                    "거래량 배수", "종목 추세", "종합 주도점수", "종합 평가",
                ]
                st.dataframe(
                    candidates[detail_columns], use_container_width=True, hide_index=True,
                    column_config={
                        "현재가(원)": st.column_config.NumberColumn(format="%,d원"),
                        "종합 주도점수": st.column_config.ProgressColumn(
                            "종합 주도점수", min_value=0, max_value=100, format="%d"
                        ),
                    },
                )
                st.info("대표 종목은 업종 비교를 위한 관찰 후보입니다. 종목의 실적·공시·과열 여부를 확인한 뒤 TOP12 분석과 함께 사용하세요.")

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

    st.warning("종합평가는 추세·시장 확산·환율·VIX·금리·업종·수급을 가중평균한 보조지표입니다. 개별종목의 실적과 가격 추세를 대신하지 않습니다.")

