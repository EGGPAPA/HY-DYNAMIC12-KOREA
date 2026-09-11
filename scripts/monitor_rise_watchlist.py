"""15-minute background monitor for the personal rise-timing watchlist."""
import base64
import json
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

SEOUL = ZoneInfo("Asia/Seoul")
REPO = "EGGPAPA/HY-DYNAMIC12-KOREA"
STATE_PATH = "data/rise_timing_live.json"
STATE_BRANCH = "monitor-state"
WATCH_FILE = Path("rise_timing_watchlist.json")
API = f"https://api.github.com/repos/{REPO}/contents/{STATE_PATH}"
APP_URL = "https://hy-dynamic12-korea-nfxvcb3ntgddwdeydldbsb.streamlit.app/"


def market_open(now):
    return now.weekday() < 5 and time(9, 0) <= now.time() <= time(15, 30)


def symbol(row):
    return f"{str(row['ticker']).zfill(6)}.{'KQ' if str(row.get('market','KOSPI')).upper() == 'KOSDAQ' else 'KS'}"


def analyze(row):
    frame = yf.Ticker(symbol(row)).history(period="1y", interval="1d", auto_adjust=False)
    if frame is None or len(frame) < 65:
        return None
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    volume = pd.to_numeric(frame["Volume"], errors="coerce").reindex(close.index)
    if len(close) < 65:
        return None
    valid_volume = volume.dropna()
    latest_date = pd.Timestamp(close.index[-1]).tz_localize(None).normalize()
    today = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None).normalize()
    if valid_volume.empty or float(valid_volume.iloc[-1]) <= 0 or (today - latest_date).days > 7:
        return None
    daily_price = float(close.iloc[-1])
    try:
        live = yf.Ticker(symbol(row)).fast_info.get("last_price")
        price = float(live) if live and float(live) > 0 else daily_price
    except Exception:
        price = daily_price
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    m20, m60 = float(ma20.iloc[-1]), float(ma60.iloc[-1])
    prior_high = float(close.iloc[-21:-1].max())
    gap20 = (daily_price / m20 - 1) * 100
    ret10 = (daily_price / float(close.iloc[-11]) - 1) * 100
    ret20 = (daily_price / float(close.iloc[-21]) - 1) * 100
    rising20 = m20 > float(ma20.iloc[-6])
    cross = (ma20 > ma60) & (ma20.shift(1) <= ma60.shift(1))
    recent_cross = bool(cross.tail(10).fillna(False).any())
    breakout = daily_price >= prior_high and float(close.iloc[-2]) < prior_high
    base_volume = volume.iloc[-21:-1].dropna()
    recent_volume = volume.tail(3).dropna()
    volume_ratio = float(recent_volume.max() / base_volume.mean()) if len(base_volume) and base_volume.mean() > 0 else 1.0
    score = (20 if daily_price > m20 else 0) + (15 if rising20 else 0) + (15 if daily_price > m60 else 0)
    score += 15 if recent_cross else 0
    score += 20 if breakout else (8 if daily_price >= prior_high * .98 else 0)
    score += 15 if volume_ratio >= 1.5 else (8 if volume_ratio >= 1.1 else 0)
    if gap20 > 12: score -= 20
    if ret10 > 25: score -= 15
    score = max(0, min(100, round(score, 1)))
    late = gap20 > 15 or ret20 > 35 or ret10 > 25
    label = "🔴 급등·추격금지"
    if not late and score >= 75 and gap20 <= 8: label = "🟢 상승초입"
    elif not late and score >= 60: label = "🟡 돌파확인"
    elif not late and daily_price > m60 and rising20: label = "🔵 준비구간"
    buy1 = min(daily_price, max(m20, prior_high * .995)) if label.startswith("🟢") else max(m20, prior_high * .99)
    low10 = float(close.tail(10).min())
    stop = min(daily_price * .97, max(m20 * .96, low10 * .98))
    if label.startswith("🟢") and daily_price > stop and buy1 * .98 <= daily_price <= buy1 * 1.02:
        label = "🟣 1차매수구간"
    gap = (price / buy1 - 1) * 100 if buy1 else 999
    risk = (price - stop) / price * 100 if price > 0 else 999
    checks = {
        "stage": "1차매수구간" in label,
        "gap": -1 <= gap <= 3,
        "volume": volume_ratio >= 1.5,
        "score": score >= 85,
        "close": daily_price >= prior_high,
        "risk": 0 <= risk <= 7,
    }
    return {
        "ticker": str(row["ticker"]).zfill(6), "name": row.get("name") or row["ticker"],
        "market": row.get("market", "KOSPI"), "price": round(price), "daily_price": round(daily_price),
        "label": label, "score": score, "volume_ratio": round(volume_ratio, 2),
        "buy1": round(buy1), "stop": round(stop), "breakout": round(prior_high),
        "gap": round(gap, 2), "risk": round(risk, 2), "checks": checks,
    }


