"""Reconstruct closing-date leadership transitions within the fixed sector sample.

No orders, notifications, watchlist changes, credentials or synthetic intraday times.
Historical events are recalculated from today's fixed constituents, not an alert log.
"""
from datetime import date
from math import isfinite

from sector_observation import SECTORS, clean_closes, percent

TIE_TOLERANCE = 1e-8  # percentage points; presentation rounding never determines rank
CONFIRMATION_DAYS = 3


def analyze_rotation(observations, confirmation_days=CONFIRMATION_DAYS):
    """Each observation is one completed trading date with the same sector universe."""
    if confirmation_days < 2 or len(observations) < 2:
        raise ValueError('교체 확인에는 2일 이상의 연속 거래일 자료가 필요합니다')
    names = set(observations[0]['strengths'])
    if len(names) < 2:
        raise ValueError('두 업종 이상 필요합니다')
    previous_date = ''
    daily, series = [], []
    for observation in observations:
        day = date.fromisoformat(observation['date']).isoformat()
        strengths = observation['strengths']
        if day <= previous_date or set(strengths) != names:
            raise ValueError('비교 날짜와 업종 범위가 일치해야 합니다')
        if any(not isfinite(value) for value in strengths.values()):
            raise ValueError('유효하지 않은 업종 강도')
        previous_date = day
        order = sorted(names, key=lambda name: (-strengths[name], name))
        top = strengths[order[0]]
        leaders = [name for name in order if top - strengths[name] <= TIE_TOLERANCE]
        daily.append({'date': day, 'leaders': leaders, 'leader': leaders[0] if len(leaders) == 1 else None,
                      'strengths': dict(strengths), 'order': order})
        for name in order:
            rank = 1 + sum(value > strengths[name] + TIE_TOLERANCE for value in strengths.values())
            series.append({'date': day, 'sector': name, 'strength': strengths[name], 'rank': rank})

    events = []
    run_start = 0
    while run_start < len(daily):
        leader = daily[run_start]['leader']
        run_end = run_start + 1
        if leader is None:
            run_start = run_end
            continue
        while run_end < len(daily) and daily[run_end]['leader'] == leader:
            run_end += 1
        if run_start > 0:
            before = daily[run_start - 1]
            kind = 'cross' if before['leader'] else 'tie_resolved'
            confirmed = (daily[run_start + confirmation_days - 1]['date']
                         if run_end - run_start >= confirmation_days else None)
            events.append({'date': daily[run_start]['date'], 'kind': kind,
                           'from': before['leader'], 'previous_leaders': before['leaders'],
                           'to': leader, 'days': run_end - run_start, 'confirmed_date': confirmed,
                           'ongoing': run_end == len(daily)})
        run_start = run_end

    latest = daily[-1]
    result = {'valid': True, 'asof': latest['date'], 'start': daily[0]['date'], 'daily': daily,
              'series': series, 'events': list(reversed(events)), 'confirmation_days': confirmation_days,
              'leader': latest['leader'], 'leaders': latest['leaders'], 'streak': 0,
              'left_censored': False, 'first_cross_date': None, 'confirmed_date': None,
              'previous_leader': None, 'challenger': None, 'gap': 0.0, 'gap_change': None,
              'closing_days': 0, 'status': '공동 1위 · 교체 판정 보류', 'run_start': None}
    if latest['leader'] is None:
        return result
    leader = latest['leader']
    start = len(daily) - 1
    while start > 0 and daily[start - 1]['leader'] == leader:
        start -= 1
    streak = len(daily) - start
    event = next((x for x in events if x['date'] == daily[start]['date']), None)
    challenger = latest['order'][1]
    # Always compare the SAME latest leader/runner-up pair, not yesterday's runner-up.
    gaps = [day['strengths'][leader] - day['strengths'][challenger] for day in daily]
    closing_days = 0
    for i in range(len(gaps) - 1, 0, -1):
        if gaps[i] >= 0 and gaps[i-1] > gaps[i] + TIE_TOLERANCE:
            closing_days += 1
        else:
            break
    if event and event['kind'] == 'cross':
        status = f'교체 유지 확인 · {confirmation_days}거래일 기준' if streak >= confirmation_days else '순위 역전 · 유지 확인 중'
    elif event:
        status = '공동 1위 해소 · 단독 1위 유지' if streak >= confirmation_days else '공동 1위 해소 · 유지 확인 중'
    else:
        status = '1위 유지 · 최초 역전일은 관찰구간 이전'
    result.update({'streak': streak, 'left_censored': start == 0, 'run_start': daily[start]['date'],
                   'first_cross_date': event['date'] if event and event['kind'] == 'cross' else None,
                   'confirmed_date': event['confirmed_date'] if event else None,
                   'previous_leader': event['from'] if event else None,
                   'challenger': challenger, 'gap': gaps[-1], 'gap_change': gaps[-1] - gaps[-2],
                   'closing_days': closing_days, 'status': status})
    return result


def build_rotation(histories, cutoff, sectors=None):
    """Require all fixed sectors on the same 81 index dates for 61 rolling-20 values.

    A missing leader must not make a different sector a fake new leader. Unlike the
    overview chart, incomplete coverage suspends the ENTIRE transition detector.
    """
    sectors = SECTORS if sectors is None else sectors
    try:
        index = clean_closes(histories.get('KOSPI', []), cutoff)
        dates = sorted(index)[-81:]
        if len(dates) < 81:
            raise ValueError('20거래일 강도 이력 계산에 필요한 시장 일봉 81개 부족')
        if (date.fromisoformat(cutoff) - date.fromisoformat(dates[-1])).days > 14:
            raise ValueError('시장 기준일이 너무 오래됨')
        baskets = {}
        for sector, members in sectors.items():
            closes = []
            for code, name in members.items():
                values = clean_closes(histories.get(code, []), cutoff)
                if any(day not in values for day in dates):
                    raise ValueError(f'{sector} / {name}: 동일 거래일 일봉 81개 확인 필요')
                values = [values[day] for day in dates]
                if any(abs(values[i] / values[i-1] - 1) > .40 for i in range(1, len(values))):
                    raise ValueError(f'{sector} / {name}: 급변·권리변동 확인 필요')
                closes.append(values)
            if not closes:
                raise ValueError(f'{sector}: 대표 종목 없음')
            basket = [100.0]
            for i in range(1, 81):
                change = sum(v[i] / v[i-1] - 1 for v in closes) / len(closes)
                basket.append(basket[-1] * (1 + change))
            baskets[sector] = basket
        observations = []
        for i in range(20, 81):
            bench = percent(index[dates[i]], index[dates[i-20]])
            observations.append({'date': dates[i], 'strengths': {
                name: percent(values[i], values[i-20]) - bench for name, values in baskets.items()}})
        return analyze_rotation(observations)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {'valid': False, 'reason': str(exc), 'events': [], 'series': []}
