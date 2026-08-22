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
    "방산·조선": {"012450.KS": "한화에어로스페이스", "042660.KS": "한화오션", "329180.KS": "HD현대중공업"},
    "전력·원전": {"034020.KS": "두산에너빌리티", "010120.KS": "LS ELECTRIC", "052690.KS": "한전기술"},
    "인터넷·게임": {"035420.KS": "NAVER", "035720.KS": "카카오", "259960.KS": "크래프톤"},
    "화학·소재": {"051910.KS": "LG화학", "096770.KS": "SK이노베이션", "005490.KS": "POSCO홀딩스"},
    "소비·유통": {"090430.KS": "아모레퍼시픽", "004170.KS": "신세계", "097950.KS": "CJ제일제당"},
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
        ret60 = (price / float(close.iloc[-61]) - 1) * 100
        gap20 = (price / ma20 - 1) * 100
        volume_ratio = float(volume.tail(5).mean() / volume.tail(20).mean())
        ma20_series = close.rolling(20).mean()
        ma60_series = close.rolling(60).mean()
        leader_flags = (
            (close > ma20_series) & (ma20_series > ma60_series)
            & (close.pct_change(20) * 100 >= 5)
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
        condition_count = sum(conditions.values())
        missing_conditions = [label for label, passed in conditions.items() if not passed]
        return {
            "symbol": symbol, "code": symbol.split(".")[0], "name": name,
            "sector": sector, "price": price, "ma20": ma20, "ma60": ma60,
            "first_watch": first_watch, "invalidation": invalidation,
            "gap20": gap20, "volume_ratio": volume_ratio,
            "ret20": ret20, "ret60": ret60,
            "leader_start": leader_start, "leader_days": leader_days,
            "score": max(0, min(100, 50 + ret20 * 1.2 + ret60 * .35 + min(volume_ratio, 2) * 7)),
            "strong": strong, "ready": ready, "conditions": conditions,
            "condition_count": condition_count,
            "missing_conditions": missing_conditions,
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
    lines = ["[HY DYNAMIC12 자동감시]", "시장환경 조건 단계 상승", now.strftime("%Y-%m-%d %H:%M KST")]
    for row in rows:
        lines.extend([
            "", f"{row['name']} ({row['sector']})",
            f"신호 {row['signal']} / 조건 {row['condition_count']}/4",
            f"부족 조건 {', '.join(row['missing_conditions']) or '없음'}",
            f"최초 포착 {row['leader_start']} / 주도 지속 {row['leader_days']}거래일",
            f"현재가 {row['price']:,.0f}원 / 1차 관찰가 {row['first_watch']:,.0f}원",
            f"20일선 대비 {row['gap20']:+.1f}% / 거래량 {row['volume_ratio']:.2f}배",
            f"추세 무효선 {row['invalidation']:,.0f}원",
        ])
    lines.append("\n앱에서 TOP12 판정과 교차 확인하세요. 자동 주문 또는 투자 권고가 아닙니다.")
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
    sector_rank = []
    for sector in LEADERS:
        members = [row for row in rows if row["sector"] == sector]
        if not members:
            continue
        avg20 = sum(row["ret20"] for row in members) / len(members)
        avg60 = sum(row["ret60"] for row in members) / len(members)
        breadth = sum(row["strong"] for row in members) / len(members) * 100
        sector_score = max(0, min(100, 50 + avg20 * 1.1 + avg60 * .35 + (breadth - 50) * .25))
        sector_rank.append((sector_score, sector))
    leading_sectors = {sector for _, sector in sorted(sector_rank, reverse=True)[:5]}
    representatives = []
    for sector in leading_sectors:
        members = sorted(
            [row for row in rows if row["sector"] == sector],
            key=lambda row: (row["ready"], row["score"]), reverse=True,
        )
        representatives.extend(members[:3])
    signals = []
    for row in representatives:
        if row["ready"]:
            row["signal"] = "🚨 시장환경 4/4 충족"
            row["signal_level"] = 2
            signals.append(row)
        elif row["strong"] and row["condition_count"] >= 3:
            row["signal"] = "🔵 시장환경 3/4 준비"
            row["signal_level"] = 1
            signals.append(row)
    state = load_state()
    today = now.strftime("%Y-%m-%d")
    daily_levels = state.get("market_levels", {}).get(today, {})
    upgraded = [
        row for row in signals
        if row["signal_level"] > int(daily_levels.get(row["code"], 0))
    ]
    print(
        f"Scanned {len(rows)} stocks; leading sectors={len(leading_sectors)}; "
        f"representatives={len(representatives)}; signals={len(signals)}; upgraded={len(upgraded)}"
    )
    if not upgraded:
        return 0
    send_kakao(message(upgraded, now))
    market_levels = state.setdefault("market_levels", {})
    today_levels = market_levels.setdefault(today, {})
    for row in upgraded:
        today_levels[row["code"]] = row["signal_level"]
    state["updated_at"] = now.isoformat()
    save_state(state)
    print("Kakao alert sent for: " + ", ".join(row["name"] for row in upgraded))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


