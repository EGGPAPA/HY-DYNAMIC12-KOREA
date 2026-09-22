import base64
import json
import os
from datetime import datetime
from uuid import uuid4

import pandas as pd
import requests
import streamlit as st
from korea_live_price import get_live_price, price_source_label
from pension_transactions import ASSETS, KST, apply_trade, portfolio_summary, valid_price, value_position

DEFAULT_KOREA_TICKER = "292150.KS"
DEFAULT_SP_TICKER = "360750.KS"
REPO = "EGGPAPA/HY-DYNAMIC12-KOREA"
BRANCH = "main"
PENSION_PATH = "pension_holdings.json"
PENSION_API = f"https://api.github.com/repos/{REPO}/contents/{PENSION_PATH}"


def _won(x):
    try: return f"{int(round(float(x))):,}원"
    except Exception: return "-"


def _return_color(value):
    text = str(value).strip().replace(",", "").replace("원", "").replace("%", "")
    if text in {"", "-"}:
        return "color: #a0a8b8; font-weight: 700"
    try:
        number = float(text)
    except Exception:
        return "color: #f0f2f6; font-weight: 700"
    if number > 0:
        return "color: #ff4b4b; font-weight: 800"
    if number < 0:
        return "color: #4da3ff; font-weight: 800"
    return "color: #f0f2f6; font-weight: 700"


