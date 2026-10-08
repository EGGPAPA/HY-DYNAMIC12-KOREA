"""Persistent convergence cohort; admission is separate from live price ranking.

No orders, no live-price membership changes, and no deletion of saved watch rows.
Only the closed-bar ordinary-stock scanner may supply automatic admissions.
"""
from copy import deepcopy
import math
import re

from ma_convergence import THRESHOLD_PCT, MIN_DAILY_VALUE, candidate_sort_key

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
    for row in eligible_candidates(snapshot, saved_rows):
        if len(state['active']) >= COHORT_LIMIT:
            break
        if row['ticker'] in seen:
            continue
        state['active'].append({
            'ticker': row['ticker'], 'name': row['name'], 'market': row['market'],
            'entry_asof': asof, 'entry_span_pct': row['span_pct'],
            'entry_reason': '보통주 · 5·20·60일선 간격 3% 이내 · 1,000원 이상 · 20일 평균 거래대금 5억원 이상',
        })
        seen.add(row['ticker'])
    state['last_review_asof'] = asof
    return state


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
