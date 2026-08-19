import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="실전운용 · ETF 자금배분", page_icon="💰", layout="wide")
SEOUL = ZoneInfo("Asia/Seoul")

ETF_MAP = {
    "TIGER 코리아TOP10": {"symbol": "292150.KS", "default_weight": 0.80},
    "KODEX200": {"symbol": "069500.KS", "default_weight": 0.80},
}

@st.cache_data(ttl=60)
def get_price(symbol):
    try:
        h = yf.Ticker(symbol).history(period="5d", auto_adjust=False)
        if h is None or h.empty:
            return None, None
        close = pd.to_numeric(h["Close"], errors="coerce").dropna()
        if close.empty:
            return None, None
        return float(close.iloc[-1]), h.index[-1]
    except Exception:
        return None, None


def regime_weight(regime, base):
    # 실전 기본값: 강세 100%, 중립 70%, 약세 30%.
    # 사용자가 수동 비중을 선택하면 그 값을 우선 사용한다.
    return {"강세장": 1.00, "중립장": 0.70, "약세장": 0.30}.get(regime, base)

st.title("💰 실전운용 · 오늘의 ETF 자금배분")
st.caption("백테스트가 아니라 실제 운용용 화면입니다. 총 운용자금에서 오늘 ETF에 얼마를 넣고 현금을 얼마 남길지 계산합니다.")

c1, c2, c3 = st.columns([1.2, 1, 1])
capital = c1.number_input("총 운용자금(원)", min_value=100_000, value=10_000_000, step=100_000, format="%d")
etf_name = c2.selectbox("운용 ETF", list(ETF_MAP.keys()))
regime = c3.selectbox("현재 시장상태", ["강세장", "중립장", "약세장"], index=0)

st.markdown("### 오늘 적용할 투자비중")
a, b = st.columns([2, 1])
a.info("기본 원칙: 강세장 100% · 중립장 70% · 약세장 30%. 필요하면 아래에서 직접 조정할 수 있습니다.")
auto_weight = regime_weight(regime, ETF_MAP[etf_name]["default_weight"])
manual = b.checkbox("비중 직접 조정")
if manual:
    weight_pct = st.slider("ETF 투자비중(%)", 0, 100, int(auto_weight * 100), 5)
    weight = weight_pct / 100
else:
    weight = auto_weight
    weight_pct = int(round(weight * 100))

price, price_date = get_price(ETF_MAP[etf_name]["symbol"])
target_etf = capital * weight
cash = capital - target_etf

if price and price > 0:
    shares = math.floor(target_etf / price)
    actual_etf = shares * price
    actual_cash = capital - actual_etf
else:
    shares = None
    actual_etf = target_etf
    actual_cash = cash

st.markdown("## 📌 오늘 실행안")
m1, m2, m3, m4 = st.columns(4)
m1.metric("총 운용자금", f"{capital:,.0f}원")
m2.metric("ETF 목표비중", f"{weight_pct}%")
m3.metric("ETF 투입 예정", f"{actual_etf:,.0f}원")
m4.metric("현금 보유", f"{actual_cash:,.0f}원")

if price:
    st.success(f"{etf_name} 최근 가격 약 {price:,.0f}원 기준 → **{shares:,}주 매수 가능** · 실제 ETF 투입 약 **{actual_etf:,.0f}원** · 잔여 현금 약 **{actual_cash:,.0f}원**")
    st.caption(f"가격 기준시점: {price_date} · 60초 캐시. 실제 주문 시 호가/체결가와 차이가 날 수 있습니다.")
else:
    st.warning("ETF 최근 가격을 가져오지 못했습니다. 비중 기준 금액만 계산했습니다.")

st.markdown("### 💵 분할매수 계획")
tranches = st.select_slider("분할 횟수", options=[1, 2, 3, 4], value=2)
if tranches == 1:
    ratios = [1.0]
elif tranches == 2:
    ratios = [0.60, 0.40]
elif tranches == 3:
    ratios = [0.50, 0.30, 0.20]
else:
    ratios = [0.40, 0.30, 0.20, 0.10]
rows = []
remaining = actual_etf
for i, r in enumerate(ratios, 1):
    amt = actual_etf * r if i < len(ratios) else remaining
    remaining -= amt
    q = math.floor(amt / price) if price else None
    rows.append({"차수": f"{i}차", "배분비중": f"{r*100:.0f}%", "예정금액(원)": round(amt), "참고수량(주)": q if q is not None else "-"})
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.markdown("### 🧭 운용 체크")
if regime == "강세장":
    st.success("강세장: 현금보다 ETF 보유를 우선합니다. 단기 흔들림만으로 비중을 줄이지 않는 보존형 운용입니다.")
elif regime == "중립장":
    st.info("중립장: ETF 70% / 현금 30%를 기본으로 두고 추세 재확인 후 남은 현금을 투입합니다.")
else:
    st.warning("약세장: ETF 30% / 현금 70%를 기본으로 방어합니다. 추세 회복 전 무리한 추가매수는 피합니다.")

st.divider()
st.caption("이 화면의 금액은 주문 제안 계산값이며 자동 주문을 전송하지 않습니다. 실제 체결 후에는 보유종목 관리에 체결가와 수량을 입력해 평균단가·현재수익률을 관리하세요.")
st.caption(f"계산시각: {datetime.now(SEOUL).strftime('%Y-%m-%d %H:%M:%S KST')}")
