"""Pension book entries only. This module never places brokerage orders."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math

KST = timezone(timedelta(hours=9))
ASSETS = {"korea": "KOREA TOP10", "sp": "S&P500"}


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
                reflect_cash=False, memo="", today=None):
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
    if asset not in ASSETS or side not in {"buy", "sell"}:
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

    old_qty = _number(result.get(f"{asset}_qty", 0), "기존 보유수량")
    old_avg = _number(result.get(f"{asset}_avg", 0), "기존 평균매수가")
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
    result[f"{asset}_qty"] = float(new_qty)
    result[f"{asset}_avg"] = float(new_avg)
    result["safe_now"] = float(new_safe)
    result.setdefault("opening_balance", {
        key: saved.get(key) for key in
        ("korea_ticker", "korea_qty", "korea_avg", "sp_ticker", "sp_qty", "sp_avg", "safe_now")
    })
    trades.append({
        "id": trade_id, "date": filled_on.isoformat(),
        "recorded_at": datetime.now(KST).isoformat(timespec="seconds"),
        "asset": asset, "ticker": str(saved.get(f"{asset}_ticker", "")),
        "side": side, "quantity": float(qty), "price": float(unit_price),
        "amount": float(amount), "realized_profit": None if realized is None else float(realized),
        "quantity_before": float(old_qty), "quantity_after": float(new_qty),
        "average_before": float(old_avg), "average_after": float(new_avg),
        "reflect_cash": bool(reflect_cash), "cash_before": float(safe),
        "cash_after": float(new_safe), "memo": str(memo).strip()[:300],
    })
    result["schema_version"] = 2
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


def portfolio_summary(saved, prices):
    positions = {asset: value_position(saved.get(f"{asset}_qty", 0),
                 saved.get(f"{asset}_avg", 0), prices.get(asset)) for asset in ASSETS}
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
    gaps = {a: max(0, (total + monthly) * weight - values[a]) for a, weight in targets.items()}
    gap_sum = sum(gaps.values())
    buys = {a: monthly * gaps[a] / gap_sum if gap_sum else monthly * weight
            for a, weight in targets.items()}
    return {"positions": positions, "complete": True, "total": total, "profit": profit,
            "rate": profit / cost * 100 if cost else 0,
            "weights": {a: v / total * 100 if total else 0 for a, v in values.items()},
            "buys": buys}
