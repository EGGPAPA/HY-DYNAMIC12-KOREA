"""A+B background observations using the exact current-screen KIS evaluator.

No order API. Notification failures remain retryable; only confirmed delivery is
recorded. --dry-run never writes remote state or sends a message.
"""
import argparse
import base64
import json
import os
import sys
from datetime import datetime, time
from pathlib import Path
from time import sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from early_watch_data import load_group_inputs
from rise_live_analysis import KST, MAX_QUOTE_AGE, entry_signal, evaluate_batches
from rise_watch_policy import reference_day, watch_groups

REPO = 'EGGPAPA/HY-DYNAMIC12-KOREA'
STATE_PATH = 'data/rise_timing_live.json'
STATE_BRANCH = 'monitor-state'
API = f'https://api.github.com/repos/{REPO}/contents/{STATE_PATH}'
APP_URL = 'https://hy-dynamic12-korea-nfxvcb3ntgddwdeydldbsb.streamlit.app/'
POLICY_VERSION = 'kis_ab_v2'


def market_open(now):
    now = now.astimezone(KST)
    return now.weekday() < 5 and time(9) <= now.time().replace(tzinfo=None) < time(15, 30)


def api_headers():
    return {'Authorization': f"Bearer {os.environ['GITHUB_TOKEN']}",
            'Accept': 'application/vnd.github+json'}


def load_state():
    response = requests.get(API, headers=api_headers(), params={'ref': STATE_BRANCH}, timeout=15)
    if response.status_code == 404:
        return {}, None
    if not response.ok:
        raise RuntimeError('자동감시 상태 조회 실패')
    data = response.json()
    return json.loads(base64.b64decode(data['content'])), data['sha']


def save_state(state, sha):
    payload = {'message': 'Update KIS A+B rise timing observations', 'branch': STATE_BRANCH,
               'content': base64.b64encode(json.dumps(state, ensure_ascii=False,
                    indent=2, allow_nan=False).encode()).decode()}
    if sha:
        payload['sha'] = sha
    response = requests.put(API, headers=api_headers(), json=payload, timeout=20)
    if not response.ok:
        raise RuntimeError('자동감시 상태 저장 실패')


def send_kakao(items):
    required = ('KAKAO_REST_API_KEY', 'KAKAO_REFRESH_TOKEN')
    if any(not os.getenv(key) for key in required):
        raise RuntimeError('카카오 연결정보 없음')
    data = {'grant_type': 'refresh_token', 'client_id': os.environ[required[0]],
            'refresh_token': os.environ[required[1]]}
    if os.getenv('KAKAO_CLIENT_SECRET'):
        data['client_secret'] = os.environ['KAKAO_CLIENT_SECRET']
    response = requests.post('https://kauth.kakao.com/oauth/token', data=data, timeout=15)
    if not response.ok:
        raise RuntimeError('카카오 인증 실패')
    token = response.json().get('access_token')
    if not token:
        raise RuntimeError('카카오 인증 토큰 없음')
    lines = ['[HY 상승시점 A+B 관찰 · KIS]', '필수 4/4 + 보조 1개 이상 · 장중 잠정']
    for x in items:
        lines.append(f"{x.get('watch_group', '')} {x['name']}({x['ticker']}) {x['price']:,.0f}원 · "
                     f"거래량 {x['volume_ratio']:.2f}배 · 보조 {x['auxiliary_count']}/3")
    lines.append('조건 충족 관찰 알림이며 매수 확정·자동 주문이 아닙니다.')
    template = {'object_type': 'text', 'text': '\n'.join(lines),
                'link': {'web_url': APP_URL, 'mobile_web_url': APP_URL}, 'button_title': '관찰 확인'}
    response = requests.post('https://kapi.kakao.com/v2/api/talk/memo/default/send',
        headers={'Authorization': 'Bearer ' + token},
        data={'template_object': json.dumps(template, ensure_ascii=False)}, timeout=15)
    if not response.ok or response.json().get('result_code') != 0:
        raise RuntimeError('카카오 전송 실패 · 다음 실행에서 조건 재확인 후 재시도')


