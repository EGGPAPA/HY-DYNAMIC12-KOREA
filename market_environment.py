import json
import os
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
import requests
from market_risk_summary import render_global_risk_summary
from korea_holdings_ui import get_kis_price, kis_ready

try:
    from pykrx import stock
except Exception:
    stock = None


SEOUL = ZoneInfo("Asia/Seoul")
EXPORT_FILE = Path("export_history.csv")
LEADER_ALERT_STATE = Path("data/leader_alert_state.json")
SECTOR_FLOW_STATE = Path("data/sector_flow_state.json")
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
    "방산·조선": {
        "012450.KS": "한화에어로스페이스", "042660.KS": "한화오션", "329180.KS": "HD현대중공업",
    },
    "전력·원전": {
        "034020.KS": "두산에너빌리티", "010120.KS": "LS ELECTRIC", "052690.KS": "한전기술",
    },
    "인터넷·게임": {
        "035420.KS": "NAVER", "035720.KS": "카카오", "259960.KS": "크래프톤",
    },
    "화학·소재": {
        "051910.KS": "LG화학", "096770.KS": "SK이노베이션", "005490.KS": "POSCO홀딩스",
    },
    "소비·유통": {
        "090430.KS": "아모레퍼시픽", "004170.KS": "신세계", "097950.KS": "CJ제일제당",
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
        if frame is not None and not frame.empty:
            frame = frame.copy()
            frame.index = pd.to_datetime(frame.index)
            if getattr(frame.index, "tz", None) is not None:
                frame.index = frame.index.tz_localize(None)
            return frame.dropna(subset=["Close"])
    except Exception:
        pass
    # Yahoo가 한국 종목을 일시적으로 누락해도 대표 종목/차트가 사라지지 않도록 보완합니다.
    if symbol.endswith((".KS", ".KQ")):
        try:
            code = symbol.split(".")[0]
            count = 270 if period == "1y" else 150
            response = requests.get(
                "https://fchart.stock.naver.com/sise.nhn",
                params={
                    "symbol": code, "timeframe": "day", "count": count, "requestType": "0",
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=12,
            )
            response.raise_for_status()
            records = []
            for item in ET.fromstring(response.content).iter("item"):
                values = item.attrib.get("data", "").split("|")
                if len(values) < 6:
                    continue
                records.append(values[:6])
            if records:
                fallback = pd.DataFrame(
                    records, columns=["Date", "Open", "High", "Low", "Close", "Volume"]
                )
                fallback["Date"] = pd.to_datetime(fallback["Date"], format="%Y%m%d")
                for column in ("Open", "High", "Low", "Close", "Volume"):
                    fallback[column] = pd.to_numeric(fallback[column], errors="coerce")
                return fallback.set_index("Date").dropna(subset=["Close"])
        except Exception:
            pass
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


def _latest_price_date(frame):
    """가격 데이터의 최신 기준일을 YYYY.MM.DD 형식으로 반환합니다."""
    if frame is None or frame.empty:
        return "기준일 확인 불가"
    try:
        return pd.Timestamp(frame.index[-1]).strftime("%Y.%m.%d")
    except Exception:
        return "기준일 확인 불가"


def _compact_indicator_text(icon, label, value, frame, change20):
    date = _latest_price_date(frame)
    if change20 is None or pd.isna(change20):
        delta = ":gray[20일 변동 확인 불가]"
    else:
        direction = "▼" if change20 < 0 else "▲" if change20 > 0 else "―"
        # 한국 주식시장 표기 관례: 상승 빨강, 하락 파랑
        color = "red" if change20 > 0 else "blue" if change20 < 0 else "gray"
        delta = f":{color}[20일 {direction} {abs(change20):.1f}%]"
    return f"{icon} **{label} {value}**  \n{date} · {delta}"


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
    rows=[]
    for name,ticker in SECTOR_ETFS.items():
        frame=_history(ticker,"6mo")
        close=pd.to_numeric(frame.get("Close"),errors="coerce").dropna() if not frame.empty else pd.Series(dtype=float)
        if len(close)<61:continue
        price=float(close.iloc[-1]);ma20=float(close.tail(20).mean());ma60=float(close.tail(60).mean())
        rows.append({"업종":name,"20일 수익률(%)":round((price/float(close.iloc[-21])-1)*100,2),"60일 수익률(%)":round((price/float(close.iloc[-61])-1)*100,2),"20·60일선":bool(price>ma20>ma60)})
    return pd.DataFrame(rows).sort_values("20일 수익률(%)",ascending=False) if rows else pd.DataFrame()

def _sector_flow_labels(sectors,breadth):
    today=datetime.now(SEOUL).strftime("%Y-%m-%d")
    try:state=json.loads(SECTOR_FLOW_STATE.read_text(encoding="utf-8"))
    except Exception:state={}
    labels={};scores={}
    for _,row in sectors.iterrows():
        name=str(row["업종"]);spread=float(breadth.get(name,50))
        score=float(np.clip(50+float(row["20일 수익률(%)"])*1.2+float(row["60일 수익률(%)"])*.45+(10 if bool(row["20·60일선"]) else -5)+(spread-50)*.2,0,100))
        target="주도" if score>=70 else "부진" if score<30 else "중립"
        item=state.get(name,{})
        confirmed=str(item.get("confirmed","중립"));pending=str(item.get("pending",""));count=int(item.get("count",0));last=str(item.get("last_date",""))
        if target==confirmed:pending="";count=0
        elif last!=today:
            count=count+1 if pending==target else 1;pending=target
            if count>=2:confirmed=target;pending="";count=0
        item={"confirmed":confirmed,"pending":pending,"count":count,"last_date":today,"score":round(score,1)};state[name]=item;scores[name]=round(score,1)
        if confirmed=="주도" and score>=55:label="🟢 주도업종"
        elif confirmed=="부진" and score<45:label="🔴 부진업종"
        elif score>=70:label="🟠 주도후보"
        elif score<30:label="🟡 약화"
        else:label="🔵 중립"
        labels[name]=label
    try:
        SECTOR_FLOW_STATE.parent.mkdir(parents=True,exist_ok=True);SECTOR_FLOW_STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception:pass
    return labels,scores


@st.cache_data(ttl=1800, show_spinner=False)
def _sector_stock_candidates(cache_version="sector-leaders-v2"):
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
            ma20_series = close.rolling(20).mean()
            ma60_series = close.rolling(60).mean()
            ret20_series = close.pct_change(20) * 100
            leader_flags = (
                (close > ma20_series) & (ma20_series > ma60_series) & (ret20_series >= 5)
            )
            leader_days = 0
            for flag in reversed(leader_flags.fillna(False).tolist()):
                if not flag:
                    break
                leader_days += 1
            leader_start = (
                pd.Timestamp(close.index[-leader_days]).strftime("%Y-%m-%d")
                if leader_days else "-"
            )
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
            if gap20 >= 12:
                entry_timing = "⏸️ 과열·추격금지"
                entry_reason = "20일선 이격 12% 이상 · 20일선 부근 눌림을 기다림"
            elif gap20 >= 6:
                entry_timing = "🟡 눌림 대기"
                entry_reason = "20일선 이격 6% 이상 · 현재가 추격보다 조정 대기"
            elif price >= ma20 and price > ma60 and (volume_ratio is None or volume_ratio >= .8):
                entry_timing = "🟢 1차 분할매수 검토"
                entry_reason = "20일선 위 0~6% · 중기 추세와 거래량 조건 양호"
            elif price < ma20 and price > ma60:
                entry_timing = "🔵 20일선 회복 확인"
                entry_reason = "60일선 위 조정 · 종가 기준 20일선 재돌파 확인"
            else:
                entry_timing = "⚪ 관망"
                entry_reason = "60일선 추세 또는 거래량 조건 미충족"
            first_watch = ma20 * 1.02
            second_watch = max(ma60 * 1.02, ma20 * .96)
            invalidation = ma60 * .97
            near_first = abs(price / first_watch - 1) <= .02 if first_watch else False
            above_ma20 = price >= ma20
            volume_ok = volume_ratio is not None and volume_ratio >= .70
            trend_valid = price > invalidation
            entry_ready = bool(
                trend == "🟢 강한 상승" and near_first and above_ma20 and volume_ok and trend_valid
            )
            entry_status = "🚨 1차 분할매수 조건 충족" if entry_ready else entry_timing
            rows.append({
                "업종": sector, "종목": name, "종목코드": symbol.split(".")[0],
                "현재가(원)": round(price), "20일 수익률(%)": round(ret20, 2),
                "60일 수익률(%)": round(ret60, 2), "20일선 대비(%)": round(gap20, 2),
                "거래량 배수": round(volume_ratio, 2) if volume_ratio is not None else None,
                "종목 추세점수": round(trend_score), "종목 추세": trend,
                "매수시기": entry_status, "매수시기 근거": entry_reason,
                "1차 관찰가(원)": round(first_watch), "2차 관찰가(원)": round(second_watch),
                "추세 무효선(원)": round(invalidation),
                "최초 포착일": leader_start, "주도 지속(거래일)": leader_days,
                "_진입조건충족": entry_ready, "_20일선": ma20, "_시세심볼": symbol,
                "_정배열": bool(price > ma20 > ma60),
                "_조건가격": near_first, "_조건20일선": above_ma20,
                "_조건거래량": volume_ok, "_조건추세": trend_valid,
            })
    return pd.DataFrame(rows)



@st.cache_data(ttl=10,show_spinner=False)
def _live_leader_prices(symbols):
    symbols=tuple(dict.fromkeys(str(x).strip() for x in symbols if str(x).strip()))
    if not symbols:return {}
    prices={}
    if kis_ready():
        for symbol in symbols:
            try:
                price=get_kis_price(symbol.split(".")[0])
                if price is not None and float(price)>0:prices[symbol]=float(price)
            except Exception:continue
    for period,interval in (("1d","1m"),("5d","1d")):
        missing=[x for x in symbols if x not in prices]
        if not missing:break
        try:
            data=yf.download(missing,period=period,interval=interval,prepost=False,auto_adjust=True,progress=False,threads=True,group_by="ticker")
            for symbol in missing:
                try:
                    frame=data[symbol] if isinstance(data.columns,pd.MultiIndex) else data
                    close=pd.to_numeric(frame["Close"],errors="coerce").dropna()
                    if not close.empty:prices[symbol]=float(close.iloc[-1])
                except Exception:continue
        except Exception:continue
    return prices

@st.fragment(run_every="10s")
def _render_live_strong_tables(strong):
    live=strong.copy()
    prices=_live_leader_prices(tuple(live["_시세심볼"].tolist())) if "_시세심볼" in live else {}
    if prices:
        live["현재가(원)"]=[round(prices.get(str(symbol),old)) for symbol,old in zip(live["_시세심볼"],live["현재가(원)"])]
    ma20=pd.to_numeric(live["_20일선"],errors="coerce")
    current=pd.to_numeric(live["현재가(원)"],errors="coerce")
    first=pd.to_numeric(live["1차 관찰가(원)"],errors="coerce")
    invalid=pd.to_numeric(live["추세 무효선(원)"],errors="coerce")
    gap=(current/ma20-1)*100
    live["20일선 대비(%)"]=gap.round(2)
    live["_조건가격"]=(current/first-1).abs()<=.02
    live["_조건20일선"]=current>=ma20
    live["_조건추세"]=current>invalid
    live["_진입조건충족"]=live["_조건가격"]&live["_조건20일선"]&live["_조건거래량"]&live["_조건추세"]
    live["매수시기"]=np.select(
        [live["_진입조건충족"],gap>=12,gap>=6,(current<ma20)&live["_조건추세"]],
        ["🚨 1차 분할매수 조건 충족","⏸️ 과열·추격금지","🟡 눌림 대기","🔵 20일선 회복 확인"],
        default="⚪ 관망",
    )
    sector_grade=live.get("업종 평가",pd.Series("🔵 중립",index=live.index))
    live["최종 매수판정"]=np.select(
        [live["_진입조건충족"]&sector_grade.eq("🟢 주도업종"),live["_진입조건충족"]&sector_grade.isin(["🟠 주도후보","🔵 중립"]),live["_진입조건충족"]],
        ["🚨 최종 매수조건 충족","🟡 소액 1차 검토","🔵 기술조건만·업종 회복 대기"],
        default="⏳ 대기",
    )
    timing_counts=live["최종 매수판정"].value_counts();timing_text=" · ".join(f"{name} {count}개" for name,count in timing_counts.items())
    st.info("업종 반영 최종 요약 · "+timing_text)
    st.caption("현재가는 KIS 국내 현재가를 최우선으로 10초마다 확인합니다. KIS 조회가 실패하면 Yahoo 1분 가격·최근 일봉 순으로 보완합니다.")
    st.dataframe(live[["종합 신호","최종 매수판정","종목","업종","업종 평가","TOP12","TOP12 판정","충족 수","부족 조건","매수시기","현재가(원)","1차 관찰가(원)","2차 관찰가(원)","추세 무효선(원)","20일 수익률(%)","60일 수익률(%)","20일선 대비(%)","거래량 배수","종합 주도점수","종합 평가","매수시기 근거","종합 근거"]],use_container_width=True,hide_index=True,column_config={
        "현재가(원)":st.column_config.NumberColumn(format="%,d원"),"1차 관찰가(원)":st.column_config.NumberColumn(format="%,d원"),"2차 관찰가(원)":st.column_config.NumberColumn(format="%,d원"),"추세 무효선(원)":st.column_config.NumberColumn(format="%,d원"),"종합 주도점수":st.column_config.ProgressColumn("종합 주도점수",min_value=0,max_value=100,format="%d")})
    st.markdown("#### 매수조건 확인")
    condition_view=live[["종목","현재가(원)","1차 관찰가(원)"]].copy()
    condition_view["관찰가 ±2%"]=live["_조건가격"].map(lambda v:"✅" if v else "대기")
    condition_view["20일선 위"]=live["_조건20일선"].map(lambda v:"✅" if v else "대기")
    condition_view["거래량 ≥0.7배"]=live["_조건거래량"].map(lambda v:"✅" if v else "대기")
    condition_view["무효선 위"]=live["_조건추세"].map(lambda v:"✅" if v else "대기")
    condition_view["최종 신호"]=live["최종 매수판정"]
    st.dataframe(condition_view,use_container_width=True,hide_index=True,column_config={"현재가(원)":st.column_config.NumberColumn(format="%,d원"),"1차 관찰가(원)":st.column_config.NumberColumn(format="%,d원")})
    ready=live[live["최종 매수판정"].eq("🚨 최종 매수조건 충족")].copy()
    st.markdown("#### 카카오 최종 매수조건 알림")
    k1,k2,k3=st.columns([1,1,1.4])
    k1.metric("카카오 연결","✅ 준비됨" if _kakao_ready() else "⚠️ 설정 필요")
    k2.metric("현재 최종 신호",f"{len(ready)}종목")
    auto_alert=k3.toggle("조건 신규충족 시 자동알림",value=True,disabled=not _kakao_ready(),key="leader_kakao_auto")
    if not auto_alert and _kakao_ready():st.caption("자동알림이 꺼져 있습니다. 앱을 초기화하면 기본 ON으로 시작합니다.")
    if st.button("📨 현재 최종 신호 카카오 알림 보내기",disabled=not _kakao_ready() or ready.empty,use_container_width=True,key="leader_kakao_manual"):
        ok,message=_send_kakao(_leader_alert_message(ready));st.success(message) if ok else st.error(message)
    if auto_alert and _kakao_ready() and not ready.empty:
        state=_load_alert_state();today=datetime.now(SEOUL).strftime("%Y-%m-%d")
        sent=set(state.get("final_entry_alerts",{}).get(today,[]))
        fresh=ready[~ready["종목코드"].astype(str).isin(sent)].copy()
        if not fresh.empty:
            ok,message=_send_kakao(_leader_alert_message(fresh))
            if ok:
                daily=state.setdefault("final_entry_alerts",{});daily[today]=sorted(sent|set(fresh["종목코드"].astype(str)))
                state["updated_at"]=datetime.now(SEOUL).isoformat();_save_alert_state(state)
                st.success("새 최종 매수조건 충족 종목을 카카오로 보냈습니다.")
            else:st.warning(message)
    st.caption("자동알림은 앱이 열려 있는 동안 10초 가격 갱신과 함께 작동하며, 같은 종목은 하루 한 번만 발송합니다.")

def _render_leader_stock_chart(all_candidates):
    st.markdown("### 📈 업종 대표 종목 차트")
    st.caption("종목을 선택하면 최근 1년 종가와 20일·60일 이동평균선, 거래량을 확인할 수 있습니다.")
    # 분석 데이터가 잠시 비어도 카카오를 포함한 전체 대표 종목은 선택할 수 있어야 합니다.
    options = [
        {
            "업종": sector, "종목": name, "종목코드": symbol.split(".")[0],
            "symbol": symbol, "label": f"{sector} · {name} ({symbol.split('.')[0]})",
        }
        for sector, members in SECTOR_STOCKS.items()
        for symbol, name in members.items()
    ]
    labels = [item["label"] for item in options]
    default_index = next(
        (i for i, label in enumerate(labels) if "삼성바이오로직스" in label), 0
    )
    selected = st.selectbox(
        "차트 종목", labels, index=default_index, key="kr_leader_stock_chart"
    )
    row = next(item for item in options if item["label"] == selected)
    symbol = row["symbol"]
    frame = _history(symbol, "1y")
    if frame.empty:
        st.warning("선택한 종목의 가격 데이터를 불러오지 못했습니다.")
        return
    close = pd.to_numeric(frame["Close"], errors="coerce")
    chart = pd.DataFrame({
        "종가": close,
        "20일선": close.rolling(20).mean(),
        "60일선": close.rolling(60).mean(),
    }).dropna(how="all")
    valid_close = close.dropna()
    price = float(valid_close.iloc[-1])
    ret20 = (price / float(valid_close.iloc[-21]) - 1) * 100 if len(valid_close) >= 21 else 0
    ret60 = (price / float(valid_close.iloc[-61]) - 1) * 100 if len(valid_close) >= 61 else 0
    ma20_series = valid_close.rolling(20).mean()
    ma60_series = valid_close.rolling(60).mean()
    leader_flags = (
        (valid_close > ma20_series)
        & (ma20_series > ma60_series)
        & (valid_close.pct_change(20) * 100 >= 5)
    )
    leader_days = 0
    for flag in reversed(leader_flags.fillna(False).tolist()):
        if not flag:
            break
        leader_days += 1
    leader_start = (
        pd.Timestamp(valid_close.index[-leader_days]).strftime("%Y-%m-%d")
        if leader_days else "-"
    )
    ma20 = float(valid_close.tail(20).mean())
    ma60 = float(valid_close.tail(60).mean())
    first_watch = ma20 * 1.02
    invalidation = ma60 * .97
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("현재가", f"{int(price):,}원")
    m2.metric("20일 수익률", f"{ret20:+.1f}%")
    m3.metric("60일 수익률", f"{ret60:+.1f}%")
    m4.metric("주도 지속", f"{leader_days}거래일")
    st.line_chart(chart, use_container_width=True, height=420)
    volume = (
        pd.to_numeric(frame["Volume"], errors="coerce").dropna()
        if "Volume" in frame else pd.Series(dtype=float)
    )
    if not volume.empty:
        st.caption("일별 거래량")
        st.bar_chart(volume.rename("거래량"), use_container_width=True, height=180)
    st.caption(
        f"최초 포착일 {leader_start} · "
        f"1차 관찰가 {int(first_watch):,}원 · "
        f"추세 무효선 {int(invalidation):,}원"
    )


EXPORT_LABELS = {
    "export_yoy": ("전체 수출", "한국 수출시장 전체"),
    "semi_yoy": ("반도체", "삼성전자 · SK하이닉스 · 한미반도체"),
    "auto_yoy": ("자동차·부품", "현대차 · 기아 · 현대모비스"),
    "ship_yoy": ("선박", "HD현대중공업 · 한화오션"),
    "bio_yoy": ("바이오·의약품", "삼성바이오로직스 · 셀트리온"),
    "battery_yoy": ("2차전지", "LG에너지솔루션 · 삼성SDI"),
}
CUSTOMS_HS_GROUPS = {
    "semi": ("8541", "8542"),
    "auto": ("8703", "8708"),
    "ship": ("8901",),
    "bio": ("3002", "3004"),
    "battery": ("8507",),
}
CUSTOMS_API_URL = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"


def _export_secret():
    try:
        raw_key = str(st.secrets.get("DATA_GO_KR_SERVICE_KEY", "")).strip()
        # 공공데이터포털은 Encoding/Decoding 키를 함께 제공합니다.
        # requests의 params가 URL 인코딩을 처리하므로 Encoding 키는 먼저 한 번 복원합니다.
        return unquote(raw_key)
    except Exception:
        return ""


def _customs_items(root):
    items = []
    for node in root.findall(".//item"):
        year = (node.findtext("year") or "").strip()
        value = pd.to_numeric(node.findtext("expDlr"), errors="coerce")
        if not year or pd.isna(value):
            continue
        date = pd.to_datetime(year.replace(".", "") + "01", format="%Y%m%d", errors="coerce")
        if pd.isna(date):
            continue
        items.append((date, float(value)))
    return items


@st.cache_data(ttl=21600, show_spinner=False)
def _customs_export_history(service_key):
    end = datetime.now(SEOUL).date().replace(day=1) - timedelta(days=1)
    start = (pd.Timestamp(end).replace(day=1) - pd.DateOffset(years=6)).date()
    base_params = {
        "serviceKey": service_key,
        "strtYymm": int(start.strftime("%Y%m")),
        "endYymm": int(end.strftime("%Y%m")),
    }

    def fetch(hs_code=None):
        params = dict(base_params)
        if hs_code:
            params["hsSgn"] = hs_code
        response = requests.get(CUSTOMS_API_URL, params=params, timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        result_code = (root.findtext(".//resultCode") or "").strip()
        if result_code and result_code not in {"00", "0"}:
            raise ValueError(root.findtext(".//resultMsg") or f"관세청 API 오류 {result_code}")
        rows = _customs_items(root)
        if not rows:
            return pd.Series(dtype=float)
        frame = pd.DataFrame(rows, columns=["date", "value"])
        return frame.groupby("date")["value"].sum().sort_index()

    try:
        values = {"export": fetch()}
        for group, codes in CUSTOMS_HS_GROUPS.items():
            series = [fetch(code) for code in codes]
            valid = [item for item in series if not item.empty]
            values[group] = pd.concat(valid, axis=1).sum(axis=1, min_count=1) if valid else pd.Series(dtype=float)

        frame = pd.DataFrame(values).sort_index()
        if frame.empty or frame["export"].dropna().shape[0] < 24:
            empty = pd.DataFrame()
            empty.attrs["api_error"] = f"정상 응답이나 전체 수출 월별 자료가 {frame['export'].dropna().shape[0]}개뿐입니다."
            return empty
        for column in values:
            frame[f"{column}_yoy"] = frame[column].pct_change(12, fill_method=None) * 100
        result = frame[[column for column in EXPORT_LABELS if column in frame]].reset_index()
        result.attrs["source"] = "관세청 품목별 수출입실적(GW)"
        return result
    except Exception as exc:
        empty = pd.DataFrame()
        empty.attrs["api_error"] = str(exc)[:160] if isinstance(exc, ValueError) else type(exc).__name__
        return empty


def _csv_export_history():
    if not EXPORT_FILE.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_csv(EXPORT_FILE)
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        yoy_columns = [name for name in frame.columns if name.endswith("_yoy")]
        for column in yoy_columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if "note" in frame:
            pending = frame["note"].astype(str).str.contains("원문 확인 후 입력", na=False)
            detail_columns = [column for column in yoy_columns if column != "export_yoy"]
            frame.loc[pending, detail_columns] = np.nan
        result = frame.dropna(subset=["date"]).sort_values("date")
        result.attrs["source"] = "저장 CSV(대체)"
        return result
    except Exception:
        return pd.DataFrame()


def _export_history():
    service_key = _export_secret()
    api_error = "Streamlit Secrets에서 DATA_GO_KR_SERVICE_KEY를 찾지 못했습니다."
    if service_key:
        official = _customs_export_history(service_key)
        if not official.empty:
            return official
        api_error = official.attrs.get("api_error", "관세청 API 응답에 자료가 없습니다.")
    fallback = _csv_export_history()
    fallback.attrs["api_error"] = api_error
    return fallback


def _export_card(exports):
    if exports.empty or "export_yoy" not in exports:
        return "📦 **한국 수출 · 확인 필요**  \\n최신 통계 없음"
    valid = exports.dropna(subset=["export_yoy"])
    if valid.empty:
        return "📦 **한국 수출 · 확인 필요**  \\n최신 통계 없음"
    latest = valid.iloc[-1]
    value = float(latest["export_yoy"])
    previous = float(valid.iloc[-2]["export_yoy"]) if len(valid) > 1 else value
    if value > 0 and value >= previous:
        state, color = "🟢 좋아지는 중", "red"
    elif value > 0:
        state, color = "🟡 증가세 둔화", "orange"
    else:
        state, color = "🔵 주의", "blue"
    direction = "증가" if value >= 0 else "감소"
    return (
        f"📦 **한국 수출 · {state}**  \\n"
        f"{latest['date']:%Y.%m} · :{color}[작년 동월보다 {abs(value):.1f}% {direction}]"
    )


def _render_export_details(exports, kospi_frame):
    card = _export_card(exports)
    state = card.split("**")[1].replace("한국 수출 · ", "") if "**" in card else "확인 필요"
    with st.expander(f"📦 수출동향 상세 보기 · {state}", expanded=False):
        st.caption("숫자보다 방향을 먼저 보세요. 수출이 좋아지고 관련 주도주도 상승하면 업종 흐름이 강해질 가능성이 높습니다.")
        source = exports.attrs.get("source", "데이터 없음") if not exports.empty else "데이터 없음"
        if source.startswith("저장 CSV"):
            reason = exports.attrs.get("api_error", "원인 확인 필요")
            st.warning(f"관세청 API에서 충분한 자료를 받지 못해 저장된 일부 자료를 표시합니다. 진단: {reason}")
        else:
            st.success(f"공식 데이터 연결됨 · {source} · 최근 5년 월별 자료")

        period_label = st.segmented_control(
            "차트 기간", ["1년", "3년", "5년"], default="5년", key="export_chart_period"
        )
        years = {"1년": 1, "3년": 3, "5년": 5}.get(period_label, 5)
        cutoff = pd.Timestamp.now().normalize() - pd.DateOffset(years=years)

        chart = pd.DataFrame()
        if not kospi_frame.empty:
            monthly = pd.DataFrame({"KOSPI": kospi_frame["Close"]}).resample("ME").last()
            monthly["KOSPI YoY"] = monthly["KOSPI"].pct_change(12) * 100
            chart = monthly[["KOSPI YoY"]]
        if not exports.empty:
            export_chart = exports.set_index("date")
            available = [column for column in EXPORT_LABELS if column in export_chart.columns]
            export_chart = export_chart[available].rename(
                columns={column: EXPORT_LABELS[column][0] + " YoY" for column in available}
            )
            chart = chart.join(export_chart, how="outer") if not chart.empty else export_chart
        chart = chart.loc[chart.index >= cutoff] if not chart.empty else chart
        if not chart.empty:
            st.line_chart(chart.sort_index())
        else:
            st.info("표시할 수출 통계가 없습니다.")

        rows = []
        if not exports.empty:
            for column, (label, leaders) in EXPORT_LABELS.items():
                if column not in exports:
                    continue
                valid = exports.dropna(subset=[column])
                if valid.empty:
                    continue
                latest = float(valid.iloc[-1][column])
                previous = float(valid.iloc[-2][column]) if len(valid) > 1 else latest
                if latest > 0 and latest >= previous:
                    easy = "🟢 좋아지는 중"
                    meaning = "수출 증가세가 유지되거나 개선"
                elif latest > 0:
                    easy = "🟡 증가세 둔화"
                    meaning = "증가 중이지만 이전보다 속도가 둔화"
                else:
                    easy = "🔵 주의"
                    meaning = "작년 같은 달보다 수출 감소"
                rows.append({
                    "분야": label,
                    "쉬운 판정": easy,
                    "작년 동월 대비": f"{latest:+.1f}%",
                    "현재 의미": meaning,
                    "관련 주도주": leaders,
                })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.info("품목 수치는 관세청 HS 품목군을 합산한 업종 참고지표입니다. 관련 주도주는 기업별 직접 수출액이나 단독 매수 신호가 아닙니다.")
        st.caption("실제 종목 매수는 TOP12·부의 점프·현재 5개월선 돌파·시장환경을 함께 확인하세요.")


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


def _secret(name, default=""):
    try:
        value = st.secrets.get(name, default)
        if value:
            return str(value).strip()
    except Exception:
        pass
    return str(os.getenv(name, default)).strip()


def _kakao_ready():
    return bool(
        _secret("KAKAO_REST_API_KEY")
        and _secret("KAKAO_CLIENT_SECRET")
        and _secret("KAKAO_REFRESH_TOKEN")
    )


@st.cache_data(ttl=60 * 60 * 5, show_spinner=False)
def _kakao_token(rest_key, client_secret, refresh_token):
    try:
        response = requests.post(
            "https://kauth.kakao.com/oauth/token",
            data={
                "grant_type": "refresh_token", "client_id": rest_key,
                "client_secret": client_secret, "refresh_token": refresh_token,
            },
            timeout=10,
        )
        data = response.json()
        return data.get("access_token") if response.ok else None
    except Exception:
        return None


def _send_kakao(text):
    token = _kakao_token(
        _secret("KAKAO_REST_API_KEY"), _secret("KAKAO_CLIENT_SECRET"),
        _secret("KAKAO_REFRESH_TOKEN"),
    )
    if not token:
        return False, "카카오 인증 토큰을 가져오지 못했습니다."
    link = _secret("KAKAO_REDIRECT_URI", "https://hy-dynamic12-korea-nfxvcb3ntgddwdeydldbsb.streamlit.app/")
    template = {
        "object_type": "text", "text": text,
        "link": {"web_url": link, "mobile_web_url": link},
        "button_title": "주도주 확인",
    }
    try:
        response = requests.post(
            "https://kapi.kakao.com/v2/api/talk/memo/default/send",
            headers={"Authorization": f"Bearer {token}"},
            data={"template_object": json.dumps(template, ensure_ascii=False)},
            timeout=10,
        )
        data = response.json()
        if response.ok and data.get("result_code") == 0:
            return True, "카카오 알림 전송 완료"
        return False, data.get("msg") or data.get("message") or f"HTTP {response.status_code}"
    except Exception as exc:
        return False, f"카카오 전송 오류: {type(exc).__name__}"


def _load_alert_state():
    try:
        if LEADER_ALERT_STATE.exists():
            return json.loads(LEADER_ALERT_STATE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_alert_state(state):
    try:
        LEADER_ALERT_STATE.parent.mkdir(exist_ok=True)
        LEADER_ALERT_STATE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _leader_alert_message(ready):
    lines = ["[HY DYNAMIC12 종합 신호 알림]", "TOP12 + 시장환경 단계 변경"]
    for _, row in ready.iterrows():
        volume_ratio = row.get("거래량 배수")
        volume_text = f"{float(volume_ratio):.2f}배" if pd.notna(volume_ratio) else "확인 불가"
        lines.extend([
            "",
            f"{row['종목']} ({row['업종']})",
            f"최종 신호 {row.get('최종 매수판정', row.get('종합 신호', '🚨 조건 충족'))}",
            f"TOP12 {row.get('TOP12 판정', '-')} / 시장조건 {row.get('충족 수', '-')}",
            f"부족 조건 {row.get('부족 조건', '없음')}",
            f"현재가 {row['현재가(원)']:,.0f}원 / 1차 관찰가 {row['1차 관찰가(원)']:,.0f}원",
            f"20일선 대비 {row['20일선 대비(%)']:+.1f}% / 거래량 {volume_text}",
            f"추세 무효선 {row['추세 무효선(원)']:,.0f}원 / {row.get('관심 등급', '')}",
        ])
    lines.append("\n조건 단계 알림이며 자동 주문 또는 투자 권고가 아닙니다.")
    return "\n".join(lines)


def render_market_environment(market_is_open=False):
    breadth = _market_breadth()
    flow = _investor_flow()
    kospi, kospi20, kospi_frame = _last_close("^KS11", "5y")
    kosdaq, kosdaq20, _ = _last_close("^KQ11", "3mo")
    usdkrw, usd20, usdkrw_frame = _last_close("KRW=X", "3mo")
    vix, vix20, vix_frame = _last_close("^VIX", "3mo")
    us10y, us10y20, us10y_frame = _last_close("^TNX", "3mo")
    wti, wti20, wti_frame = _last_close("CL=F", "3mo")

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

    exports = _export_history()
    render_global_risk_summary(
        usdkrw, usd20, us10y, us10y20, wti, wti20, vix, vix20,
        _compact_indicator_text,
        (usdkrw_frame, us10y_frame, wti_frame, vix_frame),
        export_markdown=_export_card(exports),
    )
    st.caption("시장가격은 Yahoo Finance 최근 종가 기준이며 장중 시세와 차이가 날 수 있습니다. 수출은 월별 통계 기준입니다.")
    _render_export_details(exports, kospi_frame)

    if not sectors.empty:
        st.markdown("### 주도·부진 업종 평가")
        candidates = _sector_stock_candidates()
        breadth_map=candidates.groupby("업종")["_정배열"].mean().mul(100).to_dict() if not candidates.empty else {}
        sector_labels,sector_scores=_sector_flow_labels(sectors,breadth_map)
        sector_view=sectors.copy();sector_view["상승 확산(%)"]=sector_view["업종"].map(breadth_map).fillna(0).round(1);sector_view["중기점수"]=sector_view["업종"].map(sector_scores);sector_view["평가"]=sector_view["업종"].map(sector_labels);sector_view=sector_view[["업종","평가","중기점수","20일 수익률(%)","60일 수익률(%)","20·60일선","상승 확산(%)"]]
        left,right=st.columns([1.6,1]);left.bar_chart(sector_view.set_index("업종")[["20일 수익률(%)","60일 수익률(%)"]],horizontal=True);right.dataframe(sector_view,use_container_width=True,hide_index=True)
        st.caption("매일 갱신하지만 20·60일 수익률, 이동평균 배열, 업종 내 상승 확산을 함께 봅니다. 주도·부진 확정은 거래일 기준 2회 연속 확인합니다.")

        if not candidates.empty:
            sector_returns = sectors.set_index("업종")["20일 수익률(%)"].to_dict()
            candidates["업종 강도점수"] = candidates["업종"].map(
                lambda name: round(float(np.clip(50 + sector_returns.get(name, 0) * 4, 0, 100)))
            )
            candidates["업종 평가"]=candidates["업종"].map(sector_labels).fillna("🔵 중립")
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
            all_candidates = candidates.copy()

            sector_leaders = candidates.groupby("업종", as_index=False).agg(
                **{
                    "업종 20일(%)": ("20일 수익률(%)", "mean"),
                    "업종 60일(%)": ("60일 수익률(%)", "mean"),
                    "상승종목 비율(%)": ("_정배열", lambda values: float(values.mean() * 100)),
                }
            )
            sector_leaders["ETF 20일(%)"] = sector_leaders["업종"].map(sector_returns).fillna(0)
            sector_leaders["업종점수"] = (
                50
                + sector_leaders["업종 20일(%)"] * 1.1
                + sector_leaders["업종 60일(%)"] * .35
                + (sector_leaders["상승종목 비율(%)"] - 50) * .25
                + sector_leaders["ETF 20일(%)"] * .8
            ).clip(0, 100).round(1)
            sector_leaders["평가"] = sector_leaders["업종점수"].map(
                lambda value: "🟢 강세" if value >= 70 else "🔵 중립" if value >= 55 else "🟡 약세"
            )
            sector_leaders = sector_leaders.sort_values("업종점수", ascending=False).head(5)
            leading_sectors = sector_leaders["업종"].tolist()
            candidates = candidates[candidates["업종"].isin(leading_sectors)].copy()
            candidates = candidates.sort_values(
                ["업종", "_추세순서", "종합 주도점수"], ascending=[True, True, False]
            )
            candidates["업종내 순위"] = candidates.groupby("업종").cumcount() + 1

            strong = candidates[candidates["종목 추세"] == "🟢 강한 상승"].copy()
            if not strong.empty:
                condition_fields = {
                    "_조건가격": "관찰가 ±2%",
                    "_조건20일선": "20일선 위",
                    "_조건거래량": "거래량 ≥0.7배",
                    "_조건추세": "무효선 위",
                }
                strong["충족 수"] = strong[list(condition_fields)].sum(axis=1).astype(int).map(
                    lambda count: f"{count}/4"
                )
                strong["부족 조건"] = strong.apply(
                    lambda row: ", ".join(
                        label for field, label in condition_fields.items() if not bool(row[field])
                    ) or "없음",
                    axis=1,
                )
                top_rows = st.session_state.get("kr_rows", [])
                top_map = {
                    str(row.get("_종목코드", "")).zfill(6): row
                    for row in top_rows[:12]
                    if row.get("_종목코드")
                }

                def top12_link(row):
                    if not top_rows:
                        return "분석 전", "-", "🔎 TOP12 분석 필요", "전체시장 분석을 실행하면 종합 신호를 계산"
                    matched = top_map.get(str(row["종목코드"]).zfill(6))
                    if matched:
                        decision = str(matched.get("판정", "TOP12"))
                        candidate = decision.startswith(("🟢", "🟡"))
                        active = decision.startswith("🟢")
                        not_overheated = str(matched.get("과열", "정상")) != "과열"
                        condition_count = int(str(row["충족 수"]).split("/")[0])
                        if active and bool(row["_진입조건충족"]) and not_overheated:
                            signal = "🟢 최우선 검토"
                            reason = "TOP12 적극매수 + 시장환경 4/4 + 비과열"
                        elif candidate and condition_count >= 3 and not_overheated:
                            signal = "🔵 매수 준비"
                            reason = "TOP12 매수후보 이상 + 시장환경 3/4 이상 + 비과열"
                        elif bool(row["_진입조건충족"]):
                            signal = "🟡 기술신호만·관찰"
                            reason = "시장환경은 충족했지만 TOP12 적극매수 조건 미충족"
                        else:
                            signal = "⚪ 대기"
                            reason = "TOP12 또는 시장환경 조건 추가 확인 필요"
                        return "🏆 포함", decision, signal, reason
                    signal = "🟡 기술신호만·관찰" if bool(row["_진입조건충족"]) else "⚪ 대기"
                    return "미포함", "-", signal, "가격 추세는 강하지만 TOP12 수급·펀더멘털 조건 미충족"

                links = strong.apply(top12_link, axis=1, result_type="expand")
                links.columns = ["TOP12", "TOP12 판정", "종합 신호", "종합 근거"]
                strong = pd.concat([strong.reset_index(drop=True), links.reset_index(drop=True)], axis=1)
                signal_counts = strong["종합 신호"].value_counts()
                signal_summary = " · ".join(
                    f"{label} {int(signal_counts.get(label, 0))}개"
                    for label in ["🟢 최우선 검토", "🔵 매수 준비", "🟡 기술신호만·관찰"]
                    if int(signal_counts.get(label, 0)) > 0
                )
                if int(signal_counts.get("🟢 최우선 검토", 0)) > 0:
                    st.success("종합 우선순위 · " + signal_summary)
                elif signal_summary:
                    st.info("종합 우선순위 · " + signal_summary)
                elif top_rows:
                    st.warning("현재 종합 매수 신호는 없습니다. 조건 단계가 올라오면 이 위치에 표시됩니다.")
                st.markdown("### 🟢 강한 상승 종목 모아보기")
                st.caption("강한 상승은 가격·업종 모멘텀 평가이며 TOP12는 수급·유동성·펀더멘털까지 보는 별도 평가입니다. 두 조건이 겹치면 관심 우선순위를 높입니다.")
                _render_live_strong_tables(strong)
                if not top_rows:
                    st.info("현재는 강한 상승 종목만 표시했습니다. ‘전체시장 분석’을 실행하면 이 표에 TOP12 포함 여부와 최우선 관심 종목이 자동 표시됩니다.")
                else:
                    priority_count = int(strong["종합 신호"].eq("🟢 최우선 검토").sum())
                    if priority_count:
                        st.success(f"강한 상승과 TOP12를 동시에 충족한 최우선 관심 종목이 {priority_count}개 있습니다.")
                    else:
                        st.warning("현재 강한 상승 종목 중 TOP12 매수후보와 동시에 겹치는 종목은 없습니다. 모멘텀 관찰만 하고 추격매수는 피하세요.")
            else:
                st.info("현재 기준을 모두 충족하는 ‘강한 상승’ 종목은 없습니다. 일반 상승 종목은 아래 상세표에서 확인하세요.")

            st.markdown("### 주도 업종 · 업종별 최강 종목")
            st.caption("업종 평균 20일·60일 성과, 상승종목 비율과 업종 ETF 흐름을 먼저 평가한 뒤 상위 5개 업종만 표시합니다.")
            summary_rows = []
            for sector in leading_sectors:
                group = candidates[candidates["업종"] == sector].sort_values(
                    "종합 주도점수", ascending=False
                ).head(3)
                if group.empty:
                    continue
                leader = group.iloc[0]
                sector_row = sector_leaders[sector_leaders["업종"] == sector].iloc[0]
                summary_rows.append({
                    "업종": sector,
                    "최강 종목": f"⭐ {leader['종목']}",
                    "현재가(원)": int(leader["현재가(원)"]),
                    "종목 20일(%)": round(float(leader["20일 수익률(%)"]), 1),
                    "최초 포착일": leader.get("최초 포착일", "-"),
                    "주도 지속": f"{int(leader.get('주도 지속(거래일)', 0))}일",
                    "매수 신호": "🟢 1차 검토" if leader["_진입조건충족"] else "⏳ 관찰",
                    "업종 20일(%)": round(float(sector_row["업종 20일(%)"]), 1),
                    "업종 60일(%)": round(float(sector_row["업종 60일(%)"]), 1),
                    "상승종목 비율(%)": round(float(sector_row["상승종목 비율(%)"])),
                    "업종 평가": sector_row["평가"],
                    "2·3위 종목": ", ".join(group.iloc[1:]["종목"].tolist()) or "-",
                    "업종점수": sector_row["업종점수"],
                })
            st.dataframe(
                pd.DataFrame(summary_rows), use_container_width=True, hide_index=True,
                column_config={
                    "현재가(원)": st.column_config.NumberColumn(format="%,d원"),
                    "업종점수": st.column_config.ProgressColumn(
                        "업종점수", min_value=0, max_value=100, format="%.1f"
                    ),
                },
            )

            with st.expander("대표 종목 상세 평가 보기 · 추세순 정렬", expanded=True):
                detail_columns = [
                    "업종", "업종내 순위", "종목", "종목코드", "현재가(원)",
                    "최초 포착일", "주도 지속(거래일)",
                    "매수시기", "1차 관찰가(원)", "2차 관찰가(원)", "추세 무효선(원)",
                    "20일 수익률(%)", "60일 수익률(%)", "20일선 대비(%)",
                    "거래량 배수", "종목 추세", "종합 주도점수", "종합 평가",
                ]
                st.dataframe(
                    candidates[detail_columns], use_container_width=True, hide_index=True,
                    column_config={
                        "현재가(원)": st.column_config.NumberColumn(format="%,d원"),
                        "1차 관찰가(원)": st.column_config.NumberColumn(format="%,d원"),
                        "2차 관찰가(원)": st.column_config.NumberColumn(format="%,d원"),
                        "추세 무효선(원)": st.column_config.NumberColumn(format="%,d원"),
                        "종합 주도점수": st.column_config.ProgressColumn(
                            "종합 주도점수", min_value=0, max_value=100, format="%d"
                        ),
                    },
                )
                st.info("대표 종목은 업종 비교를 위한 관찰 후보입니다. 종목의 실적·공시·과열 여부를 확인한 뒤 TOP12 분석과 함께 사용하세요.")

            with st.expander("📈 업종 대표 종목 차트 상세 보기", expanded=False):
                _render_leader_stock_chart(all_candidates)

    st.warning("종합평가는 추세·시장 확산·환율·VIX·금리·업종·수급을 가중평균한 보조지표입니다. 개별종목의 실적과 가격 추세를 대신하지 않습니다.")

