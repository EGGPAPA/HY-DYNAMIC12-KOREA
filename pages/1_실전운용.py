import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="실전운용 · ETF 매수/보유/매도", page_icon="💰", layout="wide")
SEOUL = ZoneInfo("Asia/Seoul")
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
KAKAO_STATE_FILE = DATA_DIR / "kakao_signal_state.json"

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
    prev160 = float(ma160.iloc[-2])
    slope40 = (float(ma40.iloc[-1]) / float(ma40.iloc[-6]) - 1) * 100 if pd.notna(ma40.iloc[-6]) else 0
    slope160 = (float(ma160.iloc[-1]) / float(ma160.iloc[-6]) - 1) * 100 if pd.notna(ma160.iloc[-6]) else 0

    reclaim40 = prev_price <= prev40 and price > p40
    break160 = prev_price >= prev160 and price < p160
    above160 = price > p160
    trend_ok = p40 > p160 and slope40 > 0 and slope160 >= 0

    if not above160:
        buy_signal = "⏸️ 신규매수 대기"
        buy_action = "장기 추세가 아직 회복되지 않았습니다. 160일선 위로 올라오기 전에는 신규매수를 기다립니다."
        buy_stage = 0
    elif reclaim40 and trend_ok:
        buy_signal = "🟢 추가매수"
        buy_action = "눌림 후 40일선을 다시 회복했습니다. 기존 1차 진입분이 있다면 추가매수 구간입니다."
        buy_stage = 2
    elif trend_ok and price >= p40:
        buy_signal = "🟢 1차 매수"
        buy_action = "가격이 160일선 위이고 40일선도 상승 중입니다. 신규 진입의 1차 매수 구간입니다."
        buy_stage = 1
    elif above160 and price < p40:
        buy_signal = "🟡 눌림 대기"
        buy_action = "장기 추세는 유지되지만 단기 조정 중입니다. 신규매수자는 40일선 재회복을 기다립니다."
        buy_stage = 0
    else:
        buy_signal = "🟡 추세 확인 대기"
        buy_action = "160일선 위이지만 단기·장기 이동평균 정렬이 충분하지 않습니다. 추세 확인 후 진입합니다."
        buy_stage = 0

    if price > p160:
        if price < p40 or slope40 <= 0:
            hold_signal = "🟢 보유"
            hold_action = "40일선 아래의 단기 조정이지만 아직 160일선 위입니다. 기존 보유분은 유지하고 장기 추세 훼손 여부를 봅니다."
            hold_weight = 1.00
        else:
            hold_signal = "🟢 보유 유지"
            hold_action = "단기·장기 추세가 모두 살아 있습니다. 기존 보유 비중을 유지합니다."
            hold_weight = 1.00
    elif price <= p160 and (slope160 >= 0 or break160):
        hold_signal = "🟠 비중축소"
        hold_action = "160일선을 이탈했습니다. 장기 추세 훼손 초기로 보고 보유 비중을 절반 수준으로 줄이는 방어 단계입니다."
        hold_weight = 0.50
    else:
        hold_signal = "🔴 매도 / 현금화"
        hold_action = "현재가가 160일선 아래이고 160일선 기울기도 하락입니다. 장기 추세 전환으로 보고 대부분 현금화합니다."
        hold_weight = 0.00

    return {
        "price": price,
        "ma40": p40,
        "ma160": p160,
        "slope40": slope40,
        "slope160": slope160,
        "buy_signal": buy_signal,
        "buy_action": buy_action,
        "buy_stage": buy_stage,
        "hold_signal": hold_signal,
        "hold_action": hold_action,
        "hold_weight": hold_weight,
        "reclaim40": reclaim40,
        "break160": break160,
    }


def suggested_weight(stage):
    if stage == 1:
        return 0.50
    if stage == 2:
        return 0.80
    return 0.0


