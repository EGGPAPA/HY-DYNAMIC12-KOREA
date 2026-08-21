"""Background monitor for first-entry conditions of strong sector leaders."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf


SEOUL = ZoneInfo("Asia/Seoul")
STATE_FILE = Path("data/leader_alert_state.json")
APP_URL = "https://hy-dynamic12-korea-nfxvcb3ntgddwdeydldbsb.streamlit.app/"
LEADERS = {
    "반도체": {"005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "042700.KS": "한미반도체"},
    "자동차": {"005380.KS": "현대차", "000270.KS": "기아", "012330.KS": "현대모비스"},
    "금융": {"105560.KS": "KB금융", "055550.KS": "신한지주", "086790.KS": "하나금융지주"},
    "헬스케어": {"207940.KS": "삼성바이오로직스", "068270.KS": "셀트리온", "196170.KQ": "알테오젠"},
    "2차전지": {"373220.KS": "LG에너지솔루션", "006400.KS": "삼성SDI", "247540.KQ": "에코프로비엠"},
}


def in_market_window(now: datetime) -> bool:
    if os.getenv("FORCE_RUN", "").lower() in {"1", "true", "yes"}:
        return True
    return now.weekday() < 5 and time(9, 0) <= now.time() <= time(15, 30)


def analyze(symbol: str, name: str, sector: str) -> dict | None:
    try:
        frame = yf.Ticker(symbol).history(period="6mo", interval="1d", auto_adjust=True)
        if frame is None or frame.empty:
            return None
        close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
        volume = pd.to_numeric(frame["Volume"], errors="coerce").dropna()
        if len(close) < 61 or len(volume) < 20:
            return None
        price = float(close.iloc[-1])
        ma20 = float(close.tail(20).mean())
        ma60 = float(close.tail(60).mean())
        ret20 = (price / float(close.iloc[-21]) - 1) * 100
        gap20 = (price / ma20 - 1) * 100
        volume_ratio = float(volume.tail(5).mean() / volume.tail(20).mean())
        first_watch = ma20 * 1.02
        invalidation = ma60 * .97
        conditions = {
            "관찰가 ±2%": abs(price / first_watch - 1) <= .02,
            "20일선 위": price >= ma20,
            "거래량 ≥0.7배": volume_ratio >= .70,
            "무효선 위": price > invalidation,
        }
        strong = price > ma20 > ma60 and ret20 >= 5
        ready = strong and all(conditions.values())
        return {
            "symbol": symbol, "code": symbol.split(".")[0], "name": name,
            "sector": sector, "price": price, "ma20": ma20, "ma60": ma60,
            "first_watch": first_watch, "invalidation": invalidation,
            "gap20": gap20, "volume_ratio": volume_ratio,
            "strong": strong, "ready": ready, "conditions": conditions,
        }
    except Exception as exc:
        print(f"WARN {symbol}: {type(exc).__name__}", file=sys.stderr)
        return None


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def access_token() -> str | None:
    required = ["KAKAO_REST_API_KEY", "KAKAO_CLIENT_SECRET", "KAKAO_REFRESH_TOKEN"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing GitHub Actions secrets: " + ", ".join(missing))
    response = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": os.environ["KAKAO_REST_API_KEY"],
            "client_secret": os.environ["KAKAO_CLIENT_SECRET"],
            "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"],
        },
        timeout=15,
    )
    data = response.json()
    if not response.ok or not data.get("access_token"):
        raise RuntimeError(f"Kakao token error: HTTP {response.status_code}")
    return data["access_token"]


def message(rows: list[dict], now: datetime) -> str:
    lines = ["[HY DYNAMIC12 자동감시]", "🚨 1차 분할매수 조건 신규충족", now.strftime("%Y-%m-%d %H:%M KST")]
    for row in rows:
        lines.extend([
            "", f"{row['name']} ({row['sector']})",
            f"현재가 {row['price']:,.0f}원 / 1차 관찰가 {row['first_watch']:,.0f}원",
            f"20일선 대비 {row['gap20']:+.1f}% / 거래량 {row['volume_ratio']:.2f}배",
            f"추세 무효선 {row['invalidation']:,.0f}원",
        ])
    lines.append("\n조건 확인 알림이며 실제 주문은 직접 판단하세요.")
    return "\n".join(lines)


def send_kakao(text: str) -> None:
    token = access_token()
    link = os.getenv("KAKAO_REDIRECT_URI", APP_URL)
    template = {
        "object_type": "text", "text": text,
        "link": {"web_url": link, "mobile_web_url": link},
        "button_title": "주도주 확인",
    }
    response = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=15,
    )
    data = response.json()
    if not response.ok or data.get("result_code") != 0:
        raise RuntimeError(f"Kakao send error: HTTP {response.status_code}")


def main() -> int:
    now = datetime.now(SEOUL)
    if not in_market_window(now):
        print("Outside Korean regular market hours; no scan.")
        return 0
    rows = []
    for sector, members in LEADERS.items():
        for symbol, name in members.items():
            result = analyze(symbol, name, sector)
            if result:
                rows.append(result)
    ready = [row for row in rows if row["ready"]]
    state = load_state()
    today = now.strftime("%Y-%m-%d")
    sent_today = set(state.get("sent", {}).get(today, []))
    newly_ready = [row for row in ready if row["code"] not in sent_today]
    print(f"Scanned {len(rows)} leaders; ready={len(ready)}; new={len(newly_ready)}")
    if not newly_ready:
        return 0
    send_kakao(message(newly_ready, now))
    sent_today.update(row["code"] for row in newly_ready)
    state = {"sent": {today: sorted(sent_today)}, "updated_at": now.isoformat()}
    save_state(state)
    print("Kakao alert sent for: " + ", ".join(row["name"] for row in newly_ready))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

