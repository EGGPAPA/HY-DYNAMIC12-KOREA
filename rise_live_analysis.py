"""Current-quote evaluation. Pure functions; no cached verdicts or order placement."""
import math
from datetime import datetime, timedelta, timezone

import pandas as pd

KST = timezone(timedelta(hours=9))
MAX_QUOTE_AGE = 45


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def market_context(calendar, now):
    now = now.astimezone(KST)
    today = now.strftime('%Y%m%d')
    dated = {str(x.get('bass_dt')): x for x in calendar}
    if today not in dated:
        return {'error': '거래일 달력 확인 실패'}
    opened = sorted(d for d, row in dated.items() if row.get('opnd_yn') == 'Y' and d <= today)
    # KRX cash-session quotes only (J). Before opening, use the last session.
    if now.hour < 9:
        opened = [d for d in opened if d < today]
    if len(opened) < 2:
        return {'error': '직전 거래일 확인 실패'}
    day, previous = opened[-1], opened[-2]
    intraday = day == today and (9, 0) <= (now.hour, now.minute) < (15, 30)
    return {'day': day, 'previous': previous, 'intraday': intraday,
            'mode': '장중 잠정' if intraday else '최근 거래일 참고'}


def on_hold(row, reason, quote=None):
    result = {k: row.get(k) for k in ('ticker', 'name', 'market')}
    result.update(valid=False, reason=reason, label='⚪ 평가 보류', action='평가 보류',
                  price=None, score=None, mandatory_count=0, auxiliary_count=0,
                  mandatory_label='— 평가 보류', auxiliary_label='— 평가 보류',
                  checks={}, quote_received_at=None)
    # Only a successful, recent quote may populate this field (validated by caller).
    if quote:
        result.update(price=quote['price'], quote_received_at=quote['received_at'])
    return result


