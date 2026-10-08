"""Read public prices only; write a local report. Never publish or send alerts."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.refresh_ma_convergence import fetch_universe, scan_one, reference_date, fetch_benchmarks
from ma_convergence import closed_date_limit, KST
from rise_leaders import leader_candidates, leader_snapshot_ready


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--saved', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    saved = json.loads(args.saved.read_text(encoding='utf-8-sig'))
    asof = reference_date(closed_date_limit())
    universe, coverage = fetch_universe()
    jobs = {row['ticker']: universe[row['ticker']] for row in saved if row['ticker'] in universe}
    items, errors = {}, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {pool.submit(scan_one, row, asof): code for code, row in jobs.items()}
        for n, task in enumerate(as_completed(tasks), 1):
            code = tasks[task]
            try:
                items[code] = task.result()
            except Exception as exc:
                errors.append({'ticker': code, 'error': type(exc).__name__})
            if n % 50 == 0:
                print(f'Checked {n}/{len(jobs)}, failures {len(errors)}', flush=True)
    if len(errors) > len(jobs) * 0.05:
        raise RuntimeError('Incomplete public price retrieval; no new selection')
    snapshot = {'version': 1, 'leader_version': 1, 'complete': True, 'pending': None,
        'asof': asof, 'updated_at': datetime.now(KST).isoformat(timespec='seconds'),
        'source': 'Naver daily OHLCV; close * volume is approximate turnover',
        'benchmarks': fetch_benchmarks(asof), 'items': items, 'errors': errors,
        'coverage': coverage, 'saved_count': len(saved), 'ordinary_saved_count': len(jobs)}
    if not leader_snapshot_ready(snapshot):
        raise RuntimeError('Benchmark data unavailable; no new selection')
    selected = leader_candidates(snapshot, saved)
    snapshot['qualified_count'] = len(selected)
    snapshot['selected_preview'] = selected[:20]
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'asof': asof, 'saved': len(saved), 'ordinary': len(jobs), 'qualified': len(selected),
                      'selected': [{k: x[k] for k in ('ticker','name','leader_score','ret20_pct','ret60_pct','excess20_pct','mean_value20')} for x in selected[:20]]}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