def api_headers():
    return {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"}


def load_state():
    response = requests.get(API, headers=api_headers(), params={"ref": STATE_BRANCH}, timeout=15)
    if response.status_code != 200: return {"items": [], "history": {}}, None
    data = response.json()
    return json.loads(base64.b64decode(data["content"])), data["sha"]


def save_state(state, sha):
    payload = {"message": "Update 15-minute rise timing state", "branch": STATE_BRANCH,
               "content": base64.b64encode(json.dumps(state, ensure_ascii=False, indent=2).encode()).decode()}
    if sha: payload["sha"] = sha
    response = requests.put(API, headers=api_headers(), json=payload, timeout=20)
    response.raise_for_status()


def send_kakao(items):
    required = ["KAKAO_REST_API_KEY", "KAKAO_CLIENT_SECRET", "KAKAO_REFRESH_TOKEN"]
    if any(not os.getenv(key) for key in required): return
    token_response = requests.post("https://kauth.kakao.com/oauth/token", data={
        "grant_type": "refresh_token", "client_id": os.environ["KAKAO_REST_API_KEY"],
        "client_secret": os.environ["KAKAO_CLIENT_SECRET"], "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"],
    }, timeout=15)
    token = token_response.json().get("access_token")
    if not token: return
    lines = ["[HY DYNAMIC12 상승시점 알림]", "필수조건을 통과한 신규 매수검토 종목"]
    for x in items:
        lines.append(f"{x['name']}({x['ticker']}) 현재 {x['price']:,}원 / 1차 {x['buy1']:,}원 / {x['score']:.0f}점")
    text = "\n".join(lines)
    template = {"object_type": "text", "text": text, "link": {"web_url": APP_URL, "mobile_web_url": APP_URL}, "button_title": "상승시점 확인"}
    requests.post("https://kapi.kakao.com/v2/api/talk/memo/default/send",
                  headers={"Authorization": f"Bearer {token}"}, data={"template_object": json.dumps(template, ensure_ascii=False)}, timeout=15)


def main():
    now = datetime.now(SEOUL)
    if not market_open(now) and os.getenv("FORCE_RUN", "").lower() not in {"1", "true"}:
        print("Outside Korean market hours")
        return
    rows = json.loads(WATCH_FILE.read_text(encoding="utf-8"))
    previous, sha = load_state()
    history = previous.get("history", {})
    prior_actionable = {x["ticker"] for x in previous.get("items", []) if str(x.get("action", "")).startswith("🟢") or "5/7 충족" in str(x.get("action", ""))}
    items = []
    for row in rows:
        try: item = analyze(row)
        except Exception as exc:
            print(f"WARN {row.get('ticker')}: {type(exc).__name__}")
            continue
        if not item: continue
        code = item["ticker"]
        stable = item["checks"]["gap"] and item["checks"]["close"] and item["checks"]["risk"]
        h = history.setdefault(code, {"consecutive": 0, "dates": []})
        h["consecutive"] = int(h.get("consecutive", 0)) + 1 if stable else 0
        today = now.strftime("%Y-%m-%d")
        dates = list(h.get("dates", []))
        if stable and today not in dates: dates.append(today)
        h["dates"] = dates[-10:]
        persistent = h["consecutive"] >= 2 or len(h["dates"]) >= 2
        item["checks"]["persistence"] = persistent
        passed = sum(item["checks"].values())
        mandatory = item["checks"]["gap"] and item["checks"]["volume"] and item["checks"]["close"] and item["checks"]["risk"]
        if item["gap"] > 7 or (item["gap"] > 0 and item["volume_ratio"] < 1.0):
            item["action"] = f"🔴 추격 금지 · 조건 {passed}/7"
        elif not item["checks"]["close"]:
            item["action"] = f"🔴 종가 돌파 실패 · 매수 제외 ({passed}/7)"
        elif not item["checks"]["risk"]:
            item["action"] = f"🔴 손절 위험 {item['risk']:.1f}% · 매수 제외"
        elif item["gap"] > 3:
            item["action"] = f"🟠 눌림 대기 · {item['gap']:+.1f}%"
        elif not item["checks"]["volume"]:
            item["action"] = f"🟠 거래량 확인 대기 · 조건 {passed}/7"
        elif mandatory and passed == 7 and item["score"] >= 90:
            item["action"] = "🟢 7/7 즉시 검토 · 1차 분할매수"
        elif mandatory and passed == 7:
            item["action"] = "🟢 7/7 충족 · 1차 분할매수 검토"
        elif mandatory and passed == 6:
            item["action"] = "🟢 6/7 충족 · 소규모 1차 매수"
        elif mandatory and passed == 5:
            item["action"] = "🟡 5/7 충족 · 계획의 10~20% 시험매수"
        else:
            item["action"] = f"🔵 관찰 유지 · 조건 {passed}/7"
        items.append(item)
    state = {"updated_at": now.isoformat(), "interval_minutes": 15, "items": items, "history": history}
    actionable = [x for x in items if x["action"].startswith("🟢") or "5/7 충족" in x["action"]]
    new_actionable = [x for x in actionable if x["ticker"] not in prior_actionable]
    if new_actionable: send_kakao(new_actionable)
    save_state(state, sha)
    print(f"Updated {len(items)} watch items; new actionable alerts={len(new_actionable)}")


if __name__ == "__main__":
    main()
