"""Persistent convergence cohort; admission is separate from live price ranking.

No orders, no live-price membership changes, and no deletion of saved watch rows.
Only the closed-bar ordinary-stock scanner may supply automatic admissions.
"""
from copy import deepcopy
import math
import re

from ma_convergence import THRESHOLD_PCT, MIN_DAILY_VALUE, candidate_sort_key
from rise_leaders import LEADER_POLICY, leader_candidates, leader_entry, leader_snapshot_ready

COHORT_PATH = 'data/rise_watch_cohort.json'
COHORT_LIMIT = 20


def eligible_candidates(snapshot, saved_rows):
    """Never infer admission from a quote, an ETF name, or missing scan data."""
    if not snapshot.get('complete') or snapshot.get('pending'):
        return []
    saved = {str(row.get('ticker', '')).zfill(6) for row in saved_rows}
    candidates = []
    seen = set()
    # snapshot.candidates contains only the scanner's ordinary-stock universe.
    # snapshot.items also contains legacy ETFs and must NOT be used for admission.
    for row in snapshot.get('candidates', []):
        code = row.get('ticker', '')
        if code not in saved or code in seen or not re.fullmatch(r'\d{6}', code):
            continue
        if not (row.get('candidate') and row.get('eligible') and row.get('cluster_now')
                and row.get('tradable') is True and row.get('date') == snapshot.get('asof')):
            continue
        try:
            span, price, value = (float(row[k]) for k in ('span_pct', 'close', 'mean_value20'))
            if not all(math.isfinite(x) for x in (span, price, value)):
                continue
            if not (0 <= span <= THRESHOLD_PCT + 1e-10 and price >= 1000 and value >= MIN_DAILY_VALUE):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        seen.add(code)
        candidates.append(dict(row))
    return sorted(candidates, key=candidate_sort_key)


def validate_cohort(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('관찰 대상 설정을 확인할 수 없습니다.')
    active = value.get('active')
    if not isinstance(active, list) or len(active) > COHORT_LIMIT:
        raise ValueError('관찰 대상 수를 확인할 수 없습니다.')
    codes = [x.get('ticker') for x in active if isinstance(x, dict)]
    if len(codes) != len(active) or len(set(codes)) != len(codes) or any(
            not isinstance(x, str) or not re.fullmatch(r'\d{6}', x) for x in codes):
        raise ValueError('관찰 대상 코드가 올바르지 않습니다.')
    if not isinstance(value.get('archived', {}), dict):
        raise ValueError('관찰 보관 기록을 확인할 수 없습니다.')
    if value.get('policy', 'convergence_v1') not in ('convergence_v1', LEADER_POLICY):
        raise ValueError('관찰 선정 기준을 확인할 수 없습니다.')
    return value


def reconcile_cohort(previous, saved_rows, snapshot):
    """Fill vacancies after a completed DAILY scan, preserving all incumbents.

    Widening MAs, weaker conditions, quote failures and a stronger new candidate
    never evict an incumbent. Explicit user retirement/removal is required.
    """
    state = deepcopy(validate_cohort(previous)) if previous else {
        'version': 1, 'active': [], 'archived': {}, 'last_review_asof': ''}
    if not snapshot.get('complete') or snapshot.get('pending'):
        return state
    is_leader = state.get('policy') == LEADER_POLICY
    if is_leader and not leader_snapshot_ready(snapshot):
        return state
    asof = snapshot.get('asof', '')
    if not asof or asof <= state.get('last_review_asof', ''):
        return state
    saved = {str(row.get('ticker', '')).zfill(6) for row in saved_rows}
    kept = []
    for row in state['active']:
        if row['ticker'] in saved:
            kept.append(row)
        else:
            state['archived'][row['ticker']] = {**row, 'archived_asof': asof,
                                              'archive_reason': '사용자가 저장 목록에서 삭제'}
    state['active'] = kept
    seen = {x['ticker'] for x in kept} | set(state['archived'])
    candidates = leader_candidates(snapshot, saved_rows) if is_leader else eligible_candidates(snapshot, saved_rows)
    for row in candidates:
        if len(state['active']) >= COHORT_LIMIT:
            break
        if row['ticker'] in seen:
            continue
        entry = leader_entry(row, asof) if is_leader else {
            'ticker': row['ticker'], 'name': row['name'], 'market': row['market'],
            'entry_asof': asof, 'entry_span_pct': row['span_pct'],
            'entry_reason': '보통주 · 5·20·60일선 간격 3% 이내 · 1,000원 이상 · 20일 평균 거래대금 5억원 이상',
        }
        state['active'].append(entry)
        seen.add(row['ticker'])
    state['last_review_asof'] = asof
    return state


def reselect_leaders(previous, saved_rows, snapshot):
    """Explicit one-time user-authorized recomposition; never called by timers.

    Preserve the previous membership/reasons in history and all saved rows.
    Manual retirements remain excluded. Daily maintenance only fills vacancies.
    """
    state = deepcopy(validate_cohort(previous))
    if not leader_snapshot_ready(snapshot):
        raise ValueError('완료된 주도주·시장지수 자료가 필요합니다.')
    asof = snapshot['asof']
    if asof < state.get('last_review_asof', ''):
        raise ValueError('이전 기준일 자료로 관찰 대상을 변경할 수 없습니다.')
    migration_key = f'{LEADER_POLICY}:{asof}'
    if state.get('reselection_key') == migration_key:
        return state
    candidates = [row for row in leader_candidates(snapshot, saved_rows)
                  if row['ticker'] not in state.get('archived', {})][:COHORT_LIMIT]
    if not candidates:
        raise ValueError('주도주 조건 충족 종목이 없어 기존 관찰 대상을 유지합니다.')
    state.setdefault('selection_history', []).append({
        'policy': state.get('policy', 'convergence_v1'), 'active': deepcopy(state['active']),
        'last_review_asof': state.get('last_review_asof', ''),
        'changed_asof': asof, 'reason': '사용자 요청: 주도력 우선, 수렴 참고로 재구성',
    })
    state.update(policy=LEADER_POLICY, active=[leader_entry(row, asof) for row in candidates],
                 selection_asof=asof, last_review_asof=asof, reselection_key=migration_key)
    return validate_cohort(state)


def retire_member(previous, ticker, stamp):
    state = deepcopy(validate_cohort(previous))
    member = next((x for x in state['active'] if x['ticker'] == ticker), None)
    if member is None:
        raise ValueError('이미 관찰 종료되었거나 목록이 변경되었습니다. 새로고침해 주세요.')
    state['active'] = [x for x in state['active'] if x['ticker'] != ticker]
    state['archived'][ticker] = {**member, 'archived_asof': stamp,
                               'archive_reason': '사용자가 관찰 종료 · 원본 기록 보존'}
    return state


def active_rows(saved_rows, cohort):
    validate_cohort(cohort)
    saved = {str(x.get('ticker', '')).zfill(6): x for x in saved_rows}
    return [{**saved[x['ticker']], 'watch_entry': dict(x)} for x in cohort['active'] if x['ticker'] in saved]


