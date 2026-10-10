"""Independent observation projects and a manual ledger. Never places orders.

Rules v1 are unvalidated screening examples, not expected returns. Closed daily
selection is separate from entry evaluation and from user-recorded executions.
"""
from copy import deepcopy
from datetime import date, datetime
import math
import re
from urllib.parse import urlparse

import pandas as pd

from rise_leaders import finite_fields, leader_snapshot_ready
from rise_live_analysis import KST, evaluate_current, on_hold
from watch_sector_context import dated_snapshot

PROJECTS = {'short': '① 단기 · 며칠~몇 주', 'medium': '② 중기 · 1~3개월'}
RULE_VERSION = 'project_v1'
PROJECT_PATH = 'data/strategy_projects.json'


def positive(value, allow_zero=False):
    if isinstance(value, bool):
        raise ValueError('금액·수량을 숫자로 입력해 주세요.')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError('금액·수량을 숫자로 입력해 주세요.') from None
    if not math.isfinite(value) or value < 0 or (not allow_zero and value == 0):
        raise ValueError('유효한 양수를 입력해 주세요.')
    return value


def valid_code(code):
    return isinstance(code, str) and re.fullmatch(r'\d{6}', code) and code != '000000'


def empty_state():
    return {'version': 1, 'projects': {key: {
        'watch': [], 'archived': [], 'trades': [], 'reviews': {},
        'budget': 0.0, 'risk_pct': 0.5, 'revision': 0,
    } for key in PROJECTS}}


def ledger(trades):
    """Average-cost ledger; sale proceeds/fees and partial closes are preserved."""
    positions, seen = {}, set()
    for trade in trades:
        code = trade.get('ticker')
        if not valid_code(code) or trade.get('side') not in ('buy', 'sell'):
            raise ValueError('매매 기록 형식을 확인해 주세요.')
        uid = trade.get('id')
        if not isinstance(uid, str) or not uid or uid in seen:
            raise ValueError('중복된 매매 기록입니다.')
        seen.add(uid)
        date.fromisoformat(trade['date'])
        q, price, fees = (positive(trade.get('qty')), positive(trade.get('price')),
                          positive(trade.get('fees', 0), True))
        if not q.is_integer():
            raise ValueError('국내 주식 수량은 정수로 입력해 주세요.')
        if trade.get('voided'):
            continue
        p = positions.setdefault(code, {'ticker': code, 'name': trade['name'],
            'qty': 0.0, 'cost': 0.0, 'realized': 0.0, 'last_date': '', 'opened': ''})
        if trade['date'] < p['last_date']:
            raise ValueError('같은 종목은 거래일 순서대로 기록해 주세요.')
        if trade['side'] == 'buy':
            if p['qty'] == 0:
                p['opened'] = trade['date']
            p['qty'] += q
            p['cost'] += q * price + fees
        else:
            if q > p['qty']:
                raise ValueError('기록된 보유수량보다 많이 매도할 수 없습니다.')
            removed = p['cost'] * q / p['qty']
            p['realized'] += q * price - fees - removed
            p['cost'] -= removed
            p['qty'] -= q
            if p['qty'] == 0:
                p['cost'] = 0.0
        p['last_date'] = trade['date']
    return positions


