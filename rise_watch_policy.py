"""Shared, read-only A/B membership and freshness rules for UI and monitor."""
from datetime import date, datetime

from dual_leader_watch import early_candidates
from rise_leaders import leader_candidates, leader_snapshot_ready
from rise_live_analysis import KST

MAX_SELECTION_AGE_DAYS = 4


def reference_day(context):
    """Last confirmed session from the provider; never invent exchange holidays."""
    if not context or context.get('error'):
        return None
    value = context.get('day') if context.get('basis') == 'close' else context.get('previous')
    try:
        return datetime.strptime(value, '%Y%m%d').date().isoformat()
    except (TypeError, ValueError):
        return None


def selection_error(snapshot, now=None, expected_day=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    try:
        ready = leader_snapshot_ready(snapshot)
    except (AttributeError, TypeError, KeyError, ValueError):
        ready = False
    if not ready:
        return '확정 일봉 검증 대기'
    try:
        asof = date.fromisoformat(snapshot['asof'])
    except (TypeError, ValueError):
        return '선정 기준일 확인 필요'
    if asof > now.date() or (asof == now.date() and now.hour < 16):
        return '미확정·미래 일봉으로 선정할 수 없습니다'
    if expected_day:
        if asof.isoformat() != expected_day:
            return f'선정자료 갱신 대기 · 자료 {asof} / 최근 확정 거래일 {expected_day}'
    elif (now.date() - asof).days > MAX_SELECTION_AGE_DAYS:
        return f'선정자료 지연 · 기준일 {asof} · 최근 거래일 확인 필요'
    return ''


def watch_groups(saved_rows, trend_rows, snapshot, now=None, expected_day=None):
    """Keep B incumbents, show their current status, and add disjoint A candidates."""
    error = selection_error(snapshot, now, expected_day)
    leaders = {r['ticker'] for r in leader_candidates(snapshot, saved_rows)} if not error else set()
    trend, seen = [], set()
    items = snapshot.get('items', {})
    if not isinstance(items, dict):
        items = {}
    for row in trend_rows:
        code = row['ticker']
        if code in seen:
            continue
        seen.add(code)
        evidence = items.get(code) or {}
        if not isinstance(evidence, dict):
            evidence = {}
        unknown = error or not evidence.get('eligible') or evidence.get('date') != snapshot.get('asof')
        status = '평가 보류' if unknown else ('상승 지속' if code in leaders else '조건 약화')
        trend.append({**row, 'watch_group': 'B', 'watch_status': status,
                      'selection_asof': snapshot.get('asof', '')})
    early = [] if error else [dict(r, watch_status='상승 초기 후보', selection_asof=snapshot['asof'])
                             for r in early_candidates(snapshot, saved_rows) if r['ticker'] not in seen]
    return {'A': early, 'B': trend, 'AB': early + trend, 'error': error,
            'asof': snapshot.get('asof', '')}


def monitor_health(state, now, context):
    """Health only; stored monitor decisions never replace current screen prices."""
    now = now.astimezone(KST)
    stamp = state.get('last_success_at')
    try:
        last = datetime.fromisoformat(stamp)
        if last.tzinfo is None:
            raise ValueError()
        age = (now - last).total_seconds()
    except (TypeError, ValueError):
        age = None
    intraday = bool(context.get('intraday')) and not context.get('error')
    if state.get('status') == 'error':
        return 'warning', '자동감시 오류 · ' + state.get('error', '자료·설정 확인 필요')
    if state.get('status') == 'partial':
        return 'warning', f"자동감시 일부 평가 보류 · 정상 {state.get('evaluated_count', 0)}/{state.get('target_count', 0)}개"
    if intraday and (age is None or age < 0 or age > 25 * 60):
        return 'warning', f'자동감시 갱신 지연 · 마지막 정상 평가 {stamp or "확인 없음"} · 15분 간격은 보장되지 않습니다'
    if state.get('notification_error'):
        return 'warning', '알림 전송 실패 · 현재 조건을 다시 확인한 뒤 재시도합니다'
    return 'caption', f'자동감시 마지막 정상 평가: {stamp or "확인 없음"} · 대상 A+B · 15분 간격 예약(실행 지연 가능)'
