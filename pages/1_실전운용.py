import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="실전운용 · ETF 매수시점", page_icon="💰", layout="wide")
SEOUL = ZoneInfo("Asia/Seoul")

ETF_MAP = {
    "TIGER 코리아TOP10": {"symbol": "292150.KS"},
    "KODEX200": {"symbol": "069500.KS"},
}

@st.cache_data(ttl=60, show_spinner=False)
def get_history(symbol):
    try:
        h = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=True)
        if h is None or h.empty:
            return pd.DataFrame()
        h = h.copy()
        h.index = pd.to_datetime(h.index)
        if getattr(h.index, "tz", None) is not None:
            h.index = h.index.tz_localize(None)
        h["Close"] = pd.to_numeric(h["Close"], errors="coerce")
        return h.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


def analyze_signal(h):
    if h is None or len(h) < 165:
        return None
    c = h["Close"].copy()
    ma40 = c.rolling(40).mean()
    ma160 = c.rolling(160).mean()
    price = float(c.iloc[-1])
    p40 = float(ma40.iloc[-1])
    p160 = float(ma160.iloc[-1])
    prev_price = float(c.iloc[-2])
    prev40 = float(ma40.iloc[-2])
    slope40 = (float(ma40.iloc[-1]) / float(ma40.iloc[-6]) - 1) * 100 if pd.notna(ma40.iloc[-6]) else 0
    slope160 = (float(ma160.iloc[-1]) / float(ma160.iloc[-6]) - 1) * 100 if pd.notna(ma160.iloc[-6]) else 0
    reclaim40 = prev_price <= prev40 and price > p40
    above160 = price > p160
    trend_ok = p40 > p160 and slope40 > 0 and slope160 >= 0

    if not above160:
        signal = "⏸️ 신규매수 대기"
        action = "장기 추세가 아직 회복되지 않았습니다. 160일선 위로 올라오기 전에는 신규매수를 기다립니다."
        stage = 0
    elif reclaim40 and trend_ok:
        signal = "🟢 2차 매수 신호"
        action = "눌림 후 40일선을 다시 회복했습니다. 기존 1차 진입분이 있다면 추가매수 구간입니다."
        stage = 2
    elif trend_ok and price >= p40:
        signal = "🟢 1차 매수 가능"
        action = "가격이 160일선 위이고 40일선도 상승 중입니다. 신규 진입의 1차 매수 구간으로 봅니다."
        stage = 1
    elif above160 and price < p40:
        signal = "🟡 눌림 대기"
        action = "장기 추세는 유지되지만 단기 조정 중입니다. 40일선 재회복을 기다립니다."
        stage = 0
    else:
        signal = "🟡 추세 확인 대기"
        action = "160일선 위이지만 단기·장기 이동평균 정렬이 충분하지 않습니다. 추세 확인 후 진입합니다."
        stage = 0

    return {
        "price": price,
        "ma40": p40,
        "ma160": p160,
        "slope40": slope40,
        "slope160": slope160,
        "signal": signal,
        "action": action,
        "stage": stage,
        "reclaim40": reclaim40,
    }


def suggested_weight(stage):
    # 실전 신규진입 기준. 손절/익절 규칙은 이 페이지에서 사용하지 않는다.
    if stage == 1:
        return 0.50
    if stage == 2:
        return 0.80
    return 0.0


st.title("💰 실전운용 · ETF 매수시점")
st.caption("손익·손절 기준이 아니라, 40일선·160일선 추세를 이용해 오늘 신규매수/추가매수/대기 여부를 판단합니다.")

c1, c2 = st.columns([1.2, 1])
capital = c1.number_input("총 운용자금(원)", min_value=100_000, value=10_000_000, step=100_000, format="%d")
etf_name = c2.selectbox("운용 ETF", list(ETF_MAP.keys()))

h = get_history(ETF_MAP[etf_name]["symbol"])
sig = analyze_signal(h)

st.markdown("## 🎯 오늘의 매수시점 판단")
if sig is None:
    st.warning("이동평균 계산에 필요한 가격 데이터가 부족합니다.")
    st.stop()

