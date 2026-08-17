import base64
import json
import os
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

REPO = "EGGPAPA/HY-DYNAMIC12-KOREA"
BRANCH = "main"
HOLDINGS_PATH = "holdings.json"
API_URL = f"https://api.github.com/repos/{REPO}/contents/{HOLDINGS_PATH}"

st.set_page_config(page_title="HY DYNAMIC12 KOREA 보유종목", page_icon="💼", layout="wide")
st.title("💼 한국 보유종목 관리")
st.caption("실제 체결 매수가와 수량을 직접 입력합니다. 추가 매수 시 평균매수가를 자동 계산합니다.")


def github_pat():
    try:
        v = st.secrets.get("GITHUB_PAT", "")
        if v:
            return str(v).strip()
    except Exception:
        pass
    return os.getenv("GITHUB_PAT", "").strip()


def headers():
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if github_pat():
        h["Authorization"] = f"Bearer {github_pat()}"
    return h


def load_holdings():
    r = requests.get(API_URL, headers=headers(), params={"ref": BRANCH}, timeout=20)
    if r.status_code == 404:
        return [], None
    if r.status_code != 200:
        raise RuntimeError(f"holdings.json 읽기 실패: HTTP {r.status_code} / {r.text[:250]}")
    data = r.json()
    raw = base64.b64decode(data["content"]).decode("utf-8")
    rows = json.loads(raw or "[]")
    if not isinstance(rows, list):
        raise ValueError("holdings.json은 JSON 배열이어야 합니다.")
    return rows, data.get("sha")


def save_holdings(rows, sha, message):
    if not github_pat():
        raise RuntimeError("Streamlit Secrets에 GITHUB_PAT를 등록하세요.")
    text = json.dumps(rows, ensure_ascii=False, indent=2)
    payload = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(API_URL, headers=headers(), json=payload, timeout=20)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"holdings.json 저장 실패: HTTP {r.status_code} / {r.text[:250]}")


def find_active(rows, code):
    c = code.strip().zfill(6)
    for i, row in enumerate(rows):
        if str(row.get("ticker", "")).zfill(6) == c and str(row.get("status", "holding")).lower() != "closed":
            return i, row
    return None, None


try:
    holdings, holdings_sha = load_holdings()
except Exception as e:
    st.error(str(e))
    holdings, holdings_sha = [], None

active = [x for x in holdings if str(x.get("status", "holding")).lower() != "closed" and x.get("enabled", True)]

c1, c2, c3 = st.columns(3)
c1.metric("보유종목", len(active))
c2.metric("GitHub 저장", "준비됨" if github_pat() else "PAT 미설정")
c3.metric("카카오 감시", "연결 준비")

if active:
    view = []
    for r in active:
        avg = float(r.get("average_price", 0) or 0)
        qty = float(r.get("quantity", 0) or 0)
        view.append({
            "종목코드": str(r.get("ticker", "")).zfill(6),
            "종목명": r.get("name", ""),
            "시장": r.get("market", "KOSPI"),
            "평균매수가(원)": round(avg),
            "수량": qty,
            "투입금액(원)": round(avg * qty),
            "손절(-3%)": round(avg * 0.97),
            "+15%": round(avg * 1.15),
            "+20%": round(avg * 1.20),
            "+25%": round(avg * 1.25),
        })
    st.dataframe(pd.DataFrame(view), use_container_width=True, hide_index=True)
else:
    st.info("현재 등록된 보유종목이 없습니다.")

st.divider()
st.subheader("매수 등록 / 추가 매수")

with st.form("kr_buy_form", clear_on_submit=False):
    a, b, c = st.columns([1, 2, 1])
    code = a.text_input("종목코드", placeholder="예: 005930").strip()
    name = b.text_input("종목명", placeholder="예: 삼성전자").strip()
    market = c.selectbox("시장", ["KOSPI", "KOSDAQ"])
    d, e = st.columns(2)
    buy_price = d.number_input("실제 체결 매수가(원)", min_value=0.0, step=100.0, format="%.0f")
    buy_qty = e.number_input("매수 수량", min_value=0.0, step=1.0, format="%.4f")
    submitted = st.form_submit_button("➕ 보유 등록 / 추가 매수", type="primary", use_container_width=True)

if submitted:
    if not code or buy_price <= 0 or buy_qty <= 0:
        st.error("종목코드, 실제 체결 매수가, 수량을 정확히 입력하세요.")
    else:
        try:
            code = code.zfill(6)
            holdings, holdings_sha = load_holdings()
            idx, old = find_active(holdings, code)
            now = datetime.now(timezone.utc).isoformat()

            if old is None:
                new_avg = float(buy_price)
                new_qty = float(buy_qty)
                holdings.append({
                    "ticker": code,
                    "name": name or code,
                    "market": market,
                    "mode": "holding",
                    "status": "holding",
                    "average_price": round(new_avg, 4),
                    "quantity": round(new_qty, 6),
                    "stop_loss_pct": 3,
                    "enabled": True,
                    "updated_at": now,
                })
                msg = f"Add Korea holding {code}"
            else:
                old_avg = float(old.get("average_price", 0) or 0)
                old_qty = float(old.get("quantity", 0) or 0)
                new_qty = old_qty + float(buy_qty)
                new_avg = ((old_avg * old_qty) + (float(buy_price) * float(buy_qty))) / new_qty
                old.update({
                    "name": name or old.get("name") or code,
                    "market": market or old.get("market", "KOSPI"),
                    "mode": "holding",
                    "status": "holding",
                    "average_price": round(new_avg, 4),
                    "quantity": round(new_qty, 6),
                    "stop_loss_pct": float(old.get("stop_loss_pct", 3) or 3),
                    "enabled": True,
                    "updated_at": now,
                })
                holdings[idx] = old
                msg = f"Update Korea holding {code}"

            save_holdings(holdings, holdings_sha, msg)
            st.success(f"{name or code} 저장 완료 · 평균매수가 {new_avg:,.0f}원 · 총수량 {new_qty:g}")
            st.rerun()
        except Exception as e:
            st.error(str(e))

st.divider()
st.subheader("전량 매도 처리")
active_codes = [str(x.get("ticker", "")).zfill(6) for x in active if x.get("ticker")]
if active_codes:
    close_code = st.selectbox("전량 매도할 종목", active_codes)
    if st.button("✅ 전량 매도 → 감시 종료", use_container_width=True):
        try:
            holdings, holdings_sha = load_holdings()
            idx, old = find_active(holdings, close_code)
            if old is None:
                st.warning("해당 보유종목을 찾지 못했습니다.")
            else:
                old["status"] = "closed"
                old["enabled"] = False
                old["closed_at"] = datetime.now(timezone.utc).isoformat()
                holdings[idx] = old
                save_holdings(holdings, holdings_sha, f"Close Korea holding {close_code}")
                st.success(f"{close_code} 전량 매도 처리 완료 · 자동감시 종료")
                st.rerun()
        except Exception as e:
            st.error(str(e))
else:
    st.caption("전량 매도 처리할 보유종목이 없습니다.")

st.info("보유종목은 TOP12에서 빠져도 holdings.json에 남습니다. 평균매수가 기준 -3%, +15%, +20%, +25% 카카오 감시와 연결할 수 있습니다.")