def secret_value(name, default=""):
    try:
        value = st.secrets.get(name, default)
        return str(value).strip() if value is not None else default
    except Exception:
        return default


def kakao_config():
    return {
        "rest_api_key": secret_value("KAKAO_REST_API_KEY"),
        "client_secret": secret_value("KAKAO_CLIENT_SECRET"),
        "redirect_uri": secret_value("KAKAO_REDIRECT_URI"),
        "refresh_token": secret_value("KAKAO_REFRESH_TOKEN"),
    }


def kakao_ready():
    cfg = kakao_config()
    return all(cfg[k] for k in ["rest_api_key", "client_secret", "refresh_token"])


def kakao_access_token():
    cfg = kakao_config()
    if not kakao_ready():
        return None, "Streamlit Secrets에 카카오 인증값이 부족합니다."
    try:
        response = requests.post(
            "https://kauth.kakao.com/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": cfg["rest_api_key"],
                "refresh_token": cfg["refresh_token"],
                "client_secret": cfg["client_secret"],
            },
            timeout=10,
        )
        data = response.json()
        token = data.get("access_token")
        if response.ok and token:
            warning = ""
            if data.get("refresh_token"):
                warning = "카카오가 새 Refresh Token을 발급했습니다. 장기 운용을 위해 Streamlit Secrets의 KAKAO_REFRESH_TOKEN 갱신이 필요할 수 있습니다."
            return token, warning
        return None, data.get("error_description") or data.get("error") or f"HTTP {response.status_code}"
    except Exception as e:
        return None, f"토큰 갱신 오류: {type(e).__name__}"


def send_kakao_message(text):
    token, token_msg = kakao_access_token()
    if not token:
        return False, token_msg
    cfg = kakao_config()
    link_url = cfg["redirect_uri"] or "https://streamlit.io"
    template = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": link_url, "mobile_web_url": link_url},
        "button_title": "실전운용 보기",
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
            return True, token_msg or "카카오톡 전송 성공"
        return False, data.get("msg") or data.get("message") or f"HTTP {response.status_code}"
    except Exception as e:
        return False, f"메시지 전송 오류: {type(e).__name__}"


