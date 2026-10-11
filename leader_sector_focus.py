"""Sector-first, read-only candidate scope. Never rewrites saved projects."""
from datetime import date, datetime
from math import isfinite

from leader_roadmap import comparison_group
from sector_observation import SECTORS, KST
from sector_rotation import TIE_TOLERANCE
from strategy_projects import candidate_report
from watch_sector_context import BUSINESSES


def sector_focus(snapshot, now=None):
    now = now or datetime.now(KST)
    held = {'valid': False, 'groups': [], 'reason': '주도업종 자료 확인 필요', 'others': []}
    try:
        day = date.fromisoformat(snapshot['asof'])
        local = now.astimezone(KST)
        if not 0 <= (local.date() - day).days <= 14 or (day == local.date() and local.hour < 16):
            raise ValueError('최근 확정 종가의 주도업종 자료 확인 필요')
        rotation = snapshot['rotation']
        if not rotation['valid'] or rotation['asof'] != snapshot['asof']:
            raise ValueError('전체 업종 순위 확인 전에는 주도업종을 대신 선정하지 않습니다.')
        latest = rotation['daily'][-1]
        strengths = latest['strengths']
        rows = {r['sector']: r for r in snapshot['rows']}
        if (latest['date'] != snapshot['asof'] or set(rows) != set(SECTORS)
                or len(snapshot['rows']) != len(SECTORS) or set(strengths) != set(SECTORS)):
            raise ValueError('10개 업종 전체의 동일 기준일 자료 확인 필요')
        for name, row in rows.items():
            values = [float(row[k]) for k in ('r20', 'r60', 'excess20', 'excess60')]
            if not all(isfinite(v) for v in values) or not isfinite(strengths[name]):
                raise ValueError('업종 강도 숫자 확인 필요')
            if abs(values[2] - strengths[name]) > 1e-6:
                raise ValueError('업종 차트와 주도업종 순위의 기준 불일치')
        order = sorted(rows, key=lambda n: (-strengths[n], n))
        top = strengths[order[0]]
        groups = [n for n in order if top - strengths[n] <= TIE_TOLERANCE]
        if top <= TIE_TOLERANCE:
            raise ValueError('1위도 KOSPI보다 약합니다. 주도업종 후보 선정을 보류합니다.')
        # A tied winner is still first; never promote a runner-up to fill a quota.
        status = ('공동 1위' if len(groups) > 1 else
                  '1위 유지 확인' if rotation.get('streak', 0) >= rotation.get('confirmation_days', 3)
                  else '현재 1위 · 유지 확인 중')
        return {'valid': True, 'groups': groups, 'asof': snapshot['asof'], 'status': status,
                'reason': '', 'rows': rows,
                'others': [rows[n] for n in order if n not in groups and strengths[n] > TIE_TOLERANCE]}
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        return {**held, 'reason': str(exc) if isinstance(exc, ValueError) else held['reason']}


def sector_codes(groups):
    """Existing representative + reviewed mappings only; no theme/name guessing."""
    known = set(BUSINESSES) | {c for members in SECTORS.values() for c in members}
    return tuple(sorted(c for c in known if comparison_group(c) in groups))


def focused_candidates(source, focus, project, now=None):
    now = now or datetime.now(KST)
    held = {'ready': False, 'rows': [], 'reason': '같은 기준일의 주도업종 종목 자료 확인 필요'}
    if not focus.get('valid'):
        return {**held, 'reason': focus.get('reason', held['reason'])}
    if source.get('asof') != focus['asof']:
        return held
    universe = [r for c, r in source.get('items', {}).items()
                if comparison_group(c) in focus['groups']]
    report = candidate_report(source, universe, project, now.astimezone(KST).date().isoformat(), now)
    if not report['ready']:
        return report
    for row in report['rows']:
        group = comparison_group(row['ticker'])
        row['focus_group'] = group
        for n in (20, 60):
            row[f'sector_edge{n}'] = row[f'ret{n}_pct'] - focus['rows'][group][f'r{n}']
    # Rank within the selected sector, before the display cap. Not a probability.
    windows = (20, 60) if project == 'short' else (60, 20)
    report['rows'].sort(key=lambda r: (
        -r[f'sector_edge{windows[0]}'], -r[f'sector_edge{windows[1]}'],
        -r['mean_value20'], r['ticker']))
    return report