def _colored_metric(slot, label, value, number):
    color = "#ff4b4b" if number > 0 else ("#4da3ff" if number < 0 else "#f0f2f6")
    slot.markdown(
        f"""
        <div style="border:1px solid #354052;border-radius:12px;padding:16px 18px;min-height:86px;background:#1d2735">
          <div style="font-size:14px;font-weight:700;color:#f0f2f6;margin-bottom:8px">{label}</div>
          <div style="font-size:29px;font-weight:800;color:{color};line-height:1.15">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _secret(name, default=""):
    try:
        v = st.secrets.get(name, default)
        if v: return str(v).strip()
    except Exception:
        pass
    return os.getenv(name, default).strip()


def _gh_headers():
    h = {"Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"}
    pat = _secret("GITHUB_PAT")
    if pat: h["Authorization"] = f"Bearer {pat}"
    return h


def _load_pension():
    default = {"monthly":500000,"korea_ticker":DEFAULT_KOREA_TICKER,"korea_qty":0.0,"korea_avg":0,"sp_ticker":DEFAULT_SP_TICKER,"sp_qty":0.0,"sp_avg":0,"safe_now":0}
    try:
        r = requests.get(PENSION_API, headers=_gh_headers(), params={"ref":BRANCH}, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"연금 보유정보 읽기 실패: HTTP {r.status_code}. 잔고를 0으로 초기화하지 않았습니다.")
        d = r.json()
        saved = json.loads(base64.b64decode(d["content"]).decode("utf-8"))
        if not isinstance(saved, dict) or not d.get("sha"):
            raise ValueError("invalid holdings")
        if not isinstance(saved.get("transactions", []), list):
            raise ValueError("invalid transactions")
        required = {"id", "date", "asset", "side", "quantity", "price", "amount",
                    "quantity_after", "average_after"}
        if any(not isinstance(t, dict) or not required.issubset(t)
               for t in saved.get("transactions", [])):
            raise ValueError("invalid transaction entry")
        default.update(saved)
        portfolio_summary(default, {})  # Validate balances without requiring quotes.
        return default, d.get("sha")
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("연금 보유정보를 읽지 못했습니다. 저장을 중단합니다. 잠시 후 다시 시도하세요.") from None


def _save_pension(data, sha):
    pat = _secret("GITHUB_PAT")
    if not pat:
        raise RuntimeError("Streamlit Secrets의 GITHUB_PAT가 필요합니다.")
    if not sha:
        raise RuntimeError("기존 잔고 버전을 확인할 수 없어 저장하지 않았습니다.")
    payload = {
        "message":"Update pension holdings",
        "content":base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode(),
        "branch":BRANCH,
    }
    if sha: payload["sha"] = sha
    try:
        r = requests.put(PENSION_API, headers=_gh_headers(), json=payload, timeout=20)
    except requests.RequestException:
        raise RuntimeError("저장 응답을 확인하지 못했습니다. 거래내역을 확인한 뒤 같은 요청을 재시도하세요.") from None
    if r.status_code in (409, 422):
        raise RuntimeError("다른 화면에서 잔고가 변경되었습니다. 최신 잔고를 불러온 뒤 다시 확인하세요.")
    if r.status_code not in (200,201):
        raise RuntimeError(f"연금 보유정보 저장 실패: HTTP {r.status_code}")
    return r.json().get("content", {}).get("sha")


@st.cache_data(ttl=10, show_spinner=False)
def _auto_price(ticker):
    """KIS quotes only. A missing/invalid quote must never become a zero price."""
    try:
        symbol = str(ticker).strip().upper()
        code = symbol.split(".")[0]
        market = "KOSDAQ" if symbol.endswith(".KQ") else "KOSPI"
        price = get_live_price(code, market)
        if valid_price(price):
            asof = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
            return float(price), asof
    except Exception:
        pass
    return None, None


def _holding(name, qty, avg, current, target):
    position = value_position(qty, avg, current)
    rate = position["rate"]
    return {
        "자산": name, "보유수량": f"{qty:g}", "평균매수가": _won(avg),
        "현재가": _won(current) if valid_price(current) else "조회 불가",
        "매입금액": _won(position["cost"]), "평가금액": _won(position["value"]),
        "수익금": _won(position["profit"]),
        "수익률": "-" if rate is None else f"{rate:+.2f}%",
        "목표비중": f"{target:.0f}%",
    }


def _persist_trade(expected_sha, **trade):
    latest, latest_sha = _load_pension()
    if any(t.get("id") == trade["trade_id"] for t in latest.get("transactions", [])):
        return latest, latest_sha  # Retry after a lost response: never apply twice.
    if latest_sha != expected_sha:
        raise RuntimeError("저장된 잔고가 변경되었습니다. 최신 잔고를 불러온 뒤 다시 등록하세요.")
    updated = apply_trade(latest, **trade)
    return updated, _save_pension(updated, latest_sha)


def _queue_reload(data, sha, message):
    st.session_state["pension_pending_sync"] = (data, sha)
    st.session_state["pension_notice"] = message


def _sync_editor():
    pending = st.session_state.pop("pension_pending_sync", None)
    if pending is None:
        return
    data, sha = pending
    if any(t.get("id") == st.session_state.get("pension_trade_id")
           for t in data.get("transactions", [])):
        st.session_state["pension_trade_id"] = uuid4().hex
    for key in ("monthly", "korea_ticker", "korea_qty", "korea_avg",
                "sp_ticker", "sp_qty", "sp_avg"):
        value = data[key]
        if key.endswith(("_qty", "_avg")):
            value = float(value)
        st.session_state[f"pension_{key}"] = value
    st.session_state["pension_safe_text"] = _won(data["safe_now"])
    st.session_state["pension_editor_sha"] = sha


def _render_trade_form(saved, sha, *, disabled=False):
    st.markdown("### ➕ 추가 매수 · 매도 체결 등록")
    st.caption("실제 체결한 거래만 기록합니다. 증권사 주문은 전송하지 않습니다. "
               "기존 잔고부터 계산하며 수수료·세금은 미반영입니다.")
    if disabled:
        st.info("상단의 변경사항을 먼저 저장하거나 최신 잔고를 불러온 뒤 거래를 등록하세요.")
    trade_id = st.session_state.setdefault("pension_trade_id", uuid4().hex)
    with st.form(f"pension_trade_form_{trade_id}"):
        a, b = st.columns(2)
        asset = a.selectbox("거래 자산", list(ASSETS), format_func=lambda key: ASSETS[key])
        side = b.selectbox("매수 / 매도", ["buy", "sell"],
                           format_func=lambda key: "추가 매수" if key == "buy" else "매도")
        a, b, c = st.columns(3)
        filled_on = a.date_input("실제 체결일", value=datetime.now(KST).date(),
                                 max_value=datetime.now(KST).date())
        quantity = b.number_input("실제 체결수량 (주)", min_value=0, value=0, step=1)
        price = c.number_input("실제 체결가격 (원/주)", min_value=0, value=0, step=1)
        reflect_cash = st.checkbox("거래대금을 채권·현금성 평가액에도 반영", value=False,
            help="현금으로 결제한 경우에만 선택하세요. 매수금액을 차감하고 매도금액을 가산합니다. "
                 "채권을 현금으로 자동 매도하지 않습니다.")
        memo = st.text_input("거래 메모 (선택)", max_chars=300)
        confirmed = st.checkbox("증권사에서 체결된 수량·가격을 확인했습니다.")
        submitted = st.form_submit_button("체결내역 저장", disabled=disabled, type="primary",
                                           use_container_width=True)
    if submitted:
        if not confirmed:
            st.error("실제 체결내역 확인에 체크해 주세요.")
        else:
            try:
                updated, new_sha = _persist_trade(
                    sha, trade_id=trade_id, asset=asset, side=side, quantity=quantity,
                    price=price, trade_date=filled_on.isoformat(),
                    reflect_cash=reflect_cash, memo=memo)
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                st.session_state["pension_trade_id"] = uuid4().hex
                _queue_reload(updated, new_sha, "체결내역 저장 완료 · 보유수량과 평균매수가를 반영했습니다.")
                st.rerun()

    st.markdown("### 📋 연금 매수·매도 내역")
    trades = saved.get("transactions", [])
    if not trades:
        st.caption("아직 등록한 거래가 없습니다. 기존 보유수량은 시작 잔고이며, 앞으로 입력하는 거래부터 기록합니다.")
        return
    rows = []
    for trade in reversed(trades):
        rows.append({
            "체결일": trade["date"], "자산": ASSETS.get(trade["asset"], trade["asset"]),
            "티커": trade.get("ticker", ""), "구분": "매수" if trade["side"] == "buy" else "매도",
            "수량": trade["quantity"], "체결가격": _won(trade["price"]),
            "체결금액": _won(trade["amount"]), "매도 실현손익": _won(trade.get("realized_profit")),
            "거래 후 수량": trade["quantity_after"], "거래 후 평균가": _won(trade["average_after"]),
            "현금 반영": "반영" if trade.get("reflect_cash") else "미반영", "메모": trade.get("memo", ""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    realized = sum(t.get("realized_profit") or 0 for t in trades)
    st.caption(f"등록한 매도의 누적 실현손익: {_won(realized)} · 수수료·세금 제외 · 미등록 과거 거래 제외")


@st.fragment(run_every="10s")
def _render_pension_prices(snapshot, korea_signal):
    prices = {}
    c1, c2, c3 = st.columns(3)
    with c3:
        if st.button("🔄 현재가 다시 조회", use_container_width=True):
            _auto_price.clear()
            get_live_price.clear()
        st.caption("이 시세·평가 영역만 10초마다 갱신합니다. 체결 입력값은 유지됩니다.")
    for asset, column in (("korea", c1), ("sp", c2)):
        ticker = snapshot[f"{asset}_ticker"].strip()
        auto, asof = _auto_price(ticker)
        with column:
            if auto is not None:
                st.metric(f"{ASSETS[asset]} 현재가", _won(auto))
                st.caption(f"조회 {asof} · {price_source_label()} · {ticker}")
                prices[asset] = auto
            else:
                st.warning(f"{ASSETS[asset]} 현재가 조회 불가")
                manual = st.number_input(f"{ASSETS[asset]} 수동 현재가 (0=미입력)",
                    min_value=0, step=1, value=0, key=f"pension_manual_{asset}_{ticker}")
                prices[asset] = manual if valid_price(manual) else None
                if prices[asset] is not None:
                    st.caption("사용자 수동 입력가 기준 · 실시간 시세 아님")
    summary = portfolio_summary(snapshot, prices)
    rows = []
    for asset, target in (("sp", 50), ("korea", 30)):
        row = _holding(ASSETS[asset], snapshot[f"{asset}_qty"],
                       snapshot[f"{asset}_avg"], prices[asset], target)
        row["현재비중"] = f"{summary['weights'][asset]:.1f}%" if summary["complete"] else "-"
        rows.append(row)
    rows.append({
        "자산": "채권·현금성", "보유수량": "-", "평균매수가": "-", "현재가": "-",
        "매입금액": "-", "평가금액": _won(snapshot["safe_now"]), "수익금": "-",
        "수익률": "-", "목표비중": "20%",
        "현재비중": f"{summary['weights']['safe']:.1f}%" if summary["complete"] else "-",
    })
    st.markdown("### 💼 현재 연금 포트폴리오")
    st.dataframe(pd.DataFrame(rows).style.map(_return_color, subset=["수익금", "수익률"])
                 .set_properties(**{"font-size": "15px", "padding": "9px 10px"}).hide(axis="index"),
                 use_container_width=True, hide_index=True)
    p1, p2, p3 = st.columns(3)
    p1.metric("총 평가액", _won(summary["total"]))
    if summary["complete"]:
        _colored_metric(p2, "주식 평가손익", _won(summary["profit"]), summary["profit"])
        _colored_metric(p3, "주식 평가수익률", f"{summary['rate']:+.2f}%", summary["rate"])
    else:
        p2.metric("주식 평가손익", "계산 보류")
        p3.metric("주식 평가수익률", "계산 보류")
        st.warning("보유 중인 ETF 시세가 없어 전체 평가·비중·정기매수 배분 계산을 보류합니다. "
                   "조회 실패를 0원 또는 -100% 손실로 처리하지 않습니다.")
    st.markdown("### 🎯 장기 목표비중")
    st.write("S&P500 **50%** · KOREA TOP10 **30%** · 채권·현금성 **20%**")
    st.markdown("### ⚡ 이번 달 정기매수")
    if summary["complete"]:
        a, b, c = st.columns(3)
        for slot, asset, label in ((a, "sp", "S&P500"), (b, "korea", "KOREA TOP10"),
                                   (c, "safe", "채권·현금성")):
            slot.metric(label, _won(summary["buys"][asset]))
        st.caption("목표비중과 입력한 월 납입액 기준 참고 배분입니다. 실제 주문은 실행하지 않습니다.")
        if "2개월 이탈" in korea_signal:
            st.warning("MA5 방어신호: 기존 KOREA TOP10 전술비중 조정 여부를 월말에 점검하세요.")
        elif "1개월 이탈" in korea_signal or "MA5 부근" in korea_signal:
            st.info("MA5 주의신호: 기존 보유분은 관찰합니다.")
    else:
        st.info("정상 현재가가 확보되면 매수 배분금액을 다시 계산합니다.")


def render_pension_manager_tab():
    _sync_editor()
    st.subheader("🏦 연금저축 · 월간 실행판")
    st.caption("보유정보와 체결내역은 GitHub에 함께 저장됩니다. 시세·평가는 10초마다 자동 갱신됩니다.")
    try:
        saved, saved_sha = _load_pension()
    except RuntimeError as error:
        st.error(str(error))
        st.info("기존 잔고와 거래내역을 보호하기 위해 입력·저장을 중단했습니다.")
        return
    notice = st.session_state.pop("pension_notice", None)
    if notice:
        st.success(notice)
    editor_sha = st.session_state.setdefault("pension_editor_sha", saved_sha)
    stale = editor_sha != saved_sha
    if stale:
        st.warning("다른 화면에서 잔고가 변경되었습니다. 최신 정보를 불러온 뒤 저장하세요.")
    if st.button("저장된 최신정보 불러오기", help="저장하지 않은 입력값은 저장된 값으로 되돌립니다."):
        _queue_reload(saved, saved_sha, "저장된 최신 보유정보를 불러왔습니다.")
        st.rerun()

    monthly = st.number_input("월 납입액", min_value=0, step=10000,
        value=int(saved.get("monthly", 500000)), format="%d", key="pension_monthly")
    st.markdown("### 📒 보유자산 입력 · 잔고 보정")
    st.caption("이 영역은 시작 잔고·직접 보정용입니다. 실제 추가 매수·매도는 아래 체결 등록을 사용하세요.")
    t1, t2 = st.columns(2)
    with t1:
        korea_ticker = st.text_input("KOREA TOP10 티커",
            value=str(saved["korea_ticker"]), key="pension_korea_ticker")
        korea_qty = st.number_input("KOREA TOP10 보유수량", min_value=0.0, step=1.0,
            value=float(saved["korea_qty"]), key="pension_korea_qty")
        korea_avg = st.number_input("KOREA TOP10 평균매수가", min_value=0.0, step=1.0,
            value=float(saved["korea_avg"]), format="%.4f", key="pension_korea_avg")
    with t2:
        sp_ticker = st.text_input("S&P500 ETF 티커", value=str(saved["sp_ticker"]), key="pension_sp_ticker")
        sp_qty = st.number_input("S&P500 ETF 보유수량", min_value=0.0, step=1.0,
            value=float(saved["sp_qty"]), key="pension_sp_qty")
        sp_avg = st.number_input("S&P500 ETF 평균매수가", min_value=0.0, step=1.0,
            value=float(saved["sp_avg"]), format="%.4f", key="pension_sp_avg")
    safe_text = st.text_input("채권·현금성 평가액", value=_won(saved["safe_now"]),
        help="예: 128,532원", key="pension_safe_text")
    cleaned_safe = safe_text.replace(",", "").replace("원", "").strip()
    try:
        safe_now = int(cleaned_safe)
        if safe_now < 0:
            raise ValueError()
    except ValueError:
        st.error("채권·현금성 평가액은 0 이상의 원화 정수로 입력하세요.")
        return
    korea_signal = st.selectbox("KOREA TOP10 MA5 참고신호", [
        "🟢 MA5 위 · 상승", "🟡 MA5 위 · 횡보", "🟠 MA5 부근",
        "🔴 MA5 1개월 이탈", "🔴 2개월 이탈 · MA5 하락", "🚀 MA5 재돌파"])
    fields = {
        "monthly": int(monthly), "korea_ticker": korea_ticker.strip().upper(),
        "korea_qty": float(korea_qty), "korea_avg": float(korea_avg),
        "sp_ticker": sp_ticker.strip().upper(), "sp_qty": float(sp_qty),
        "sp_avg": float(sp_avg), "safe_now": safe_now,
    }
    dirty = any(fields[key] != saved.get(key) for key in fields)
    if st.button("💾 연금 보유정보 저장", type="primary", use_container_width=True, disabled=stale):
        try:
            for asset in ASSETS:
                code = fields[f"{asset}_ticker"].split(".")[0]
                if len(code) != 6 or not code.isascii() or not code.isalnum():
                    raise ValueError("국내 ETF 6자리 종목코드(.KS 또는 .KQ)를 확인하세요.")
                if fields[f"{asset}_qty"] > 0 and fields[f"{asset}_avg"] <= 0:
                    raise ValueError("보유 중인 ETF의 평균매수가를 입력하세요.")
            latest, latest_sha = _load_pension()
            if latest_sha != saved_sha:
                raise RuntimeError("저장된 잔고가 변경되었습니다. 최신 정보를 불러와 주세요.")
            updated = dict(latest)
            updated.update(fields)  # Preserve transactions, opening balances and other metadata.
            changes = {k: {"before": latest.get(k), "after": v} for k, v in fields.items()
                       if k.endswith(("_qty", "_avg", "_ticker")) and latest.get(k) != v}
            if changes:
                updated["balance_adjustments"] = list(latest.get("balance_adjustments", [])) + [{
                    "recorded_at": datetime.now(KST).isoformat(timespec="seconds"), "changes": changes}]
            new_sha = _save_pension(updated, latest_sha)
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        else:
            _queue_reload(updated, new_sha, "연금 보유정보 저장 완료 · 기존 거래내역을 유지했습니다.")
            st.rerun()

    _render_trade_form(saved, saved_sha, disabled=stale or dirty)
    if stale or dirty:
        st.info("아래 시세·평가는 저장된 잔고 기준입니다. 미저장 입력값은 반영하지 않습니다.")
    _render_pension_prices(saved, korea_signal)