def load_signal_state():
    try:
        if KAKAO_STATE_FILE.exists():
            return json.loads(KAKAO_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_signal_state(state):
    try:
        KAKAO_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def signal_message(etf_name, sig):
    return (
        f"[HY DYNAMIC12 실전신호]\n"
        f"{etf_name}\n"
        f"신규매수: {sig['buy_signal']}\n"
        f"보유자: {sig['hold_signal']}\n"
        f"현재가 {sig['price']:,.0f}원\n"
        f"40일선 {sig['ma40']:,.0f}원 / 160일선 {sig['ma160']:,.0f}원\n"
        f"40일선 기울기 {sig['slope40']:+.2f}% / 160일선 기울기 {sig['slope160']:+.2f}%"
    )


st.title("💰 실전운용 · ETF 매수/보유/매도")
st.caption("손익·손절 기준이 아니라 40일선·160일선 추세로 신규매수자와 기존 보유자의 행동을 따로 판단합니다.")

c1, c2 = st.columns([1.2, 1])
capital = c1.number_input("총 운용자금(원)", min_value=100_000, value=10_000_000, step=100_000, format="%d")
etf_name = c2.selectbox("운용 ETF", list(ETF_MAP.keys()))

h = get_history(ETF_MAP[etf_name]["symbol"])
sig = analyze_signal(h)

st.markdown("## 🎯 오늘의 실전 신호")
if sig is None:
    st.warning("이동평균 계산에 필요한 가격 데이터가 부족합니다.")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("현재가", f"{sig['price']:,.0f}원")
m2.metric("40일선", f"{sig['ma40']:,.0f}원", f"기울기 {sig['slope40']:+.2f}%")
m3.metric("160일선", f"{sig['ma160']:,.0f}원", f"기울기 {sig['slope160']:+.2f}%")

st.markdown("### 👤 신규매수자 / 💼 기존 보유자")
b1, b2 = st.columns(2)
with b1:
    st.metric("신규매수 신호", sig["buy_signal"])
    if sig["buy_stage"] > 0:
        st.success(sig["buy_action"])
    else:
        st.info(sig["buy_action"])
with b2:
    st.metric("보유자 신호", sig["hold_signal"])
    if sig["hold_signal"].startswith("🔴"):
        st.error(sig["hold_action"])
    elif sig["hold_signal"].startswith("🟠"):
        st.warning(sig["hold_action"])
    else:
        st.success(sig["hold_action"])

st.markdown("### 🔔 카카오 실전 알림")
k1, k2, k3 = st.columns([1, 1, 1.4])
k1.metric("카카오 연결", "준비됨" if kakao_ready() else "설정 필요")
auto_kakao = k2.toggle("신호 변경 자동알림", value=True, disabled=not kakao_ready())
if k3.button("📨 현재 신호 테스트 전송", use_container_width=True, disabled=not kakao_ready()):
    ok, msg = send_kakao_message("[HY DYNAMIC12 테스트]\n카카오 실전 알림 연결이 정상입니다.\n\n" + signal_message(etf_name, sig))
    if ok:
        st.success(msg)
    else:
        st.error(msg)

state = load_signal_state()
current_key = f"{sig['buy_signal']}|{sig['hold_signal']}"
previous_key = state.get(etf_name)
if auto_kakao and kakao_ready():
    if previous_key is None:
        state[etf_name] = current_key
        save_signal_state(state)
        st.caption("자동알림 기준 신호를 저장했습니다. 다음부터 신호가 바뀔 때만 카카오톡을 보냅니다.")
    elif previous_key != current_key:
        ok, msg = send_kakao_message("🔔 ETF 실전 신호 변경\n\n" + signal_message(etf_name, sig))
        if ok:
            state[etf_name] = current_key
            save_signal_state(state)
            st.success("카카오톡으로 신호 변경 알림을 보냈습니다.")
        else:
            st.warning(f"카카오 자동알림 전송 실패: {msg}")

st.markdown("### 판단 기준")
criteria = pd.DataFrame([
    {"조건": "장기 추세", "현재": "충족" if sig['price'] > sig['ma160'] else "미충족", "기준": "현재가 > 160일선"},
    {"조건": "추세 정렬", "현재": "충족" if sig['ma40'] > sig['ma160'] else "미충족", "기준": "40일선 > 160일선"},
    {"조건": "단기 추세", "현재": "상승" if sig['slope40'] > 0 else "하락/정체", "기준": "40일선 기울기 > 0"},
    {"조건": "40일선 재회복", "현재": "발생" if sig['reclaim40'] else "없음", "기준": "전일 40일선 이하 → 오늘 40일선 위"},
    {"조건": "160일선 이탈", "현재": "발생" if sig['break160'] else "없음", "기준": "전일 160일선 이상 → 오늘 160일선 아래"},
    {"조건": "장기선 방향", "현재": "상승" if sig['slope160'] >= 0 else "하락", "기준": "160일선 기울기"},
])
st.dataframe(criteria, use_container_width=True, hide_index=True)

st.markdown("## 📌 신규매수 실행안")
base_weight = suggested_weight(sig["buy_stage"])
manual = st.checkbox("신규매수 추천 비중 직접 조정")
if manual:
    weight_pct = st.slider("ETF 신규매수 목표비중(%)", 0, 100, int(base_weight * 100), 5)
    weight = weight_pct / 100
else:
    weight = base_weight
    weight_pct = int(round(weight * 100))

amount = capital * weight
shares = math.floor(amount / sig["price"]) if sig["price"] > 0 else 0
actual_amount = shares * sig["price"]
cash = capital - actual_amount

a, b, c, d = st.columns(4)
a.metric("총 운용자금", f"{capital:,.0f}원")
b.metric("신규매수 목표비중", f"{weight_pct}%")
c.metric("매수 가능 수량", f"{shares:,}주")
d.metric("매수 후 예상 현금", f"{cash:,.0f}원")

if sig["buy_stage"] == 1:
    st.success(f"1차 진입안: {etf_name} 약 **{shares:,}주**, 약 **{actual_amount:,.0f}원** 매수. 남은 자금은 40일선 눌림 후 재회복 신호를 기다립니다.")
elif sig["buy_stage"] == 2:
    st.success(f"추가매수안: 누적 목표비중 약 **{weight_pct}%**. 현재 가격 기준 총 목표 규모는 약 **{shares:,}주 / {actual_amount:,.0f}원**입니다.")
else:
    st.warning("오늘은 신규매수 신호가 아닙니다. 현금을 유지하고 다음 추세 신호를 기다립니다.")

st.markdown("## 💼 기존 보유자 실행안")
hold_target = capital * sig["hold_weight"]
hold_shares = math.floor(hold_target / sig["price"]) if sig["price"] > 0 else 0
h1, h2, h3 = st.columns(3)
h1.metric("보유 권장비중", f"{sig['hold_weight']*100:.0f}%")
h2.metric("권장 ETF 금액", f"{hold_target:,.0f}원")
h3.metric("현재가 기준 참고수량", f"{hold_shares:,}주")

if sig["hold_weight"] == 1.0:
    st.success("기존 보유자는 매도하지 않고 유지합니다. 40일선 이탈만으로는 매도하지 않습니다.")
elif sig["hold_weight"] == 0.5:
    st.warning("160일선 이탈 방어 단계입니다. 기존 보유분의 약 절반 수준으로 비중축소를 검토합니다.")
else:
    st.error("장기 추세 하락 단계입니다. 기존 보유분 대부분을 현금화하는 신호입니다.")

st.markdown("## 🧭 실전 행동 규칙")
plan = pd.DataFrame([
    {"대상": "신규매수", "신호": "🟢 1차 매수", "조건": "현재가 > 160일선 + 40일선 > 160일선 + 40일선 상승", "행동": "목표 50% 진입"},
    {"대상": "신규/기존", "신호": "🟢 추가매수", "조건": "눌림 후 40일선 재회복 + 추세정렬", "행동": "누적 80%까지 확대"},
    {"대상": "기존 보유", "신호": "🟢 보유", "조건": "현재가 > 160일선, 단기 조정", "행동": "매도하지 않음"},
    {"대상": "기존 보유", "신호": "🟠 비중축소", "조건": "160일선 하향 이탈", "행동": "약 50%까지 축소"},
    {"대상": "기존 보유", "신호": "🔴 매도/현금화", "조건": "현재가 < 160일선 + 160일선 기울기 하락", "행동": "대부분 현금화"},
])
st.dataframe(plan, use_container_width=True, hide_index=True)

st.markdown("### 📈 최근 가격과 이동평균")
chart = pd.DataFrame(index=h.index)
chart[etf_name] = h["Close"]
chart["40일선"] = h["Close"].rolling(40).mean()
chart["160일선"] = h["Close"].rolling(160).mean()
st.line_chart(chart.tail(220))

st.divider()
st.caption("이 페이지는 고정 익절·손절률을 사용하지 않습니다. 매수·보유·비중축소·매도를 모두 40일선/160일선 추세로 판단합니다.")
st.caption("카카오 자동알림은 앱이 실행되어 신호를 계산할 때 작동합니다. 백그라운드 상시감시는 별도의 스케줄러가 필요합니다.")
st.caption("실제 주문은 자동 전송하지 않으며, 체결가는 증권사 호가와 다를 수 있습니다.")
st.caption(f"계산시각: {datetime.now(SEOUL).strftime('%Y-%m-%d %H:%M:%S KST')}")