m1, m2, m3, m4 = st.columns(4)
m1.metric("현재가", f"{sig['price']:,.0f}원")
m2.metric("40일선", f"{sig['ma40']:,.0f}원", f"기울기 {sig['slope40']:+.2f}%")
m3.metric("160일선", f"{sig['ma160']:,.0f}원", f"기울기 {sig['slope160']:+.2f}%")
m4.metric("현재 신호", sig["signal"])

if sig["stage"] > 0:
    st.success(sig["action"])
else:
    st.info(sig["action"])

st.markdown("### 판단 기준")
criteria = pd.DataFrame([
    {"조건": "장기 추세", "현재": "충족" if sig['price'] > sig['ma160'] else "미충족", "기준": "현재가 > 160일선"},
    {"조건": "추세 정렬", "현재": "충족" if sig['ma40'] > sig['ma160'] else "미충족", "기준": "40일선 > 160일선"},
    {"조건": "단기 추세", "현재": "상승" if sig['slope40'] > 0 else "하락/정체", "기준": "40일선 기울기 > 0"},
    {"조건": "40일선 재회복", "현재": "발생" if sig['reclaim40'] else "없음", "기준": "전일 40일선 이하 → 오늘 40일선 위"},
])
st.dataframe(criteria, use_container_width=True, hide_index=True)

base_weight = suggested_weight(sig["stage"])
manual = st.checkbox("추천 비중 직접 조정")
if manual:
    weight_pct = st.slider("ETF 투자비중(%)", 0, 100, int(base_weight * 100), 5)
    weight = weight_pct / 100
else:
    weight = base_weight
    weight_pct = int(round(weight * 100))

amount = capital * weight
shares = math.floor(amount / sig["price"]) if sig["price"] > 0 else 0
actual_amount = shares * sig["price"]
cash = capital - actual_amount

st.markdown("## 📌 오늘 실행안")
a, b, c, d = st.columns(4)
a.metric("총 운용자금", f"{capital:,.0f}원")
b.metric("오늘 ETF 비중", f"{weight_pct}%")
c.metric("오늘 매수 가능 수량", f"{shares:,}주")
d.metric("매수 후 예상 현금", f"{cash:,.0f}원")

if sig["stage"] == 1:
    st.success(f"1차 진입안: {etf_name} 약 **{shares:,}주**, 약 **{actual_amount:,.0f}원** 매수. 나머지는 40일선 눌림 후 재회복 신호를 기다립니다.")
elif sig["stage"] == 2:
    st.success(f"2차 진입안: 누적 목표비중 약 **{weight_pct}%**. 현재 가격 기준 약 **{shares:,}주 / {actual_amount:,.0f}원** 수준입니다.")
else:
    st.warning("오늘은 신규매수 신호가 아닙니다. 현금을 유지하고 다음 추세 신호를 기다립니다.")

st.markdown("## 🧭 실전 진입 순서")
plan = pd.DataFrame([
    {"단계": "1차", "조건": "현재가 > 160일선 + 40일선 > 160일선 + 40일선 상승", "목표 누적비중": "50%", "행동": "신규 진입"},
    {"단계": "2차", "조건": "눌림 후 40일선 재회복", "목표 누적비중": "80%", "행동": "추가매수"},
    {"단계": "3차", "조건": "추세 유지 확인 후 강세 지속", "목표 누적비중": "100%", "행동": "최종 비중 확대"},
])
st.dataframe(plan, use_container_width=True, hide_index=True)

st.markdown("### 📈 최근 가격과 이동평균")
chart = pd.DataFrame(index=h.index)
chart[etf_name] = h["Close"]
chart["40일선"] = h["Close"].rolling(40).mean()
chart["160일선"] = h["Close"].rolling(160).mean()
st.line_chart(chart.tail(220))

st.divider()
st.caption("이 페이지는 매수시점과 자금배분만 판단합니다. 예전의 +15%/+20% 익절, -3% 손절 같은 규칙은 사용하지 않습니다.")
st.caption("실제 주문은 자동 전송하지 않으며, 체결가는 증권사 호가와 다를 수 있습니다.")
st.caption(f"계산시각: {datetime.now(SEOUL).strftime('%Y-%m-%d %H:%M:%S KST')}")
