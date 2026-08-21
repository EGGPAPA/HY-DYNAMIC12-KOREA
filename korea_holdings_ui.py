import base64
import json
import os
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

REPO = "EGGPAPA/HY-DYNAMIC12-KOREA"
BRANCH = "main"
HOLDINGS_PATH = "holdings.json"
API_URL = f"https://api.github.com/repos/{REPO}/contents/{HOLDINGS_PATH}"


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


def yf_symbol(code, market):
    return f"{str(code).zfill(6)}.{ 'KQ' if str(market).upper() == 'KOSDAQ' else 'KS' }"


@st.cache_data(ttl=60, show_spinner=False)
def get_current_price(code, market):
    """Yahoo Finance의 가능한 최신 가격을 가져오고, 실패하면 최근 종가로 폴백합니다."""
    symbol = yf_symbol(code, market)
    try:
        ticker = yf.Ticker(symbol)
        try:
            fi = ticker.fast_info
            for key in ("last_price", "regular_market_price", "previous_close"):
                try:
                    value = fi.get(key) if hasattr(fi, "get") else getattr(fi, key, None)
                    if value is not None and float(value) > 0:
                        return float(value)
                except Exception:
                    pass
        except Exception:
            pass

        h = ticker.history(period="5d", interval="1d", auto_adjust=False)
        if h is not None and not h.empty and "Close" in h.columns:
            s = pd.to_numeric(h["Close"], errors="coerce").dropna()
            if not s.empty:
                return float(s.iloc[-1])
    except Exception:
        pass

    try:
        d = yf.download(symbol, period="5d", auto_adjust=False, progress=False, threads=False)
        if d is not None and not d.empty:
            x = d["Close"]
            if isinstance(x, pd.DataFrame):
                x = x.iloc[:, 0]
            x = pd.to_numeric(x, errors="coerce").dropna()
            if not x.empty:
                return float(x.iloc[-1])
    except Exception:
        pass
    return None


def normalized_purchases(row):
    """기존 holdings.json도 호환. 과거 단일 평균값은 1회 레거시 매수로 간주."""
    purchases = row.get("purchases")
    if isinstance(purchases, list) and purchases:
        clean = []
        for p in purchases:
            try:
                price = float(p.get("price", 0) or 0)
                qty = float(p.get("quantity", 0) or 0)
                if price > 0 and qty > 0:
                    clean.append({
                        "price": price,
                        "quantity": qty,
                        "executed_at": p.get("executed_at") or p.get("date") or "",
                        "source": p.get("source", "매수"),
                    })
            except Exception:
                pass
        if clean:
            return clean

    avg = float(row.get("average_price", 0) or 0)
    qty = float(row.get("quantity", 0) or 0)
    if avg > 0 and qty > 0:
        return [{
            "price": avg,
            "quantity": qty,
            "executed_at": row.get("updated_at", ""),
            "source": "기존보유",
        }]
    return []


def calc_position(purchases):
    total_qty = sum(float(p["quantity"]) for p in purchases)
    total_cost = sum(float(p["price"]) * float(p["quantity"]) for p in purchases)
    avg = total_cost / total_qty if total_qty > 0 else 0.0
    return total_qty, total_cost, avg


