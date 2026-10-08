"""Current prices evaluate a saved convergence cohort, never reselect its members."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import monotonic

import pandas as pd
import streamlit as st

from rise_live_analysis import KST, evaluate_current, observe_persistence, priority_key
from rise_live_data import get_current_quotes, get_histories, get_market_context

DISPLAY_LIMIT = 20
WATCHLIST_REFRESH_SECONDS = 10
WATCHLIST_SELECTION_SECONDS = 60
WATCHLIST_HIDDEN_COLUMNS = (
    '시세 수신시각(KST)', '평가 기준일', '과거 일봉 마지막', '평가 구분',
    '7조건 확인', '수렴 간격', '1차가 거리', '현재가 평가',
)


def won(value):
    return '—' if value is None else f'{value:,.0f}원'


def _evaluate(rows, namespace):
    now = datetime.now(KST)
    # A new trading date/hour or explicit refresh reloads daily input, not old verdicts.
    revision = st.session_state.get('rise_live_revision', 0)
    key = (now.strftime('%Y%m%d%H'), revision, tuple(x['ticker'] for x in rows))
    saved = st.session_state.get(namespace + '_histories')
    if saved is None or saved['key'] != key:
        notice = st.empty()
        notice.info(f'KIS 일봉 확인 중 · {len(rows):,}개 종목 (최초 조회는 시간이 걸릴 수 있습니다)')
        def progress(done, total):
            notice.info(f'KIS 일봉 확인 중 · {done:,}/{total:,}개')
        histories = get_histories(rows, revision, progress)
        saved = {'key': key, 'histories': histories, 'retry_after': datetime.now(KST).timestamp() + 60}
        st.session_state[namespace + '_histories'] = saved
        notice.empty()
    elif now.timestamp() >= saved.get('retry_after', 0):
        failed = [row for row in rows if saved['histories'].get(row['ticker']) is None
                  or saved['histories'][row['ticker']].empty]
        if failed:
            # Retry only failed feeds; do not freeze an empty cached daily response
            # for an hour or redo all successful histories on every quote update.
            saved['histories'].update(get_histories(failed, (revision, int(now.timestamp() // 60))))
        saved['retry_after'] = datetime.now(KST).timestamp() + 60
    context = get_market_context(revision)
    quotes = get_current_quotes([row['ticker'] for row in rows])
    evaluated_at = datetime.now(KST)
    results = [evaluate_current(row, saved['histories'].get(row['ticker']),
                               quotes.get(row['ticker']), context, evaluated_at) for row in rows]
    states = st.session_state.setdefault('rise_live_observations', {})
    observe_persistence(results, states)
    return sorted(results, key=priority_key), context


@st.cache_resource(show_spinner=False)
def _watchlist_pool():
    # Workers return data only. UI and Session State are touched on the script thread.
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix='watch-selection')


def _build_watch_selection(rows, revision, histories, history_hour, observations):
    now = datetime.now(KST)
    hour = now.strftime('%Y%m%d%H')
    histories = dict(histories)
    missing = rows if history_hour != hour else [
        row for row in rows if histories.get(row['ticker']) is None or histories[row['ticker']].empty]
    if missing:
        histories.update(get_histories(missing, (revision, int(now.timestamp() // 60))))
    context = get_market_context(revision)
    quotes = get_current_quotes([row['ticker'] for row in rows])
    evaluated_at = datetime.now(KST)
    results = [evaluate_current(row, histories.get(row['ticker']), quotes.get(row['ticker']),
                               context, evaluated_at) for row in rows]
    observe_persistence(results, observations)
    return {'results': sorted(results, key=priority_key), 'histories': histories,
            'history_hour': hour, 'context': context, 'observations': observations,
            'completed_at': datetime.now(KST).isoformat(timespec='seconds')}


def _watch_state(rows):
    now = datetime.now(KST)
    revision = st.session_state.get('rise_live_revision', 0)
    key = (now.strftime('%Y%m%d'), revision,
           tuple((row['ticker'], row.get('name'), row.get('market')) for row in rows))
    state = st.session_state.get('rise_watch_fast_state')
    if state is None or state['key'] != key:
        if state and state.get('future'):
            state['future'].cancel()
        # Reuse daily input from the old renderer on a hot deployment, not old verdicts.
        saved = st.session_state.get('rise_current_watch_histories', {})
        saved_key = saved.get('key', ())
        reusable = (len(saved_key) == 3 and saved_key[:2] == (now.strftime('%Y%m%d%H'), revision)
                    and set(saved_key[2]) == {row['ticker'] for row in rows})
        state = {'key': key, 'revision': revision, 'future': None, 'snapshot': None,
                 'histories': dict(saved.get('histories', {})) if reusable else {},
                 'history_hour': now.strftime('%Y%m%d%H') if reusable else None,
                 'observations': {}, 'observed_at': {}, 'next_selection': 0, 'error': ''}
        st.session_state['rise_watch_fast_state'] = state
    future = state['future']
    if future is not None and future.done():
        state['future'] = None
        state['next_selection'] = monotonic() + WATCHLIST_SELECTION_SECONDS
        try:
            snapshot = future.result()  # done() above: never wait for the full scan here.
            state.update(snapshot=snapshot, histories=snapshot['histories'],
                         history_hour=snapshot['history_hour'], error='')
            # A background result cannot roll back a newer visible-row observation.
            for item in snapshot['results']:
                code, stamp = item['ticker'], item.get('quote_received_at')
                if stamp and stamp >= state['observed_at'].get(code, ''):
                    state['observed_at'][code] = stamp
                    if code in snapshot['observations']:
                        state['observations'][code] = dict(snapshot['observations'][code])
                    else:
                        state['observations'].pop(code, None)
        except Exception:
            # Do not expose provider responses, secrets, or traceback values in UI.
            state['error'] = '관찰 종목 일봉 확인 실패 · 대상 종목은 유지하며 현재가 갱신은 계속합니다.'
    return state


def _schedule_watch_selection(state, rows):
    if state['future'] is None and monotonic() >= state['next_selection']:
        state['future'] = _watchlist_pool().submit(
            _build_watch_selection, [dict(row) for row in rows], state['revision'],
            dict(state['histories']), state['history_hour'], deepcopy(state['observations']))


def _evaluate_visible(rows, state):
    # At most 20 quotes: one KIS batch. No daily history or full-universe wait here.
    context = get_market_context(state['revision'])
    quotes = get_current_quotes([row['ticker'] for row in rows], refresh_seconds=WATCHLIST_REFRESH_SECONDS)
    now = datetime.now(KST)
    results = [evaluate_current(row, state['histories'].get(row['ticker']),
                               quotes.get(row['ticker']), context, now) for row in rows]
    observe_persistence(results, state['observations'])
    for item in results:
        # Even a failed receipt supersedes older background observations.
        state['observed_at'][item['ticker']] = item.get('quote_received_at') or now.isoformat(timespec='seconds')
        item['watch_entry'] = next((row.get('watch_entry') for row in rows if row['ticker'] == item['ticker']), None)
    return sorted(results, key=priority_key), context


def _frame(results, *, compact=False):
    rows = []
    for rank, item in enumerate(results, 1):
        good = item['valid']
        checks = item['checks']
        descriptions = [('가격거리', 'gap'), ('누적거래량', 'volume'), ('현재가돌파', 'close'),
                        ('손절위험', 'risk'), ('단계', 'stage'), ('점수', 'score'), ('지속성', 'persistence')]
        rows.append({
            '관찰 우선순위': rank if good else None,
            '종목': item['name'], '코드': item['ticker'], '현재가(KIS)': won(item['price']),
            '필수조건': item['mandatory_label'], '보조조건': item['auxiliary_label'],
            '단계': item['label'], '현재가 평가': item['action'] if good else item['reason'],
            '1차가 거리': f"{item['gap']:+.1f}%" if good else '—',
            '누적거래량/20일평균': f"{item['volume_ratio']:.2f}배" if good else '—',
            '시점점수': item['score'] if good else None,
            '1차 매수 참고': won(item.get('buy1')), '2차 눌림 참고': won(item.get('buy2')),
            '손절 참고': won(item.get('stop')), '돌파 기준': won(item.get('breakout')),
            '20일선 이격': f"{item['gap20']:+.1f}%" if good else '—',
            '세 선 수렴(현재가 반영)': item.get('convergence', '—'),
            '수렴 간격': f"{item['span']:.2f}%" if good else '—',
            '7조건 확인': ' · '.join(f"{name} {'✓' if checks[k] else '×'}" for name, k in descriptions) if good else '평가 보류',
            '시세 수신시각(KST)': item.get('quote_received_at') or '수신 실패',
            '평가 기준일': item.get('analysis_day', '—'), '과거 일봉 마지막': item.get('history_day', '—'),
            '평가 구분': item.get('mode', '보류'),
        })
    frame = pd.DataFrame(rows)
    # Simplify only the personal table; keep calculations and diagnostics intact.
    return frame.drop(columns=list(WATCHLIST_HIDDEN_COLUMNS), errors='ignore') if compact else frame


def _status(results, context, seconds):
    good = sum(x['valid'] for x in results)
    received = [x['quote_received_at'] for x in results if x.get('quote_received_at')]
    stamps = f'{min(received)} ~ {max(received)}' if received else '수신 성공 없음'
    st.caption(f'현재가·누적 거래량: KIS KRX(J) · {seconds}초 간격 조회 요청(통신 지연 가능) · 정상 평가 {good:,}/{len(results):,}개')
    st.caption(f'실제 시세 수신시각(KST): {stamps} · 수신시각은 거래소 체결시각이 아닙니다.')
    if context.get('notice'):
        st.caption(context['notice'])
    if context.get('error'):
        st.warning(context['error'] + ' · 거래일을 확인할 때까지 평가를 보류합니다.')
    elif not context['intraday']:
        st.info(f"정규장 외 참고 평가 · {context['day']} 거래일 기준입니다. 휴장·장 종료 후에는 재조회해도 가격이 같을 수 있습니다.")
    if good < len(results):
        st.warning(f'평가 보류 {len(results) - good:,}개 · 시세/거래량/직전 거래일 일봉이 확인되지 않은 종목에는 점수와 매수 조건을 표시하지 않습니다.')
        with st.expander('평가 보류 사유 확인'):
            st.dataframe(_frame([x for x in results if not x['valid']]), hide_index=True, use_container_width=True)


def _detail(results):
    if not results:
        return
    labels = {f"{x['name']} ({x['ticker']})": x for x in results}
    selected = st.selectbox('현재가 기준 상세 종목', list(labels), key='rise_current_detail')
    item = labels[selected]
    entry = item.get('watch_entry')
    if entry:
        st.caption(f"수렴 후보 선정일 {entry['entry_asof']} · 선정 당시 간격 {entry['entry_span_pct']:.2f}% · {entry['entry_reason']}")
        st.caption('선정 후 수렴이 풀리거나 순위가 내려가도 관찰을 유지합니다. 필수 4/4는 매수 검토 조건이지 매수 확정 신호가 아닙니다.')
    if not item['valid']:
        st.warning(item['reason'] + ' · 이전 분석값을 대신 표시하지 않습니다.')
        return
    a, b, c, d = st.columns(4)
    a.metric('평가에 사용한 현재가', won(item['price']))
    b.metric('현재가 반영 점수', f"{item['score']:.0f}점")
    c.metric('1차 매수 참고', won(item['buy1']))
    d.metric('손절 참고', won(item['stop']))
    st.info(f"{item['label']} · {item['action']} · {item['mandatory_label']} / {item['auxiliary_label']}")
    st.caption(f"{item['mode']} · {item['quote_received_at']} 수신 · 과거 일봉 {item['history_day']}까지 + {item['analysis_day']} 현재가 1개")
    st.line_chart(item['chart'], height=320)
    st.caption('표·상세·차트는 동일한 현재가로 계산됩니다. 마지막 점은 확정 종가가 아닌 현재가일 수 있습니다. 실제 주문은 하지 않습니다.')


@st.fragment(run_every=WATCHLIST_REFRESH_SECONDS)
def _render_live_watchlist(rows):
    if not rows:
        st.info('관찰 중인 수렴 종목이 없습니다. 다음 완료된 일일 검색에서 빈자리를 보충합니다.')
        return
    # Membership comes only from the durable cohort, NOT current-price ranking.
    rows = rows[:DISPLAY_LIMIT]
    state = _watch_state(rows)
    if state['snapshot'] is None:
        st.info(f'선정된 수렴 관찰 {len(rows):,}개 일봉을 준비 중입니다. 준비 후 가격·조건을 {WATCHLIST_REFRESH_SECONDS}초 간격으로 조회합니다.')
        if state['error']:
            st.warning(state['error'])
        _schedule_watch_selection(state, rows)
        return
    chosen = rows
    started = monotonic()
    results, context = _evaluate_visible(chosen, state)
    selected = results[:DISPLAY_LIMIT]
    st.caption(f'관찰 {len(selected)}개 유지 · 가격·조건 {WATCHLIST_REFRESH_SECONDS}초 간격 조회 · 장중 종목 교체 없음')
    st.caption('같은 관찰 종목 안에서 필수 → 보조 → 단계·가격거리·거래량·점수 순으로 표시합니다. 수렴 해제·순위 하락만으로 종목을 제외하지 않으며, 순위는 매수 확정 신호가 아닙니다.')
    st.dataframe(_frame(selected, compact=True), key='rise_live_watchlist', use_container_width=True, hide_index=True,
                 column_config={'관찰 우선순위': st.column_config.NumberColumn(format='%d위')})
    _status(selected, context, WATCHLIST_REFRESH_SECONDS)
    snapshot = state['snapshot']
    full_good = sum(item['valid'] for item in snapshot['results'])
    progress = ' · 관찰 종목 자료 확인 중' if state['future'] is not None else ''
    st.caption(f"이번 조회·평가 {monotonic() - started:.1f}초 · 자료 확인 완료 {snapshot['completed_at']} · 관찰 자료 정상 {full_good}/{len(rows)}개{progress}")
    if state['error']:
        st.warning(state['error'])
    failed = [item for item in snapshot['results'] if not item['valid']]
    if failed:
        with st.expander(f'관찰 자료 보류 {len(failed)}개 확인'):
            st.dataframe(_frame(failed), hide_index=True, use_container_width=True)
    _detail(selected)
    # Submit after rendering the fast table; never block on this future.
    _schedule_watch_selection(state, rows)


def _scan_candidates(results):
    return [x for x in results if x['valid'] and x['price'] >= 1000 and x['average_value'] >= 500_000_000
            and x['label'].startswith(('🟢', '🟣', '🟡', '🔵'))]


@st.fragment(run_every='30s')
def _render_live_scan(rows, send_alerts, promote):
    results, context = _evaluate(rows, 'rise_current_scan')
    candidates = _scan_candidates(results)
    a, b, c, d = st.columns(4)
    a.metric('상승초입·1차구간', sum(x['label'].startswith(('🟢', '🟣')) for x in candidates))
    b.metric('돌파확인', sum(x['label'].startswith('🟡') for x in candidates))
    c.metric('준비구간', sum(x['label'].startswith('🔵') for x in candidates))
    d.metric('현재가 평가 후보', len(candidates))
    if candidates:
        st.dataframe(_frame(candidates[:DISPLAY_LIMIT]), key='rise_current_scan_table', use_container_width=True, hide_index=True)
        st.caption(f'현재가 평가 후보 {len(candidates):,}개 중 상위 {min(len(candidates), DISPLAY_LIMIT)}개 표시')
    else:
        st.info('최신 현재가와 일봉으로 확인된 상승 후보가 없습니다. 평가 보류 사유도 확인하세요.')
    _status(results, context, 30)
    # Keep the existing explicit-search notifications, never send from timer reruns.
    if st.session_state.pop('rise_current_notify_once', False):
        qualified = [x for x in candidates if x['mandatory_count'] == 4 and x['intraday']]
        payload = [{'종목': x['name'], '코드': x['ticker'], '시장': x['market'], '단계': x['label'],
                    '시점점수': x['score'], '현재가': x['price'], '1차매수': x['buy1'], '손절참고': x['stop']}
                   for x in qualified]
        if payload:
            for notice in (send_alerts(payload), promote(payload)):
                if notice:
                    st.info(notice)


def render_current_price_screen(universe, watchlist, send_alerts, promote, *, saved_count=None, cohort_asof=''):
    st.subheader('📍 현재가 기준 상승시점 평가')
    st.caption('KIS 현재가·누적 거래량과 직전 거래일까지의 KIS 일봉으로 평가합니다. 과거 서버 판정은 현재 평가에 섞지 않습니다.')
    st.caption('현재가 돌파는 종가 확정이 아닙니다. 거래량 배수는 당일 누적 거래량 ÷ 직전 20일 하루 평균으로, 오전에는 낮을 수 있습니다.')
    if st.button('🔄 일봉·현재가 모두 다시 조회', key='rise_current_reload'):
        st.session_state['rise_live_revision'] = st.session_state.get('rise_live_revision', 0) + 1
    if universe is None or universe.empty:
        st.warning('검색 대상 목록을 가져오지 못했습니다.')
    else:
        count = len(universe)
        if count < 500:
            st.warning(f'현재 확보된 검색 대상은 {count:,}개뿐입니다. KOSPI·KOSDAQ 전종목 분석이 아닙니다.')
        st.caption(f'검색 대상 {count:,}개 · 후보 선별도 최신 현재가 기준 · 1,000원 이상·직전 20일 평균 거래대금 5억원 이상')
        if st.button(f'🔎 대상 {count:,}개 현재가로 분석', type='primary', use_container_width=True):
            st.session_state['rise_current_scan_rows'] = [
                {'ticker': str(r['종목코드']).zfill(6), 'name': r['종목명'], 'market': r['시장']}
                for _, r in universe.drop_duplicates('종목코드').iterrows()]
            st.session_state['rise_current_notify_once'] = True
        scan_rows = st.session_state.get('rise_current_scan_rows')
        if scan_rows:
            _render_live_scan(scan_rows, send_alerts, promote)
    st.divider()
    st.markdown('### ⭐ 개인 관찰목록 · 수렴 후보 추적')
    st.caption('수렴 후보를 선정해 계속 관찰합니다. 상승·돌파로 수렴이 풀려도 유지하고, 현재가와 필수·보조조건만 갱신합니다.')
    total = len(watchlist) if saved_count is None else saved_count
    st.caption(f'관찰 {len(watchlist)}개 / 전체 저장 {total:,}개 · 최근 일일 검토 {cohort_asof or "확인 대기"} · 새 후보는 보관·대기 · 원본 기록 보존')
    _render_live_watchlist(watchlist)
