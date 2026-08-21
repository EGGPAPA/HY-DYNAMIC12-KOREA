import json
import os
from pathlib import Path

import requests
import yfinance as yf

CODE = "000660"
SYMBOL = "000660.KS"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
STATE_FILE = Path("price_alert_state.json")


def env(name):
    return os.getenv(name, "").strip()


def refresh_kakao_token():
    data = {
        "grant_type": "refresh_token",
        "client_id": env("KAKAO_REST_API_KEY"),
        "refresh_token": env("KAKAO_REFRESH_TOKEN"),
    }
    secret = env("KAKAO_CLIENT_SECRET")
    if secret:
        data["client_secret"] = secret
    r = requests.post(KAKAO_TOKEN_URL, data=data, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def send_kakao(text):
    token = refresh_kakao_token()
    template = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": "https://egg-papa-hy-dynamic12-usa-app-s7ppvp.streamlit.app", "mobile_web_url": "https://egg-papa-hy-dynamic12-usa-app-s7ppvp.streamlit.app"},
        "button_title": "실전운용 보기",
    }
    r = requests.post(KAKAO_MEMO_URL, headers={"Authorization": f"Bearer {token}"}, data={"template_object": json.dumps(template, ensure_ascii=False)}, timeout=20)
    r.raise_for_status()


def history():
    h = yf.download(SYMBOL, period="1y", interval="1d", auto_adjust=True, progress=False, threads=False)
    if h is None or h.empty:
        raise RuntimeError("SK하이닉스 가격 조회 실패")
    close = h["Close"]
    if getattr(close, "ndim", 1) > 1:
        close = close.iloc[:, 0]
    return close.dropna()


def levels(s):
    p = float(s.iloc[-1]); m40 = float(s.tail(40).mean()); m60 = float(s.tail(60).mean()); m160 = float(s.tail(160).mean())
    return {
        "price": p,
        "first": min(m40 * 0.97, p * 1.02),
        "second": max(m160 * 1.05, min(m60 * 0.97, p * 0.94)),
        "recovery": m40 * 1.005,
        "risk": m160 * 0.97,
    }


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    lv = levels(history()); p = lv["price"]
    state = load_state(); previous = float(state.get("price", p))
    events = []
    checks = [
        ("first", "1차 분할매수 참고선", lambda old, now, x: old > x >= now),
        ("second", "2차 분할매수 참고선", lambda old, now, x: old > x >= now),
        ("recovery", "추세회복 확인선", lambda old, now, x: old < x <= now),
        ("risk", "비중축소 경계선", lambda old, now, x: old > x >= now),
    ]
    for key, label, crossed in checks:
        x = lv[key]
        if crossed(previous, p, x):
            events.append(f"{label} 도달\n현재가 {p:,.0f}원 / 기준 {x:,.0f}원")
    if events:
        send_kakao("🔔 SK하이닉스 가격 알림\n\n" + "\n\n".join(events) + "\n\n※ 자동 가격감시 참고 알림")
    state.update({"price": p, "levels": lv})
    save_state(state)
    print(f"SK hynix {p:,.0f} / events={len(events)}")


if __name__ == "__main__":
    main()
