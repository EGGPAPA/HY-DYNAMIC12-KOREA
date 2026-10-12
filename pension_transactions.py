"""Pension book entries only. This module never places brokerage orders."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math
import re

KST = timezone(timedelta(hours=9))
ASSETS = {"korea": "KOREA TOP10", "sp": "S&P500"}
GROUPS = {"korea": "국내주식 ETF", "sp": "해외주식 ETF", "safe": "채권·현금성 ETF"}


def normalize_ticker(value):
    ticker = str(value).strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{6}(?:\.KS|\.KQ)?", ticker, flags=re.ASCII):
        raise ValueError("국내상장 ETF의 6자리 종목코드(.KS 또는 .KQ)를 확인하세요.")
    return ticker if "." in ticker else ticker + ".KS"


def pension_positions(saved):
    """Read legacy slots and additive ETF positions without rewriting the book."""
    rows = {}
    seen = set()
    for asset, label in ASSETS.items():
        ticker = normalize_ticker(saved.get(f"{asset}_ticker", ""))
        rows[asset] = {"ticker": ticker, "name": label, "group": asset,
                       "qty": saved.get(f"{asset}_qty", 0), "avg": saved.get(f"{asset}_avg", 0)}
    extras = saved.get("etf_positions", {})
    if not isinstance(extras, dict):
        raise ValueError("추가 ETF 잔고 형식을 확인하세요.")
    for key, row in extras.items():
        if not isinstance(row, dict) or not {"ticker", "name", "group", "qty", "avg"}.issubset(row):
            raise ValueError("추가 ETF 잔고 항목을 확인하세요.")
        ticker = normalize_ticker(row["ticker"])
        if key != "etf:" + ticker or not str(row["name"]).strip() or row["group"] not in GROUPS:
            raise ValueError("추가 ETF 이름·종목코드·자산 구분을 확인하세요.")
        rows[key] = {**row, "ticker": ticker}
    for row in rows.values():
        # One domestic code cannot appear as both .KS and .KQ or in two buckets.
        code = row["ticker"].split(".")[0]
        if code in seen:
            raise ValueError("같은 ETF가 중복되어 있습니다. 기존 거래 자산을 선택하세요.")
        seen.add(code)
        qty = _number(row["qty"], "보유수량")
        avg = _number(row["avg"], "평균매수가")
        if qty > 0 and avg <= 0:
            raise ValueError("기존 평균매수가를 먼저 확인하고 저장하세요.")
        row["qty"], row["avg"] = float(qty), float(avg)
    return rows


def _number(value, label, *, positive=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label}: 올바른 숫자를 입력하세요.") from None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError(f"{label}: {'0보다 큰' if positive else '0 이상의'} 유한한 숫자가 필요합니다.")
    if not math.isfinite(float(number)):
        raise ValueError(f"{label}: 숫자가 너무 큽니다.")
    return number


def valid_price(value):
    try:
        return value is not None and math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def apply_trade(saved, *, trade_id, asset, side, quantity, price, trade_date,
                reflect_cash=False, memo="", today=None, new_etf=None):
    """Apply an ordered, idempotent fill using moving-average acquisition cost.

    Existing balances are the opening snapshot, not synthetic transactions.
    Amounts and realized P/L exclude fees and taxes; sales preserve average cost.
    """
    if not isinstance(saved, dict):
        raise ValueError("저장된 연금 잔고 형식이 올바르지 않습니다.")
    result = deepcopy(saved)
    trades = result.setdefault("transactions", [])
    if not isinstance(trades, list) or any(not isinstance(t, dict) for t in trades):
        raise ValueError("거래내역 형식이 올바르지 않습니다. 기존 데이터를 확인하세요.")
    if not isinstance(trade_id, str) or not trade_id.strip():
        raise ValueError("거래 식별자가 필요합니다.")
    if any(t.get("id") == trade_id for t in trades):
        return result
    positions = pension_positions(result)
    if asset == "new":
        if side != "buy" or not isinstance(new_etf, dict):
            raise ValueError("새 ETF는 매수 체결로 등록하세요.")
        ticker = normalize_ticker(new_etf.get("ticker", ""))
        name = str(new_etf.get("name", "")).strip()
        group = new_etf.get("group")
        if not name or len(name) > 100 or group not in GROUPS:
            raise ValueError("ETF 이름(100자 이내)과 자산 구분을 확인하세요.")
        if any(p["ticker"].split(".")[0] == ticker.split(".")[0] for p in positions.values()):
            raise ValueError("이미 등록된 ETF입니다. 거래 자산에서 해당 ETF를 선택해 추가 매수하세요.")
        asset = "etf:" + ticker
        positions[asset] = {"ticker": ticker, "name": name, "group": group, "qty": 0, "avg": 0}
        result.setdefault("etf_positions", {})[asset] = deepcopy(positions[asset])
    elif new_etf is not None:
        raise ValueError("기존 ETF의 거래에는 새 ETF 정보를 넣을 수 없습니다.")
    if asset not in positions or side not in {"buy", "sell"}:
        raise ValueError("자산과 매수/매도 구분을 확인하세요.")
    qty = _number(quantity, "체결수량", positive=True)
    unit_price = _number(price, "체결가격", positive=True)
    if qty != qty.to_integral_value() or unit_price != unit_price.to_integral_value():
        raise ValueError("국내 ETF 체결수량과 원화 체결가격은 정수로 입력하세요.")
    try:
        filled_on = date.fromisoformat(str(trade_date))
    except (TypeError, ValueError):
        raise ValueError("체결일을 확인하세요.") from None
    if filled_on > (today or datetime.now(KST).date()):
        raise ValueError("미래 날짜의 체결내역은 등록할 수 없습니다.")
    prior_dates = [t.get("date", "") for t in trades if t.get("asset") == asset]
    if prior_dates and filled_on.isoformat() < max(prior_dates):
        raise ValueError("평균매수가 계산을 위해 같은 자산의 거래는 체결일 순서대로 등록하세요.")

    position = positions[asset]
    old_qty = _number(position["qty"], "기존 보유수량")
    old_avg = _number(position["avg"], "기존 평균매수가")
    if old_qty > 0 and old_avg <= 0:
        raise ValueError("기존 평균매수가를 먼저 확인하고 저장하세요.")
    if side == "sell" and qty > old_qty:
        raise ValueError(f"매도수량이 보유수량({old_qty}주)을 초과합니다.")
    amount = qty * unit_price
    new_qty = old_qty + qty if side == "buy" else old_qty - qty
    if side == "buy":
        new_avg = (old_qty * old_avg + amount) / new_qty
        realized = None
    else:
        new_avg = old_avg if new_qty > 0 else Decimal(0)
        realized = (unit_price - old_avg) * qty
    safe = _number(result.get("safe_now", 0), "채권·현금성 평가액")
    new_safe = safe
    if reflect_cash:
        new_safe = safe - amount if side == "buy" else safe + amount
        if new_safe < 0:
            raise ValueError("채권·현금성 잔액이 매수금액보다 적습니다. 실제 현금잔액을 확인하세요.")
    for number in (amount, new_qty, new_avg, new_safe):
        _number(number, "계산 결과")
    if asset in ASSETS:
        result[f"{asset}_qty"] = float(new_qty)
        result[f"{asset}_avg"] = float(new_avg)
    else:
        result["etf_positions"][asset].update(qty=float(new_qty), avg=float(new_avg))
    result["safe_now"] = float(new_safe)
    result.setdefault("opening_balance", {
        key: saved.get(key) for key in
        ("korea_ticker", "korea_qty", "korea_avg", "sp_ticker", "sp_qty", "sp_avg", "safe_now")
    })
    trades.append({
        "id": trade_id, "date": filled_on.isoformat(),
        "recorded_at": datetime.now(KST).isoformat(timespec="seconds"),
        "asset": asset, "ticker": position["ticker"], "name": position["name"],
        "group": position["group"],
        "side": side, "quantity": float(qty), "price": float(unit_price),
        "amount": float(amount), "realized_profit": None if realized is None else float(realized),
        "quantity_before": float(old_qty), "quantity_after": float(new_qty),
        "average_before": float(old_avg), "average_after": float(new_avg),
        "reflect_cash": bool(reflect_cash), "cash_before": float(safe),
        "cash_after": float(new_safe), "memo": str(memo).strip()[:300],
    })
    result["schema_version"] = 3
    return result


def value_position(quantity, average, price):
    qty = float(_number(quantity, "보유수량"))
    avg = float(_number(average, "평균매수가"))
    cost = qty * avg
    # A missing quote on an unheld asset does not block portfolio valuation.
    value = 0.0 if qty == 0 else (qty * float(price) if valid_price(price) else None)
    profit = None if value is None else value - cost
    rate = None if profit is None else (profit / cost * 100 if cost else 0.0)
    return {"cost": cost, "value": value, "profit": profit, "rate": rate}


def adjust_extra_position(saved, asset, *, quantity, average, name, group):
    """Explicit balance correction, not a trade; retain the full audit trail."""
    positions = pension_positions(saved)
    if asset in ASSETS or asset not in positions:
        raise ValueError("보정할 추가 ETF를 확인하세요.")
    qty = _number(quantity, "보유수량")
    avg = _number(average, "평균매수가")
    if qty != qty.to_integral_value() or (qty > 0 and avg <= 0):
        raise ValueError("보유수량은 정수, 보유 중 평균매수가는 0보다 크게 입력하세요.")
    name = str(name).strip()
    if not name or len(name) > 100 or group not in GROUPS:
        raise ValueError("ETF 이름과 자산 구분을 확인하세요.")
    result = deepcopy(saved)
    before = result["etf_positions"][asset]
    after = {**before, "name": name, "group": group, "qty": float(qty),
             "avg": float(avg) if qty else 0.0}
    if after != before:
        result.setdefault("balance_adjustments", []).append({
            "recorded_at": datetime.now(KST).isoformat(timespec="seconds"),
            "asset": asset, "before": deepcopy(before), "after": deepcopy(after)})
        result["etf_positions"][asset] = after
    return result


def portfolio_summary(saved, prices):
    holdings = pension_positions(saved)
    positions = {asset: value_position(row["qty"], row["avg"], prices.get(asset))
                 for asset, row in holdings.items()}
    safe = float(_number(saved.get("safe_now", 0), "채권·현금성 평가액"))
    monthly = float(_number(saved.get("monthly", 0), "월 납입액"))
    complete = all(p["value"] is not None for p in positions.values())
    if not complete:
        return {"positions": positions, "complete": False, "total": None,
                "profit": None, "rate": None, "weights": None, "buys": None}
    values = {asset: p["value"] for asset, p in positions.items()}
    values["safe"] = safe
    total = sum(values.values())
    cost = sum(p["cost"] for p in positions.values())
    profit = sum(p["profit"] for p in positions.values())
    targets = {"sp": .5, "korea": .3, "safe": .2}
    group_values = {"korea": 0.0, "sp": 0.0, "safe": safe}
    for asset, row in holdings.items():
        group_values[row["group"]] += positions[asset]["value"]
    gaps = {a: max(0, (total + monthly) * weight - group_values[a]) for a, weight in targets.items()}
    gap_sum = sum(gaps.values())
    buys = {a: monthly * gaps[a] / gap_sum if gap_sum else monthly * weight
            for a, weight in targets.items()}
    return {"positions": positions, "complete": True, "total": total, "profit": profit,
            "rate": profit / cost * 100 if cost else 0,
            "weights": {a: v / total * 100 if total else 0 for a, v in values.items()},
            "buys": buys, "group_values": group_values}

