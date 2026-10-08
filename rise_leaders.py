"""Descriptive leader-candidate screen, not a forecast or buy recommendation.

Only saved ordinary stocks are eligible. Selection uses completed daily bars;
live entry conditions remain separate. No network, orders or credential access.
"""
from bisect import bisect_left, bisect_right
import math
import re

LEADER_POLICY = 'leader_v1'
MIN_LEADER_VALUE = 2_000_000_000  # 20-session average close * volume (approximation)


def strength_metrics(closes):
    """Called only after the daily scanner validates 65 ordered positive closes."""
    return {
        'strength_version': 1,
        'ret10_pct': (closes[-1] / closes[-11] - 1) * 100,
        'ret20_pct': (closes[-1] / closes[-21] - 1) * 100,
        'ret60_pct': (closes[-1] / closes[-61] - 1) * 100,
        'ma20_prev5': sum(closes[-25:-5]) / 20,
    }


def finite_fields(row, keys):
    try:
        values = [float(row[k]) for k in keys]
        return values if all(math.isfinite(x) for x in values) else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def leader_snapshot_ready(snapshot):
    if (not snapshot.get('complete') or snapshot.get('pending')
            or snapshot.get('leader_version') != 1 or not isinstance(snapshot.get('items'), dict)):
        return False
    asof = snapshot.get('asof', '')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(asof)):
        return False
    for market in ('KOSPI', 'KOSDAQ'):
        benchmark = snapshot.get('benchmarks', {}).get(market, {})
        if (not benchmark.get('eligible') or benchmark.get('date') != asof
                or benchmark.get('strength_version') != 1
                or finite_fields(benchmark, ('ret20_pct', 'ret60_pct')) is None):
            return False
    return True


def leader_candidates(snapshot, saved_rows):
    """Rank by excess-return and liquidity percentiles within saved ordinary stocks.

    Admission requires positive 20/60 returns, outperformance of the matching
    KOSPI/KOSDAQ index at both horizons, rising MA20 and close > MA20 > MA60.
    Convergence is a reference only. ETFs, stale bars and unknown trade status
    cannot enter. Do not fill the quota with failing stocks.
    """
    if not leader_snapshot_ready(snapshot):
        return []
    population = []
    seen = set()
    for saved in saved_rows:
        code = str(saved.get('ticker', '')).zfill(6)
        row = snapshot['items'].get(code, {})
        if (code in seen or not re.fullmatch(r'\d{6}', code) or row.get('ticker') != code
                or row.get('ordinary') is not True or row.get('tradable') is not True
                or not row.get('eligible') or row.get('strength_version') != 1
                or row.get('date') != snapshot['asof']):
            continue
        values = finite_fields(row, ('close', 'ma20', 'ma60', 'ma20_prev5',
                                    'ret10_pct', 'ret20_pct', 'ret60_pct', 'mean_value20'))
        if values is None:
            continue
        price, ma20, ma60, old_ma20, ret10, ret20, ret60, value20 = values
        if min(price, ma20, ma60, old_ma20, value20) <= 0:
            continue
        benchmark = snapshot['benchmarks'].get(row.get('market'))
        if benchmark is None:
            continue
        seen.add(code)
        excess20 = ret20 - float(benchmark['ret20_pct'])
        excess60 = ret60 - float(benchmark['ret60_pct'])
        gap20 = (price / ma20 - 1) * 100
        population.append({**row, 'excess20_pct': excess20, 'excess60_pct': excess60,
            'leader_eligible': (price >= 1000 and value20 >= MIN_LEADER_VALUE
                and price > ma20 > ma60 and ma20 > old_ma20
                and ret20 > 0 and ret60 > 0 and excess20 > 0 and excess60 > 0),
            'leader_overheated': gap20 > 15 or ret20 > 35 or ret10 > 25})
    fields = ('excess20_pct', 'excess60_pct', 'mean_value20')
    samples = {key: sorted(float(row[key]) for row in population) for key in fields}
    def percentile(key, value):
        sample = samples[key]
        if len(sample) == 1:
            return 50.0
        midpoint = (bisect_left(sample, value) + bisect_right(sample, value) - 1) / 2
        return 100 * midpoint / (len(sample) - 1)
    result = []
    for row in population:
        if not row['leader_eligible']:
            continue
        score = sum(weight * percentile(key, float(row[key]))
                    for key, weight in zip(fields, (0.45, 0.25, 0.30)))
        result.append({**row, 'leader_score': round(score, 4), 'leader_population': len(population)})
    return sorted(result, key=lambda x: (-x['leader_score'], -x['mean_value20'], x['ticker']))


def leader_entry(row, asof):
    return {
        'ticker': row['ticker'], 'name': row['name'], 'market': row['market'],
        'entry_asof': asof, 'entry_policy': LEADER_POLICY,
        'entry_span_pct': row.get('span_pct'),
        'entry_reason': '보통주 · 상승 추세 · 20·60거래일 시장지수 초과 상승 · 20일 평균 거래대금 약 20억원 이상 · 수렴은 참고',
        'entry_leader_score': row['leader_score'],
        'entry_ret20_pct': row['ret20_pct'], 'entry_ret60_pct': row['ret60_pct'],
        'entry_excess20_pct': row['excess20_pct'], 'entry_excess60_pct': row['excess60_pct'],
        'entry_mean_value20': row['mean_value20'],
        'entry_overheated': row['leader_overheated'],
    }