def validate_state(state):
    if not isinstance(state, dict) or state.get('version') != 1:
        raise ValueError('프로젝트 저장 형식을 확인해 주세요.')
    if set(state.get('projects', {})) != set(PROJECTS):
        raise ValueError('단기·중기 프로젝트 구성이 올바르지 않습니다.')
    for p in state['projects'].values():
        if not isinstance(p, dict) or not isinstance(p.get('trades'), list):
            raise ValueError('프로젝트 거래 기록을 확인해 주세요.')
        watch = p.get('watch')
        if not isinstance(watch, list) or len(watch) > 20:
            raise ValueError('프로젝트 관찰목록은 최대 20개입니다.')
        codes = [x.get('ticker') for x in watch if isinstance(x, dict)]
        if len(codes) != len(watch) or len(set(codes)) != len(codes) or not all(valid_code(x) for x in codes):
            raise ValueError('프로젝트 종목코드를 확인해 주세요.')
        if any(not isinstance(x.get('name'), str) or not x['name'] or x.get('market') not in ('KOSPI','KOSDAQ') for x in watch):
            raise ValueError('종목명·시장을 확인해 주세요.')
        if not isinstance(p.get('archived'), list) or not isinstance(p.get('reviews'), dict):
            raise ValueError('프로젝트 보관·검토 기록을 확인해 주세요.')
        for code, review in p['reviews'].items():
            if not valid_code(code) or not isinstance(review, dict):
                raise ValueError('종목 검토 기록을 확인해 주세요.')
            positive(review.get('stop', 0), True)
            date.fromisoformat(review['next_review'])
            date.fromisoformat(review['checked_on'])
            if not isinstance(review.get('thesis'), str) or not isinstance(review.get('source'), str):
                raise ValueError('검토 근거·주소를 확인해 주세요.')
        positive(p.get('budget'), True)
        if not .1 <= positive(p.get('risk_pct')) <= 2:
            raise ValueError('거래당 계획 손실률은 0.1~2%로 입력해 주세요.')
        positions = ledger(p.get('trades', []))
        if any(x['qty'] > 0 and code not in codes for code, x in positions.items()):
            raise ValueError('보유 기록이 있는 종목은 관찰목록에 유지해야 합니다.')
    return state


def change_state(previous, project, action, payload, today):
    """All mutations are explicit actions; timers never call this function."""
    state = deepcopy(validate_state(previous))
    if project not in PROJECTS:
        raise ValueError('프로젝트를 선택해 주세요.')
    p = state['projects'][project]
    if action == 'settings':
        p['budget'] = positive(payload['budget'], True)
        p['risk_pct'] = positive(payload['risk_pct'])
    elif action == 'add':
        seen = {x['ticker'] for x in p['watch']}
        for row in payload['rows']:
            code = row['ticker']
            if code not in seen:
                p['watch'].append({k: row[k] for k in ('ticker', 'name', 'market')})
                p['watch'][-1].update(added_on=today, selection_asof=payload['asof'], rule=RULE_VERSION)
                seen.add(code)
    elif action == 'archive':
        code = payload['ticker']
        if ledger(p['trades']).get(code, {}).get('qty', 0) > 0:
            raise ValueError('보유 기록이 있습니다. 실제 매도 내역을 기록한 뒤 관찰을 종료해 주세요.')
        row = next((x for x in p['watch'] if x['ticker'] == code), None)
        if row is None:
            raise ValueError('이미 관찰이 종료되었거나 목록이 변경되었습니다.')
        p['archived'].append({**row, 'archived_on': today})
        p['watch'] = [x for x in p['watch'] if x['ticker'] != code]
    elif action == 'review':
        code = payload['ticker']
        if code not in {x['ticker'] for x in p['watch']}:
            raise ValueError('관찰 중인 종목을 선택해 주세요.')
        review = deepcopy(payload)
        stop = positive(review.get('stop', 0), True)
        if review.get('fundamental_ok') and (not review.get('thesis', '').strip()
                or urlparse(review.get('source', '')).scheme not in ('https', 'http')
                or not urlparse(review.get('source', '')).netloc):
            raise ValueError('실적·공시 확인에는 매수 근거와 확인한 자료의 주소가 필요합니다.')
        due = date.fromisoformat(review['next_review'])
        if due < date.fromisoformat(today):
            raise ValueError('다음 점검일은 오늘 이후로 입력해 주세요.')
        review.update(stop=stop, checked_on=today)
        p['reviews'][code] = review
    elif action == 'trade':
        trade = deepcopy(payload)
        row = next((x for x in p['watch'] if x['ticker'] == trade.get('ticker')), None)
        if row is None:
            raise ValueError('먼저 프로젝트 관찰목록에 종목을 추가해 주세요.')
        if any(x['id'] == trade.get('id') for x in p['trades']):
            return state  # retry of the same explicitly submitted record
        if date.fromisoformat(trade['date']) > date.fromisoformat(today):
            raise ValueError('미래 거래는 기록할 수 없습니다.')
        trade.update(name=row['name'], recorded_on=today)
        p['trades'].append(trade)
        ledger(p['trades'])
    elif action == 'void_trade':
        trade = next((x for x in p['trades'] if x['id'] == payload['id'] and not x.get('voided')), None)
        if trade is None or not payload.get('reason', '').strip():
            raise ValueError('유효한 기록과 정정 사유를 확인해 주세요.')
        trade.update(voided=True, voided_on=today, void_reason=payload['reason'].strip())
        ledger(p['trades'])  # Cannot void a buy that is required by a later sell.
    else:
        raise ValueError('지원하지 않는 변경입니다.')
    p['revision'] += 1
    state['updated_on'] = today
    return validate_state(state)