def serializable_results(results, rows):
    metadata = {row['ticker']: row for row in rows}
    return [{**{k: v for k, v in x.items() if k != 'chart'},
             'watch_group': metadata[x['ticker']].get('watch_group', ''),
             'watch_status': metadata[x['ticker']].get('watch_status', '')} for x in results]


def deliver_candidates(items, sent, send, clock):
    """At most once per code/session after confirmed delivery; recheck failed ones next run."""
    confirmed = set(sent)
    candidates = [x for x in items if entry_signal(x)
                  and f"{x['analysis_day']}:{x['ticker']}" not in confirmed]
    error = ''
    for start in range(0, len(candidates), 5):
        now = clock()
        batch = []
        for x in candidates[start:start + 5]:
            try:
                age = (now - datetime.fromisoformat(x['quote_received_at'])).total_seconds()
            except (TypeError, ValueError, KeyError):
                continue
            if market_open(now) and x['analysis_day'] == now.date().isoformat() and 0 <= age <= MAX_QUOTE_AGE:
                batch.append(x)
        if not batch:
            continue
        try:
            send(batch)
        except Exception:
            error = '카카오 전송 실패 · 조건 재확인 후 재시도'
            break
        confirmed.update(f"{x['analysis_day']}:{x['ticker']}" for x in batch)
    return sorted(confirmed), error


def run(dry_run=False, force=False):
    from rise_live_data import get_current_quotes, get_histories, get_market_context
    clock = lambda: datetime.now(KST)
    now = clock()
    previous, sha = ({}, None) if dry_run else load_state()
    state = {**previous, 'version': POLICY_VERSION, 'last_attempt_at': now.isoformat(),
             'interval_minutes': 15, 'notification_error': '', 'error': ''}
    try:
        if not market_open(now) and not force:
            state.update(status='outside_session', history={})
        else:
            context = get_market_context()
            if context.get('error'):
                raise RuntimeError('KIS 거래일·인증정보 확인 필요')
            token = os.getenv('GITHUB_TOKEN', '')
            saved, trend, snapshot = load_group_inputs(token)
            groups = watch_groups(saved, trend, snapshot, now, reference_day(context))
            if groups['error']:
                raise RuntimeError(groups['error'])
            rows = groups['AB']
            histories = get_histories(rows)
            # Start each scheduled invocation with fresh evidence. Two current KIS
            # observations replace the old accumulated historical-date criterion.
            observations = {}
            fetch = lambda batch: get_current_quotes([r['ticker'] for r in batch], refresh_seconds=10)
            results = evaluate_batches(rows, histories, context, fetch, clock, observations)
            if context.get('intraday'):
                sleep(11)
                context = get_market_context()
                results = evaluate_batches(rows, histories, context, fetch, clock, observations)
            items = serializable_results(results, rows)
            state.update(updated_at=clock().isoformat(), selection_asof=groups['asof'],
                         target_count=len(rows), group_counts={k: len(groups[k]) for k in ('A', 'B')},
                         evaluated_count=sum(x['valid'] for x in items), items=items, history=observations,
                         status='ok' if all(x['valid'] for x in items) else 'partial')
            if all(x['valid'] for x in items):
                state['last_success_at'] = state['updated_at']
            day = now.date().isoformat()
            sent = [x for x in previous.get('sent', []) if x.startswith(day + ':')]
            if not dry_run:
                state['sent'], state['notification_error'] = deliver_candidates(items, sent, send_kakao, clock)
    except Exception as exc:
        # Never log provider responses, request headers or credentials.
        safe = str(exc) if isinstance(exc, RuntimeError) else '자동감시 자료 조회 실패'
        state.update(status='error', error=safe, history={}, items=[])
    if not dry_run:
        save_state(state, sha)
    print(json.dumps({k: state.get(k) for k in ('version', 'status', 'target_count',
          'evaluated_count', 'last_success_at', 'notification_error', 'error')}, ensure_ascii=False))
    if state['status'] == 'error' or state['notification_error']:
        raise RuntimeError('자동감시 실행 미완료 · 저장된 운영상태 확인 필요')
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(dry_run=args.dry_run, force=os.getenv('FORCE_RUN', '').lower() in {'1', 'true'})
