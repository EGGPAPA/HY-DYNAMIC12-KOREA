"""Screen-independent holdings reference alerts. No order API is used."""
import json
import math
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

KST = ZoneInfo("Asia/Seoul")
STATE = Path("data/holding_price_alert_state.json")
APP_URL = "https://hy-dynamic12-korea-nfxvcb3ntgddwdeydldbsb.streamlit.app/"
LEVELS = (("stop", -.03, "손절 참고(-3%)"), ("reference5", .05, "참고(+5%)"),
          ("take10", .10, "1차(+10%)"), ("take15", .15, "2차(+15%)"), ("take20", .20, "3차(+20%)"))

def positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None

def market_window(now):
    return now.weekday() < 5 and time(9) <= now.time().replace(tzinfo=None) <= time(15, 40)

def pending(row, price, sent):
    avg = positive(row.get("average_price"))
    if (str(row.get("status", "holding")).lower() == "closed" or not row.get("enabled", True)
            or not positive(row.get("quantity")) or not avg or not positive(price)):
        return []
    code = str(row.get("ticker", "")).zfill(6)
    result = []
    for key, rate, label in LEVELS:
        level = avg * (1 + rate)
        reached = price <= level if rate < 0 else price >= level
        event_id = code + ":" + key
        if reached and event_id not in sent:
            result.append({"id": event_id, "code": code, "name": row.get("name") or code,
                           "label": label, "price": price, "average": avg, "level": level,
                           "return": (price / avg - 1) * 100})
    return result

def fresh_price(row, now):
    code = str(row.get("ticker", "")).zfill(6)
    suffix = "KQ" if "KOSDAQ" in str(row.get("market", "")).upper() else "KS"
    frame = yf.Ticker(code + "." + suffix).history(period="1d", interval="5m", auto_adjust=False)
    if frame is None or frame.empty:
        return None
    close = frame["Close"].dropna()
    if close.empty:
        return None
    stamp = close.index[-1].to_pydatetime()
    if stamp.tzinfo is None:
        return None
    stamp = stamp.astimezone(KST)
    if stamp.date() != now.date() or not timedelta(0) <= now - stamp <= timedelta(minutes=20):
        return None
    return positive(close.iloc[-1])

def send_kakao(text):
    required = ("KAKAO_REST_API_KEY", "KAKAO_REFRESH_TOKEN")
    if any(not os.getenv(name) for name in required):
        raise RuntimeError("Kakao credentials missing")
    data = {"grant_type": "refresh_token", "client_id": os.environ[required[0]],
            "refresh_token": os.environ[required[1]]}
    if os.getenv("KAKAO_CLIENT_SECRET"):
        data["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
    response = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=20)
    if not response.ok:
        raise RuntimeError("Kakao authentication failed: HTTP " + str(response.status_code))
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Kakao access token missing")
    template = {"object_type": "text", "text": text,
                "link": {"web_url": APP_URL, "mobile_web_url": APP_URL}, "button_title": "보유종목 확인"}
    response = requests.post("https://kapi.kakao.com/v2/api/talk/memo/default/send",
                             headers={"Authorization": "Bearer " + token},
                             data={"template_object": json.dumps(template, ensure_ascii=False)}, timeout=20)
    if not response.ok or response.json().get("result_code") != 0:
        raise RuntimeError("Kakao delivery failed: HTTP " + str(response.status_code))

def main():
    if os.getenv("HOLDING_TEST") == "true":
        send_kakao("🔔 보유종목 서버 알림 연결 확인\n화면·PC를 꺼도 서버에서 확인합니다.\n-3%, +5%, +10%, +15%, +20% 참고선\n평일 정규장 약 10분 간격 · Yahoo 5분봉 · 지연 가능\n※ 참고용 알림이며 자동 주문은 없습니다.")
        return
    now = datetime.now(KST)
    if not market_window(now):
        print("Outside market window")
        return
    rows = json.loads(Path("holdings.json").read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    today = now.strftime("%Y-%m-%d")
    sent = set(state.get("sent", [])) if state.get("date") == today else set()
    errors = 0
    for row in rows:
        if (
                not positive(row.get("quantity")) or not positive(row.get("average_price"))
                or str(row.get("status", "holding")).lower() == "closed" or not row.get("enabled", True)):
            continue
        try:
            price = fresh_price(row, now)
            if price is None:
                errors += 1
                continue
            for item in pending(row, price, sent):
                send_kakao(
                    f"🔔 보유종목 {item['label']} 도달\n{item['name']} ({item['code']})\n"
                    f"현재가 {price:,.0f}원 / 기준가 {item['level']:,.0f}원\n"
                    f"평균매수가 {item['average']:,.0f}원 / 수익률 {item['return']:+.2f}%\n"
                    f"{now:%Y-%m-%d %H:%M KST} · Yahoo 5분봉\n"
                    "※ 수수료·세금 전 참고용 · 지연 가능 · 자동 주문 없음")
                sent.add(item["id"])
                STATE.parent.mkdir(parents=True, exist_ok=True)
                STATE.write_text(json.dumps({"date": today, "sent": sorted(sent)}, indent=2), encoding="utf-8")
        except Exception as exc:
            print("Holding check failed:", type(exc).__name__)
            errors += 1
    if errors:
        raise RuntimeError(f"{errors} holdings could not be checked or notified")

if __name__ == "__main__":
    main()