def render_holdings_tab():
    st.subheader("💼 보유종목 관리")
    st.caption("여러 번 매수한 실제 체결내역을 누적하고, 가중평균 매수가·현재가·평가손익·현재 수익률을 자동 계산합니다.")

    try:
        holdings, holdings_sha = load_holdings()
    except Exception as e:
        st.error(str(e))
        holdings, holdings_sha = [], None

    active = [x for x in holdings if str(x.get("status", "holding")).lower() != "closed" and x.get("enabled", True)]

    c1, c2, c3 = st.columns(3)
    c1.metric("보유종목", len(active))
    c2.metric("GitHub 저장", "준비됨" if github_pat() else "PAT 미설정")
    c3.metric("시세 갱신", "60초")

    if active:
        view = []
        price_map = {}
        total_cost_all = 0.0
        total_value_all = 0.0
        priced_cost_all = 0.0

        for r in active:
            purchases = normalized_purchases(r)
            qty, total_cost, avg = calc_position(purchases)
            market = r.get("market", "KOSPI")
            current = get_current_price(r.get("ticker", ""), market)
            code = str(r.get("ticker", "")).zfill(6)
            price_map[code] = current
            value = current * qty if current is not None else None
            pnl = value - total_cost if value is not None else None
            return_pct = (pnl / total_cost * 100) if pnl is not None and total_cost > 0 else None
            total_cost_all += total_cost
            if value is not None:
                total_value_all += value
                priced_cost_all += total_cost

            view.append({
                "종목코드": code,
                "종목명": r.get("name", ""),
                "시장": market,
                "매수횟수": len(purchases),
                "평균매수가(원)": round(avg),
                "수량": qty,
                "총매수금액(원)": round(total_cost),
                "현재가(원)": round(current) if current is not None else None,
                "평가금액(원)": round(value) if value is not None else None,
                "평가손익(원)": round(pnl) if pnl is not None else None,
                "현재수익률(%)": round(return_pct, 2) if return_pct is not None else None,
            })

        p1, p2, p3, p4 = st.columns(4)
        total_pnl = total_value_all - priced_cost_all if priced_cost_all > 0 else 0.0
        total_ret = total_pnl / priced_cost_all * 100 if priced_cost_all > 0 else 0.0
        p1.metric("총 매수금액", f"{total_cost_all:,.0f}원")
        p2.metric("현재 평가금액", f"{total_value_all:,.0f}원")
        p3.metric("총 평가손익", f"{total_pnl:+,.0f}원")
        p4.metric("전체 수익률", f"{total_ret:+.2f}%")

        st.dataframe(
            pd.DataFrame(view),
            use_container_width=True,
            hide_index=True,
            column_config={
                "현재수익률(%)": st.column_config.NumberColumn(format="%+.2f%%"),
                "평가손익(원)": st.column_config.NumberColumn(format="%+,d원"),
                "현재가(원)": st.column_config.NumberColumn(format="%,d원"),
            },
        )
        st.caption(f"시세 기준: Yahoo Finance · 약 60초 캐시 · 화면 계산시각 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        st.markdown("### 매수 체결내역")
        for r in active:
            purchases = normalized_purchases(r)
            qty, total_cost, avg = calc_position(purchases)
            code = str(r.get("ticker", "")).zfill(6)
            current = price_map.get(code)
            ret = ((current / avg) - 1) * 100 if current is not None and avg > 0 else None
            label = f"{code} {r.get('name','')} · {len(purchases)}회 매수 · 평균 {avg:,.0f}원"
            if current is not None:
                label += f" · 현재가 {current:,.0f}원"
            if ret is not None:
                label += f" · 수익률 {ret:+.2f}%"
            with st.expander(label):
                hist = []
                cum_qty = 0.0
                cum_cost = 0.0
                for n, p in enumerate(purchases, 1):
                    q = float(p["quantity"])
                    pr = float(p["price"])
                    cum_qty += q
                    cum_cost += q * pr
                    hist.append({
                        "회차": n,
                        "체결일시": p.get("executed_at", ""),
                        "구분": p.get("source", "매수"),
                        "체결가(원)": round(pr),
                        "수량": q,
                        "매수금액(원)": round(pr * q),
                        "누적수량": cum_qty,
                        "누적평균가(원)": round(cum_cost / cum_qty),
                    })
                st.dataframe(pd.DataFrame(hist), use_container_width=True, hide_index=True)
    else:
        st.info("현재 등록된 보유종목이 없습니다.")

    st.markdown("### 매수 등록 / 추가 매수")
    with st.form("kr_hold_buy_form", clear_on_submit=False):
        a, b, c = st.columns([1, 2, 1])
        code = a.text_input("종목코드", placeholder="예: 005930", key="kr_hold_code").strip()
        name = b.text_input("종목명", placeholder="예: 삼성전자", key="kr_hold_name").strip()
        market = c.selectbox("시장", ["KOSPI", "KOSDAQ"], key="kr_hold_market")
        d, e = st.columns(2)
        buy_price = d.number_input("실제 체결 매수가(원)", min_value=0.0, step=100.0, format="%.0f", key="kr_hold_price")
        buy_qty = e.number_input("매수 수량", min_value=0.0, step=1.0, format="%.4f", key="kr_hold_qty")
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
                new_trade = {
                    "price": round(float(buy_price), 4),
                    "quantity": round(float(buy_qty), 6),
                    "executed_at": now,
                    "source": "추가매수" if old is not None else "신규매수",
                }

                if old is None:
                    purchases = [new_trade]
                    new_qty, total_cost, new_avg = calc_position(purchases)
                    holdings.append({
                        "ticker": code,
                        "name": name or code,
                        "market": market,
                        "mode": "holding",
                        "status": "holding",
                        "average_price": round(new_avg, 4),
                        "quantity": round(new_qty, 6),
                        "purchases": purchases,
                        "enabled": True,
                        "updated_at": now,
                    })
                    msg = f"Add Korea holding {code}"
                else:
                    purchases = normalized_purchases(old)
                    purchases.append(new_trade)
                    new_qty, total_cost, new_avg = calc_position(purchases)
                    old.update({
                        "name": name or old.get("name") or code,
                        "market": market,
                        "mode": "holding",
                        "status": "holding",
                        "average_price": round(new_avg, 4),
                        "quantity": round(new_qty, 6),
                        "purchases": purchases,
                        "enabled": True,
                        "updated_at": now,
                    })
                    old.pop("stop_loss_pct", None)
                    holdings[idx] = old
                    msg = f"Update Korea holding {code}"

                save_holdings(holdings, holdings_sha, msg)
                st.success(
                    f"{name or code} 저장 완료 · 이번 {buy_qty:g}주 @ {buy_price:,.0f}원 · "
                    f"총 {new_qty:g}주 · 새 평균매수가 {new_avg:,.0f}원 · 매수 {len(purchases)}회"
                )
                get_current_price.clear()
                st.rerun()
            except Exception as e:
                st.error(str(e))

    st.markdown("### 전량 매도 처리")
    active_codes = [str(x.get("ticker", "")).zfill(6) for x in active if x.get("ticker")]
    if active_codes:
        close_code = st.selectbox("전량 매도할 종목", active_codes, key="kr_hold_close_code")
        if st.button("✅ 전량 매도 → 보유종목에서 종료", use_container_width=True, key="kr_hold_close_btn"):
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
                    st.success(f"{close_code} 전량 매도 처리 완료")
                    st.rerun()
            except Exception as e:
                st.error(str(e))
    else:
        st.caption("전량 매도 처리할 보유종목이 없습니다.")

    st.info("추가매수는 체결 건별로 저장되며 평균매수가는 가중평균으로 재계산합니다. 현재가는 Yahoo Finance의 가능한 최신 가격을 사용하고, 불가하면 최근 종가로 대체합니다.")


def install_holdings_tab():
    if getattr(st, "_hy_korea_holdings_tab_installed", False):
        return

    original_tabs = st.tabs
    original_dataframe = st.dataframe

    def wrapped_tabs(labels, *args, **kwargs):
        labels = list(labels)
        if "💼 보유종목" in labels:
            return original_tabs(labels, *args, **kwargs)
        containers = original_tabs(labels + ["💼 보유종목"], *args, **kwargs)
        with containers[-1]:
            render_holdings_tab()
        return containers[:-1]

    def wrapped_dataframe(data=None, *args, **kwargs):
        try:
            if isinstance(data, pd.DataFrame) and "KOREA점수" in data.columns:
                data = data.drop(columns=["KOREA점수"])
        except Exception:
            pass
        return original_dataframe(data, *args, **kwargs)

    st.tabs = wrapped_tabs
    st.dataframe = wrapped_dataframe
    st._hy_korea_holdings_tab_installed = True