def candidate_report(snapshot, saved_rows, project, today, now=None):
    now = now or datetime.now(KST)
    if (project not in PROJECTS or not isinstance(snapshot, dict)
            or not leader_snapshot_ready(snapshot) or not dated_snapshot(snapshot, today)
            or (snapshot.get('asof') == today and now.astimezone(KST).hour < 16)):
        return {'ready': False, 'rows': [], 'reason': '완료된 최근 종가·시장지수 자료 확인 필요'}
    # A completed daily scan is not permission to include today's unfinished bar.
    asof = snapshot['asof']
    pool, seen = [], set()
    keys = ('close', 'ma20', 'ma60', 'ma20_prev5', 'ret10_pct', 'ret20_pct', 'ret60_pct', 'mean_value20')
    for saved in saved_rows:
        code = str(saved.get('ticker', '')).zfill(6)
        row = snapshot['items'].get(code, {})
        values = finite_fields(row, keys)
        if code in seen or not valid_code(code) or values is None or row.get('date') != asof:
            continue
        if not (row.get('ticker') == code and row.get('ordinary') is True and row.get('tradable') is True
                and row.get('eligible') and row.get('strength_version') == 1):
            continue
        benchmark = snapshot['benchmarks'].get(row.get('market'), {})
        bv = finite_fields(benchmark, ('ret20_pct', 'ret60_pct'))
        if bv is None:
            continue
        seen.add(code)
        price, m20, m60, old20, r10, r20, r60, value = values
        if min(price, m20, m60, old20, value) <= 0:
            continue
        e20, e60 = r20 - bv[0], r60 - bv[1]
        common = price >= 1000 and value >= 2_000_000_000 and price > m20 and m20 > old20
        eligible = common and r10 > 0 and r20 > 0 and e20 > 0
        if project == 'medium':
            eligible = common and m20 > m60 and r20 > 0 and r60 > 0 and e20 > 0 and e60 > 0
        if eligible:
            pool.append({**row, 'ticker': code, 'name': saved.get('name', code),
                'excess20': e20, 'excess60': e60,
                'overheated': price / m20 > 1.15 or r10 > 25 or r20 > 35})
    fields = (('ret10_pct', .4), ('excess20', .4), ('mean_value20', .2)) if project == 'short' else (
        ('excess20', .3), ('excess60', .5), ('mean_value20', .2))
    for row in pool:
        # Percentile ranks within eligible saved stocks, not probabilities.
        row['rank_score'] = sum(weight * 100 * (
            sum(x[key] < row[key] for x in pool) + .5 * (sum(x[key] == row[key] for x in pool) - 1)
        ) / max(1, len(pool) - 1) for key, weight in fields)
    pool.sort(key=lambda x: (-x['rank_score'], -x['mean_value20'], x['ticker']))
    return {'ready': True, 'asof': asof, 'rows': pool, 'reason': ''}


def closed_project_context(history, context):
    if context.get('error') or history is None or history.empty:
        return {'error': '중기 종가 평가를 위한 거래일·일봉 확인 필요'}
    if context.get('basis') == 'close':
        return context
    day = context.get('previous')
    try:
        dates = sorted(set(pd.to_datetime(history.index).strftime('%Y%m%d')))
        earlier = [d for d in dates if d < day]
        if day not in dates or not earlier:
            raise ValueError()
        return {'basis': 'close', 'intraday': False, 'day': day, 'previous': earlier[-1],
                'mode': '중기 · 직전 확정 종가 기준'}
    except (ValueError, TypeError):
        return {'error': '중기 확정 거래일 일봉 누락'}


