import json
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

CODE = "000660"
SYMBOL = "000660.KS"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
STATE_FILE = Path("price_alert_state.json")
HOLDINGS_FILE = Path("holdings.json")
KST = ZoneInfo("Asia/Seoul")


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
    if not r.ok:
        # 토큰/키 자체는 절대 출력하지 않고 카카오의 오류 코드/메시지만 남김
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:300]
        raise RuntimeError(f"카카오 토큰 갱신 실패 HTTP {r.status_code}: {detail}")
    payload = r.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"카카오 access_token 없음: {payload}")
    return token


def send_kakao(text):
    token = refresh_kakao_token()
    template = {
        "object_type": "text",
        "text": text,
        "link": {
            "web_url": "https://eggpapa-hy-dynamic12-usa-app-s7ppvp.streamlit.app",
            "mobile_web_url": "https://eggpapa-hy-dynamic12-usa-app-s7ppvp.streamlit.app",
        },
        "button_title": "실전운용 보기",
    }
    r = requests.post(
        KAKAO_MEMO_URL,
        headers={"Authorization": f"Bearer {token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=20,
    )
    if not r.ok:
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:300]
        raise RuntimeError(f"카카오 메시지 전송 실패 HTTP {r.status_code}: {detail}")


def is_korea_market_time():
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return False, now
    return time(9, 0) <= now.time() <= time(15, 30), now


def current_price():
    # 장중 알림용: 5분봉의 마지막 체결가를 우선 사용
    h = yf.download(SYMBOL, period="1d", interval="5m", auto_adjust=False, progress=False, threads=False)
    if h is not None and not h.empty:
        close = h["Close"]
        if getattr(close, "ndim", 1) > 1:
            close = close.iloc[:, 0]
        close = close.dropna()
        if not close.empty:
            return float(close.iloc[-1]), "Yahoo 5m"
    # 폴백: 최근 일봉 종가
    h = yf.download(SYMBOL, period="5d", interval="1d", auto_adjust=False, progress=False, threads=False)
    if h is None or h.empty:
        raise RuntimeError("SK하이닉스 현재가 조회 실패")
    close = h["Close"]
    if getattr(close, "ndim", 1) > 1:
        close = close.iloc[:, 0]
    return float(close.dropna().iloc[-1]), "Yahoo daily fallback"


def daily_history():
    h = yf.download(SYMBOL, period="1y", interval="1d", auto_adjust=True, progress=False, threads=False)
    if h is None or h.empty:
        raise RuntimeError("SK하이닉스 일봉 조회 실패")
    close = h["Close"]
    if getattr(close, "ndim", 1) > 1:
        close = close.iloc[:, 0]
    return close.dropna()


def moving_levels(s, p):
    m40 = float(s.tail(40).mean())
    m60 = float(s.tail(60).mean())
    m160 = float(s.tail(160).mean())
    return {
        "first": min(m40 * 0.97, p * 1.02),
        "second": max(m160 * 1.05, min(m60 * 0.97, p * 0.94)),
        "recovery": m40 * 1.005,
        "risk": m160 * 0.97,
        "ma40": m40,
        "ma160": m160,
    }


def holding_average():
    try:
        rows = json.loads(HOLDINGS_FILE.read_text(encoding="utf-8"))
        for row in rows:
            if str(row.get("ticker", "")).zfill(6) == CODE and str(row.get("status", "holding")).lower() != "closed":
                avg = float(row.get("average_price", 0) or 0)
                if avg > 0:
                    return avg
    except Exception:
        pass
    return None


def fixed_levels(avg):
    if not avg:
        return {}
    return {
        "stop": avg * 0.97,
        "take15": avg * 1.15,
        "take20": avg * 1.20,
        "take25": avg * 1.25,
    }


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def crossed_down(old, now, level):
    return old > level >= now


def crossed_up(old, now, level):
    return old < level <= now


def main():
    force_test = env("KAKAO_FORCE_TEST").lower() in {"1", "true", "yes"}
    if force_test:
        send_kakao("🔔 HY DYNAMIC12 자동알림 테스트\n\nSK하이닉스 한국주식 가격감시 시스템 정상 연결\nGitHub Actions → KakaoTalk 전송 성공\n\n※ 테스트 메시지입니다.")
        print("Kakao forced test message sent successfully")
        return

    market_open, now = is_korea_market_time()
    print(f"KST {now:%Y-%m-%d %H:%M:%S} / Korea market open={market_open}")
    if not market_open:
        print("한국 정규장 09:00~15:30 KST가 아니므로 종료")
        return

    p, source = current_price()
    s = daily_history()
    dynamic = moving_levels(s, p)
    avg = holding_average()
    fixed = fixed_levels(avg)

    state = load_state()
    previous = float(state.get("price", p))
    events = []

    checks = [
        ("first", "1차 분할매수 참고선", dynamic["first"], crossed_down),
        ("second", "2차 분할매수 참고선", dynamic["second"], crossed_down),
        ("recovery", "40일선 추세회복 확인선", dynamic["recovery"], crossed_up),
        ("risk", "160일선 비중축소 경계선", dynamic["risk"], crossed_down),
    ]
    if fixed:
        checks += [
            ("stop", "평균매수가 -3% 손절 참고선", fixed["stop"], crossed_down),
            ("take15", "평균매수가 +15% 1차 익절선", fixed["take15"], crossed_up),
            ("take20", "평균매수가 +20% 2차 익절선", fixed["take20"], crossed_up),
            ("take25", "평균매수가 +25% 3차 익절선", fixed["take25"], crossed_up),
        ]

    for key, label, level, rule in checks:
        if rule(previous, p, level):
            events.append(f"{label} 도달\n현재가 {p:,.0f}원 / 기준 {level:,.0f}원")

    if events:
        avg_text = f"\n평균매수가 {avg:,.0f}원" if avg else ""
        send_kakao(
            "🔔 SK하이닉스 실전 가격 알림\n\n"
            + "\n\n".join(events)
            + f"\n\n시세원 {source}{avg_text}\n※ 자동감시 참고 알림"
        )
        print(f"Kakao alerts sent: {len(events)}")
    else:
        print(f"No trigger / current={p:,.0f} / previous={previous:,.0f} / source={source}")

    state.update({
        "price": p,
        "source": source,
        "average_price": avg,
        "dynamic_levels": dynamic,
        "fixed_levels": fixed,
        "checked_at": now.isoformat(),
    })
    save_state(state)


if __name__ == "__main__":
    main()