def evaluate_current(row, history, quote, context, now):
    now = now.astimezone(KST)
    if not quote or not quote.get('ok'):
        return on_hold(row, (quote or {}).get('error', 'KIS 현재가 수신 실패'))
    try:
        age = (now - datetime.fromisoformat(quote['received_at'])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return on_hold(row, '시세 수신 시각 없음')
    price, volume, previous_close = (number(quote.get(k)) for k in ('price', 'volume', 'previous_close'))
    if age < -2 or age > MAX_QUOTE_AGE or price is None or price <= 0:
        return on_hold(row, '시세 지연 또는 잘못된 가격')
    if context.get('error'):
        return on_hold(row, context['error'], quote)
    if volume is None or volume <= 0 or previous_close is None or previous_close <= 0:
        return on_hold(row, '누적 거래량·전일 종가 확인 필요', quote)
    if history is None or history.empty or not {'Close', 'Volume'}.issubset(history.columns):
        return on_hold(row, 'KIS 일봉 수신 실패', quote)
    bars = history[['Close', 'Volume']].copy()
    try:
        bars.index = pd.to_datetime(bars.index).tz_localize(None).normalize()
        if bars.index.duplicated().any():
            return on_hold(row, '중복 일봉 확인 필요', quote)
        bars = bars.sort_index()
        day = pd.Timestamp(context['day'])
        past = bars[bars.index < day].copy()
        if past.empty or past.index[-1].strftime('%Y%m%d') != context['previous']:
            return on_hold(row, '직전 거래일 일봉 누락', quote)
        for col in ('Close', 'Volume'):
            past[col] = pd.to_numeric(past[col], errors='coerce')
        if len(past) < 64 or not all(math.isfinite(float(x)) for x in past.tail(64).to_numpy().ravel()):
            return on_hold(row, '유효한 64거래일 일봉 부족', quote)
        if (past.Close.tail(64) <= 0).any() or (past.Volume.tail(64) < 0).any():
            return on_hold(row, '잘못된 과거 가격·거래량', quote)
        if abs(float(past.Close.iloc[-1]) / previous_close - 1) > .005:
            return on_hold(row, '일봉과 시세의 전일 종가 불일치', quote)
    except (ValueError, TypeError, KeyError):
        return on_hold(row, '일봉 날짜·값 확인 필요', quote)
    # Discard today's possibly cached bar, then add EXACTLY ONE live observation.
    current = past.copy()
    current.loc[day] = [price, volume]
    close = current.Close
    ma5, ma20, ma60 = [close.rolling(n).mean() for n in (5, 20, 60)]
    m5, m20, m60 = [float(s.iloc[-1]) for s in (ma5, ma20, ma60)]
    prior_high = float(past.Close.tail(20).max())
    vol_base = float(past.Volume.tail(20).mean())
    if vol_base <= 0:
        return on_hold(row, '20일 평균 거래량 없음', quote)
    # Today's cumulative volume / previous 20 full-session average, not a 3-day max.
    volume_ratio = volume / vol_base
    gap20 = (price / m20 - 1) * 100
    ret10 = (price / float(close.iloc[-11]) - 1) * 100
    ret20 = (price / float(close.iloc[-21]) - 1) * 100
    rising20 = m20 > float(ma20.iloc[-6])
    cross = (ma20 > ma60) & (ma20.shift(1) <= ma60.shift(1))
    recent_cross = bool(cross.tail(10).fillna(False).any())
    breakout_now = price >= prior_high and float(past.Close.iloc[-1]) < prior_high
    score = (20 if price > m20 else 0) + (15 if rising20 else 0) + (15 if price > m60 else 0)
    score += 15 if recent_cross else 0
    score += 20 if breakout_now else (8 if price >= prior_high * .98 else 0)
    score += 15 if volume_ratio >= 1.5 else (8 if volume_ratio >= 1.1 else 0)
    if gap20 > 12:
        score -= 20
    if ret10 > 25:
        score -= 15
    score = max(0, min(100, score))
    late = gap20 > 15 or ret20 > 35 or ret10 > 25
    if late:
        label = '🔴 급등·추격주의'
    elif score >= 75 and gap20 <= 8:
        label = '🟢 상승초입'
    elif score >= 60:
        label = '🟡 돌파확인'
    elif price > m60 and rising20:
        label = '🔵 준비구간'
    else:
        label = '⚪ 신호대기'
    first = max(m20, prior_high * .99)
    if label.startswith('🟢'):
        first = min(price, max(m20, prior_high * .995))
    second = m20 * 1.01
    stop = min(price * .97, max(m20 * .96, float(close.tail(10).min()) * .98))
    # Conditions use the same rounded reference levels shown on screen.
    first, second, stop, prior_high = map(round, (first, second, stop, prior_high))
    gap = (price / first - 1) * 100
    risk = (price - stop) / price * 100
    if label.startswith('🟢') and first * .98 <= price <= first * 1.02 and price > stop:
        label = '🟣 1차 매수구간'
    checks = {'gap': -1 <= gap <= 3, 'volume': volume_ratio >= 1.5,
              'close': price >= prior_high > 0, 'risk': 0 <= risk <= 7,
              'stage': label.startswith('🟣'), 'score': score >= 85, 'persistence': False}
    averages = [m5, m20, m60]
    span = (max(averages) - min(averages)) / (sum(averages) / 3) * 100
    convergence = ('🟠 수렴·하방주의' if price < min(averages) else '🔵 수렴 관찰') if span <= 3 else '⚪ 비수렴'
    result = {k: row.get(k) for k in ('ticker', 'name', 'market')}
    result.update(valid=True, reason='', price=price, quote_received_at=quote['received_at'],
                  analysis_day=day.strftime('%Y-%m-%d'), history_day=past.index[-1].strftime('%Y-%m-%d'),
                  mode=context['mode'], intraday=context['intraday'], label=label, score=score,
                  volume_ratio=volume_ratio, gap20=gap20, buy1=first, buy2=second, stop=stop,
                  breakout=prior_high, gap=gap, risk=risk, ma5=m5, ma20=m20, ma60=m60,
                  convergence=convergence, span=span, checks=checks,
                  average_value=float((past.Close * past.Volume).tail(20).mean()),
                  chart=pd.DataFrame({'가격(마지막=현재가)': close, '5일선': ma5, '20일선': ma20, '60일선': ma60}).tail(100))
    return apply_labels(result)


def apply_labels(result):
    checks = result['checks']
    mandatory = sum(bool(checks[k]) for k in ('gap', 'volume', 'close', 'risk'))
    auxiliary = sum(bool(checks[k]) for k in ('stage', 'score', 'persistence'))
    result.update(mandatory_count=mandatory, auxiliary_count=auxiliary,
                  mandatory_label=f"{'🟢' if mandatory == 4 else '🟠'} 필수 {mandatory}/4",
                  auxiliary_label=f'보조 {auxiliary}/3')
    if result['label'].startswith('🔴'):
        action = '추격주의 · 신규 진입 조건 미충족'
    elif mandatory == 4:
        action = '필수 충족 · 장중 잠정 관찰' if result['intraday'] else '필수 충족 · 최근 거래일 참고'
    else:
        action = '조건 미충족 · 관찰'
    result['action'] = action
    return result


def observe_persistence(results, states):
    """Session-local, distinct receipts >=10 seconds apart. Failures break a streak."""
    for row in results:
        code = row['ticker']
        if not row['valid']:
            states.pop(code, None)
            continue
        current = datetime.fromisoformat(row['quote_received_at'])
        stable = all(row['checks'][k] for k in ('gap', 'close', 'risk')) and row['intraday']
        state = states.get(code, {})
        if not stable or state.get('day') != row['analysis_day']:
            state = {'day': row['analysis_day'], 'count': 0, 'at': None}
        if stable and (not state['at'] or (current - datetime.fromisoformat(state['at'])).total_seconds() >= 10):
            state.update(count=state['count'] + 1, at=row['quote_received_at'])
        row['checks']['persistence'] = bool(stable and state['count'] >= 2)
        states[code] = state
        apply_labels(row)


def priority_key(row):
    if not row['valid']:
        return (1, 0, 0, 99, 999, 0, 0, row['ticker'])
    stage = {'🟣': 0, '🟢': 1, '🟡': 2, '🔵': 3}.get(row['label'][0], 4)
    return (0, -row['mandatory_count'], -row['auxiliary_count'], stage,
            abs(row['gap']), -row['volume_ratio'], -row['score'], row['ticker'])