def technical_view(project, row, history, quote, context, now, review=None):
    review = review or {}
    basis = closed_project_context(history, context) if project == 'medium' else context
    if basis.get('error'):
        return {**on_hold(row, basis['error']), 'breakout_state': '평가 보류', 'pullback_state': '평가 보류'}
    base = evaluate_current(row, history, None if basis.get('basis') == 'close' else quote, basis, now)
    if not base['valid']:
        return {**base, 'breakout_state': '평가 보류', 'pullback_state': '평가 보류'}
    close = base['chart'].iloc[:, 0].astype(float)
    price, m5, m20, m60 = (base[x] for x in ('price', 'ma5', 'ma20', 'ma60'))
    old20 = float(close.iloc[-25:-5].mean())
    old60 = float(close.iloc[-65:-5].mean())
    prior = close.iloc[:-1]
    recent = prior.tail(5)
    peak = float(prior.tail(20).max())
    low = float(prior.tail(10 if project == 'short' else 20).min())
    stop = low * .99
    risk = (price - stop) / price * 100
    limit = 7 if project == 'short' else 12
    trend = price > m20 > m60 and m20 > old20 and (project == 'short' or m60 >= old60)
    hot = base['gap20'] > 15 or (price / close.iloc[-11] - 1) * 100 > 25 or (price / close.iloc[-21] - 1) * 100 > 35
    pullback = (float(recent.min()) <= m20 * 1.03 and peak >= float(recent.min()) * 1.03
                and price > float(prior.iloc[-1]) and price >= m5
                and base['gap20'] <= (6 if project == 'short' else 10))
    break_checks = {'상승 추세': trend, '이전 20일 종가고점 돌파·3% 이내': peak <= price <= peak * 1.03,
                    '거래량 1.5배': base['volume_ratio'] >= 1.5, '손절 참고거리': 0 < risk <= limit}
    pull_checks = {'상승 추세': trend, '조정 후 반등': pullback,
                   '거래량 1.1배': base['volume_ratio'] >= 1.1, '손절 참고거리': 0 < risk <= limit}
    verified = False
    try:
        age = (now.date() - date.fromisoformat(review['checked_on'])).days
        verified = (review.get('fundamental_ok') is True and 0 <= age <= 90
                    and bool(review.get('thesis', '').strip()) and bool(urlparse(review.get('source', '')).netloc))
    except (KeyError, ValueError, TypeError):
        pass
    def label(checks):
        count = sum(checks.values())
        if hot:
            return f'추격 주의 · {count}/4'
        if count < 4:
            return f'대기 · {count}/4'
        if project == 'medium' and not verified:
            return '기술 4/4 · 실적·공시 확인 대기'
        return '검토 4/4 · 장중 잠정' if base['intraday'] else '검토 4/4 · 종가 참고'
    return {**base, 'stop_reference': stop, 'trend_ok': trend, 'fundamental_verified': bool(verified),
            'breakout_state': label(break_checks), 'pullback_state': label(pull_checks),
            'breakout_checks': break_checks, 'pullback_checks': pull_checks,
            'review_stop': review.get('stop', 0)}


def risk_quantity(budget, risk_pct, entry, stop):
    if not all(math.isfinite(float(x)) for x in (budget, risk_pct, entry, stop)):
        return None
    if budget <= 0 or not 0 < risk_pct <= 2 or not 0 < stop < entry:
        return None
    return min(math.floor(budget * risk_pct / 100 / (entry - stop)), math.floor(budget / entry))


def combined_positions(state):
    combined = {}
    for project, p in validate_state(state)['projects'].items():
        for code, pos in ledger(p['trades']).items():
            if pos['qty'] <= 0:
                continue
            item = combined.setdefault(code, {'name': pos['name'], 'ticker': code, 'qty': 0, 'cost': 0, 'projects': []})
            item['qty'] += pos['qty']
            item['cost'] += pos['cost']
            item['projects'].append(project)
    total = sum(x['cost'] for x in combined.values())
    return [{**x, 'cost_weight': 100 * x['cost'] / total if total else 0} for x in combined.values()]
