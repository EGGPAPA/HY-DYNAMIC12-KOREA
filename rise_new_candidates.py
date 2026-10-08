"""Read-only view of the latest completed daily scan's newly added records."""
from datetime import date
import re

from ma_convergence import DAILY_ADD_LIMIT


def recent_discoveries(saved_rows, snapshot, cohort=None, *, today):
    result = {'state': 'unavailable', 'asof': '', 'rows': [], 'unverified': 0}
    if not isinstance(snapshot, dict) or not snapshot:
        return result
    if not snapshot.get('complete') or snapshot.get('pending'):
        return {**result, 'state': 'pending'}
    asof = snapshot.get('asof', '')
    try:
        if date.fromisoformat(asof) > date.fromisoformat(today):
            return result
    except (ValueError, TypeError):
        return result
    added = snapshot.get('added_this_run')
    if not isinstance(added, list):
        return result
    result.update(state='ready', asof=asof)
    saved = {str(x.get('ticker', '')).zfill(6): x for x in saved_rows}
    active = {x['ticker'] for x in (cohort or {}).get('active', [])}
    archived = set((cohort or {}).get('archived', {}))
    seen = set()
    for code in added:
        if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
            result['unverified'] += 1
            continue
        if code in seen:
            continue
        seen.add(code)
        row = saved.get(code)
        if row is None:
            # Respect later user deletion; never recreate saved rows from a report.
            continue
        if row.get('source') != 'ma_convergence_daily' or row.get('added_asof') != asof:
            result['unverified'] += 1
            continue
        status = ('관찰 여부 확인 중' if cohort is None else
                  '현재 관찰 중' if code in active else '관찰 종료·보관' if code in archived else '새 후보·대기')
        result['rows'].append({**row, 'discovered_asof': asof, 'discovery_status': status})
        if len(result['rows']) >= DAILY_ADD_LIMIT:
            break
    return result
