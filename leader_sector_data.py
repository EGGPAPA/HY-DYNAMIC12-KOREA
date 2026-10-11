"""Fetch closed data for the mapped leading-sector universe, not saved top 20.

Reuses the existing Naver scanner's read-only feeds, listing/trade-status checks
and price calculations. Cached by closed date; no remote/local state writes.
"""
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from ma_convergence import analyze_bars
from rise_leaders import leader_snapshot_ready


@st.cache_data(ttl=3600, show_spinner=False)
def _listing(revision=0):
    from scripts.refresh_ma_convergence import fetch_universe
    return fetch_universe()[0]


def build_focus_source(codes, asof, listing, histories, benchmarks):
    items, excluded = {}, []
    for code in codes:
        row = listing.get(code)
        if not row or row.get('ordinary') is not True or row.get('tradable') is not True:
            excluded.append({'코드': code, '사유': '상장 보통주·거래상태 확인 필요'})
            continue
        try:
            bars = histories.get(code, [])
            values = [float(b['close']) for b in sorted(bars, key=lambda b: b['date']) if b['date'] <= asof]
            if any(abs(b / a - 1) > .40 for a, b in zip(values, values[1:]) if a > 0):
                raise ValueError('가격 급변·권리변동 확인 필요')
            metrics = analyze_bars(bars, asof)
            if not metrics['eligible']:
                raise ValueError(metrics.get('reason', '일봉 확인 필요'))
            items[code] = {**row, **metrics}
        except (ValueError, TypeError, KeyError, OverflowError):
            excluded.append({'코드': code, '사유': '동일 기준일 일봉·거래량·권리변동 확인 필요'})
    source = {'asof': asof, 'complete': True, 'pending': False, 'leader_version': 1,
              'items': items, 'benchmarks': benchmarks, 'requested_count': len(codes), 'excluded': excluded}
    if not leader_snapshot_ready(source):
        raise ValueError('같은 기준일의 KOSPI·KOSDAQ 종가 자료 확인 필요')
    return source


@st.cache_data(ttl=3600, max_entries=24, show_spinner=False)
def load_focus_source(codes, asof, revision=0):
    from scripts.refresh_ma_convergence import fetch_bars, fetch_benchmarks
    listing = _listing(revision)
    def fetch(code):
        if code not in listing or listing[code].get('tradable') is not True:
            return code, []
        try:
            return code, fetch_bars(code)
        except Exception:
            return code, []  # No provider response or credential detail in UI.
    with ThreadPoolExecutor(max_workers=4) as pool:
        histories = dict(pool.map(fetch, codes))
    source = build_focus_source(codes, asof, listing, histories, fetch_benchmarks(asof))
    if not source['items']:
        raise ValueError('주도업종 종목의 확정 일봉을 확인하지 못했습니다.')
    return source
