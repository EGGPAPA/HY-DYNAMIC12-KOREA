"""Current prices evaluate a saved convergence cohort, never reselect its members."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import monotonic

import pandas as pd
import streamlit as st

from rise_live_analysis import KST, evaluate_current, observe_persistence, priority_key
from rise_live_data import get_current_quotes, get_histories, get_market_context
from watch_sector_context import business_context, sector_flow

DISPLAY_LIMIT = 20
WATCHLIST_REFRESH_SECONDS = 10
WATCHLIST_SELECTION_SECONDS = 60
WATCHLIST_HIDDEN_COLUMNS = (
    '시세 수신시각(KST)', '평가 기준일', '과거 일봉 마지막', '평가 구분',
    '7조건 확인', '수렴 간격', '1차가 거리', '현재가 평가',
)


def won(value):
    return '—' if value is None else f'{value:,.0f}원'


def _quotes_for_context(rows, context, seconds=10):
    # Reference evaluation uses its dated daily bar, not an undated holiday quote.
    if context.get('basis') == 'close':
        return {}
    return get_current_quotes([row['ticker'] for row in rows], refresh_seconds=seconds)


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
    quotes = _quotes_for_context(rows, context)
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
    quotes = _quotes_for_context(rows, context)
    evaluated_at = datetime.now(KST)
    results = [evaluate_current(row, histories.get(row['ticker']), quotes.get(row['ticker']),
                               context, evaluated_at) for row in rows]
    observe_persistence(results, observations)
    return {'results': sorted(results, key=priority_key), 'histories': histories,
            'history_hour': hour, 'context': context, 'observations': observations,
            'completed_at': datetime.now(KST).isoformat(timespec='seconds')}


def _watch_state(rows, namespace='rise_watch_fast_state'):
    now = datetime.now(KST)
    revision = st.session_state.get('rise_live_revision', 0)
    key = (now.strftime('%Y%m%d'), revision,
           tuple((row['ticker'], row.get('name'), row.get('market')) for row in rows))
    state = st.session_state.get(namespace)
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
        st.session_state[namespace] = state
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
    quotes = _quotes_for_context(rows, context, WATCHLIST_REFRESH_SECONDS)
    now = datetime.now(KST)
    results = [evaluate_current(row, state['histories'].get(row['ticker']),
                               quotes.get(row['ticker']), context, now) for row in rows]
    observe_persistence(results, state['observations'])
    for item in results:
        # Even a failed receipt supersedes older background observations.
        state['observed_at'][item['ticker']] = item.get('quote_received_at') or now.isoformat(timespec='seconds')
        item['watch_entry'] = next((row.get('watch_entry') for row in rows if row['ticker'] == item['ticker']), None)
    return sorted(results, key=priority_key), context


def _frame(results, *, compact=False, sector_snapshot=None):
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
            '시세 수신시각(KST)': item.get('quote_received_at') or ('해당 없음 · 확정 일봉 참고' if item.get('basis') == 'close' else '수신 실패'),
            '평가 기준일': item.get('analysis_day', '—'), '과거 일봉 마지막': item.get('history_day', '—'),
            '평가 구분': item.get('mode', '보류'),
        })
    frame = pd.DataFrame(rows)
    if any(item.get('basis') == 'close' for item in results):
        frame = frame.rename(columns={'현재가(KIS)': '기준가(KIS 종가)',
            '누적거래량/20일평균': '마감거래량/20일평균', '세 선 수렴(현재가 반영)': '세 선 수렴(종가 기준)'})
    # Simplify only the personal table; keep calculations and diagnostics intact.
    if not compact or frame.empty:
        return frame
    frame = frame.drop(columns=list(WATCHLIST_HIDDEN_COLUMNS), errors='ignore')
    frame['주요사업'] = [business_context(x['ticker'])['primary'] for x in results]
    frame['업종 흐름(종가)'] = [sector_flow(x['ticker'], sector_snapshot,
        x.get('analysis_day')) for x in results]
    frame['업종 흐름(종가)'] = frame['업종 흐름(종가)'].map(lambda value: value.split(' · ', 1)[-1])
    convergence = '세 선 수렴(종가 기준)' if '세 선 수렴(종가 기준)' in frame else '세 선 수렴(현재가 반영)'
    frame = frame.rename(columns={convergence: '수렴 상태'})
    # Failed current data must not borrow a former convergence label.
    frame['수렴 상태'] = [x.get('convergence', '—') if x['valid'] else '— 평가 보류' for x in results]
    first = ['관찰 우선순위', '종목', '주요사업', '필수조건', '보조조건', '수렴 상태', '업종 흐름(종가)']
    # Keep the two conditions together; identifiers follow decision information.
    first += [c for c in ('현재가(KIS)', '기준가(KIS 종가)') if c in frame]
    return frame[first + [name for name in frame if name not in first]]


def _status(results, context, seconds):
    good = sum(x['valid'] for x in results)
    received = [x['quote_received_at'] for x in results if x.get('quote_received_at')]
    stamps = f'{min(received)} ~ {max(received)}' if received else '수신 성공 없음'
    reference = context.get('basis') == 'close'
    if reference:
        st.info(f"{context['mode']} · 평가 기준일 {pd.Timestamp(context['day']).strftime('%Y-%m-%d')} · 종가와 하루 거래량으로 다시 계산한 참고 평가입니다. 실시간 시세가 아닙니다.")
        st.caption(f'KIS 확정 일봉 기준 · 정상 평가 {good:,}/{len(results):,}개 · 휴일에는 가격이 변하지 않습니다. 장중 지속성은 판정하지 않습니다.')
        receipts = [x['history_received_at'] for x in results if x.get('history_received_at')]
        if receipts:
            st.caption(f'일봉 조회 시각(KST): {min(receipts)} ~ {max(receipts)} · 거래소 체결시각이 아닙니다.')
    else:
        st.caption(f'현재가·누적 거래량: KIS KRX(J) · {seconds}초 간격 조회 요청(통신 지연 가능) · 정상 평가 {good:,}/{len(results):,}개')
        st.caption(f'실제 시세 수신시각(KST): {stamps} · 수신시각은 거래소 체결시각이 아닙니다.')
    if context.get('notice'):
        st.caption(context['notice'])
    if context.get('error'):
        st.warning(context['error'] + ' · 거래일을 확인할 때까지 평가를 보류합니다.')
    elif not reference and not context['intraday']:
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
    business = business_context(item['ticker'])
    st.caption(f"주요사업: {business['primary']} · 관련 사업·테마: {business['themes']}")
    st.caption(f"비교 업종: {business['group'] or '미지정 · 복합사업 또는 그래프 범위 밖'} · 사업 기반 관찰용 분류이며 공식 거래소 업종 분류가 아닙니다.")
    if business['source']:
        st.markdown(f"[사업 분류 근거]({business['source']}) · 확인일 {business['reviewed_on']}")
    entry = item.get('watch_entry')
    if entry:
        if entry.get('entry_policy') == 'leader_v1':
            st.caption(f"주도주 후보 선정 기준일 {entry['entry_asof']} · {entry['entry_reason']}")
            st.caption(f"선정 당시: 20거래일 {entry['entry_ret20_pct']:+.1f}% / 60거래일 {entry['entry_ret60_pct']:+.1f}% · "
                       f"해당 시장지수 대비 {entry['entry_excess20_pct']:+.1f}%p / {entry['entry_excess60_pct']:+.1f}%p · "
                       f"20일 평균 거래대금 약 {entry['entry_mean_value20']/100000000:,.1f}억원")
            if entry.get('entry_overheated'):
                st.caption('선정 당시 급등·과열 참고 종목입니다. 주도주 후보 포함은 즉시 매수 신호가 아닙니다.')
        else:
            st.caption(f"수렴 후보 선정일 {entry['entry_asof']} · 선정 당시 간격 {entry['entry_span_pct']:.2f}% · {entry['entry_reason']}")
        st.caption('선정 후 조건이 약해져도 관찰 대상은 자동 교체하지 않습니다. 필수 4/4는 매수 검토 조건이지 매수 확정 신호가 아닙니다.')
    if not item['valid']:
        st.warning(item['reason'] + ' · 이전 분석값을 대신 표시하지 않습니다.')
        return
    a, b, c, d = st.columns(4)
    reference = item.get('basis') == 'close'
    a.metric('평가에 사용한 종가' if reference else '평가에 사용한 현재가', won(item['price']))
    b.metric('종가 기준 점수' if reference else '현재가 반영 점수', f"{item['score']:.0f}점")
    c.metric('1차 매수 참고', won(item['buy1']))
    d.metric('손절 참고', won(item['stop']))
    st.info(f"{item['label']} · {item['action']} · {item['mandatory_label']} / {item['auxiliary_label']}")
    if reference:
        st.caption(f"{item['mode']} · {item['analysis_day']} 확정 종가·하루 거래량 기준 · 오늘 일봉을 가상으로 추가하지 않습니다.")
    else:
        st.caption(f"{item['mode']} · {item['quote_received_at']} 수신 · 과거 일봉 {item['history_day']}까지 + {item['analysis_day']} 현재가 1개")
    st.line_chart(item['chart'], height=320)
    st.caption('표·상세·차트는 동일한 기준 가격으로 계산됩니다. 장중에는 잠정 현재가, 휴일에는 표시된 거래일 종가입니다. 실제 주문은 하지 않습니다.')


@st.fragment(run_every=WATCHLIST_REFRESH_SECONDS)
def _render_live_watchlist(rows):
    if not rows:
        st.info('관찰 중인 종목이 없습니다. 다음 완료된 일일 검색에서 선정 기준을 충족하는 종목으로 빈자리를 보충합니다.')
        return
    # Membership comes only from the durable cohort, NOT current-price ranking.
    rows = rows[:DISPLAY_LIMIT]
    state = _watch_state(rows)
    if state['snapshot'] is None:
        st.info(f'선정된 관찰 {len(rows):,}개 일봉을 준비 중입니다. 준비 후 가격·조건을 {WATCHLIST_REFRESH_SECONDS}초 간격으로 조회합니다.')
        if state['error']:
            st.warning(state['error'])
        _schedule_watch_selection(state, rows)
        return
    chosen = rows
    started = monotonic()
    results, context = _evaluate_visible(chosen, state)
    selected = results[:DISPLAY_LIMIT]
    if context.get('basis') == 'close':
        st.info(f"📅 {pd.Timestamp(context['day']).strftime('%Y-%m-%d')} 종가 기준 · 휴일에도 점수·필수·보조조건·매수가·손절 참고를 확인할 수 있습니다. 실시간 시세가 아닙니다.")
        st.caption(f'관찰 {len(selected)}개 유지 · 최근 확정 일봉 참고 · 장중에는 가격·조건 10초 간격 조회로 전환')
    else:
        st.caption(f'관찰 {len(selected)}개 유지 · 가격·조건 {WATCHLIST_REFRESH_SECONDS}초 간격 조회 · 장중 종목 교체 없음')
    st.caption('같은 관찰 종목 안에서 필수 → 보조 → 단계·가격거리·거래량·점수 순으로 표시합니다. 주도력 선정 순위와는 다르며, 조건 약화·순위 하락만으로 자동 제외하지 않습니다. 순위는 매수 확정 신호가 아닙니다.')
    sector_snapshot = st.session_state.get('sector_observation_last_good')
    sector_asof = sector_snapshot.get('asof', '확인 중') if sector_snapshot else '확인 중'
    st.caption(f'업종 흐름: {sector_asof} 종가 · 위 업종 그래프의 대표 3종목 비교 지표입니다. 수렴은 5·20·60일선 간격 3% 이내 여부이며, 주도주 선정의 필수조건이 아닙니다.')
    st.dataframe(_frame(selected, compact=True, sector_snapshot=sector_snapshot), key='rise_live_watchlist', use_container_width=True, hide_index=True,
                 column_config={'관찰 우선순위': st.column_config.NumberColumn(format='%d위')})
    with st.expander('업종 흐름·수렴 상태 읽는 방법'):
        st.caption('주도: 20거래일 시장 대비 강도가 양수이며 3거래일 연속 단독 1위. 주도 후보: 아직 3거래일 미충족. 추격: 현재 2위가 현재 1위와의 격차를 3거래일 연속 줄이는 중입니다.')
        st.caption('강세·관찰은 시장 대비 강도가 양수인 나머지 업종, 시장 하회는 음수인 업종입니다. 가격 상승·하락 자체나 앞으로의 수익을 뜻하지 않습니다. 비교업종 미지정은 약세라는 뜻이 아닙니다.')
        st.caption('주요사업은 사업자료 기반 관찰용 분류입니다. 업종 흐름은 대표 3종목의 참고 지표이며 개별 종목의 주도력을 대신하지 않습니다. 관련 사업·테마와 분류 근거는 상세에서 확인할 수 있습니다.')
        st.caption('수렴 관찰: 5·20·60일선 간격 3% 이내. 수렴·하방주의: 이 조건에서 가격이 세 선 아래. 비수렴도 주도주 후보가 될 수 있으며 수렴 자체는 매수 신호가 아닙니다.')
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
    with st.expander('관찰종목 상세·차트'):
        _detail(selected)
    # Submit after rendering the fast table; never block on this future.
    _schedule_watch_selection(state, rows)


@st.fragment(run_every=WATCHLIST_REFRESH_SECONDS)
def render_new_discoveries(load_recent):
    st.markdown('### 🆕 최근 새로 발굴된 종목')
    recent = load_recent()
    if recent['state'] == 'pending':
        st.info('일일 검색·저장이 진행 중입니다. 완료된 신규 후보만 표시합니다.')
        return
    if recent['state'] != 'ready':
        st.warning('최근 신규 발굴 결과를 확인하지 못했습니다. 신규 후보가 없다는 뜻은 아닙니다.')
        return
    asof = recent['asof']
    today = datetime.now(KST).date().isoformat()
    when = '오늘 완료된 검색' if asof == today else '최근 완료된 검색 · 오늘 발굴 결과가 아닙니다'
    rows = recent['rows']
    st.caption(f'발굴 기준일 {asof} 종가 · {when} · 신규 저장 {len(rows)}개')
    st.caption(f'매일 장 마감 후 새 후보를 최대 10개 저장합니다. 이 표는 새로 추가된 종목만 보여주며, 기존 관찰 20개를 교체하지 않습니다. 현재가·조건은 {WATCHLIST_REFRESH_SECONDS}초 간격 조회합니다.')
    if recent['unverified']:
        st.warning(f"발굴일·저장 기록 확인이 필요한 {recent['unverified']}개는 표시를 보류했습니다.")
    if not rows:
        st.info(f'{asof} 검색에서 표시할 신규 저장 종목이 없습니다. 기존 후보의 중복 검색은 새 발굴로 표시하지 않습니다.')
        return
    # Separate state prevents this panel from replacing the primary cohort's data.
    state = _watch_state(rows, namespace='rise_discovery_fast_state')
    if state['snapshot'] is None:
        st.info(f'신규 후보 {len(rows)}개의 현재가 평가 자료를 준비 중입니다.')
        if state['error']:
            st.warning(state['error'])
        _schedule_watch_selection(state, rows)
        return
    results, context = _evaluate_visible(rows, state)
    frame = _frame(results, compact=True)
    price_column = '기준가(KIS 종가)' if '기준가(KIS 종가)' in frame.columns else '현재가(KIS)'
    columns = ['종목', '코드', price_column, '필수조건', '보조조건', '단계', '1차 매수 참고', '손절 참고']
    frame = frame[columns].copy()
    status = {row['ticker']: row['discovery_status'] for row in rows}
    frame.insert(2, '관찰 상태', frame['코드'].map(status))
    st.dataframe(frame, key='rise_recent_discoveries', hide_index=True, use_container_width=True)
    _status(results, context, WATCHLIST_REFRESH_SECONDS)
    if state['error']:
        st.warning(state['error'])
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
    d.metric('종가 기준 후보' if context.get('basis') == 'close' else '현재가 평가 후보', len(candidates))
    if candidates:
        st.dataframe(_frame(candidates[:DISPLAY_LIMIT]), key='rise_current_scan_table', use_container_width=True, hide_index=True)
        basis = '종가 기준' if context.get('basis') == 'close' else '현재가 평가'
        st.caption(f'{basis} 후보 {len(candidates):,}개 중 상위 {min(len(candidates), DISPLAY_LIMIT)}개 표시')
    else:
        st.info('현재 평가 기준에서 확인된 상승 후보가 없습니다. 기준일과 평가 보류 사유도 확인하세요.')
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


def render_current_price_screen(universe, watchlist, send_alerts, promote, *, saved_count=None, cohort_asof='', cohort_policy='convergence_v1'):
    st.subheader('📍 현재가 기준 상승시점 평가')
    st.caption('장중에는 KIS 현재가·누적 거래량, 휴일·장 시작 전에는 최근 확정 일봉의 종가·하루 거래량으로 평가합니다. 과거 서버 판정을 재사용하지 않고 같은 계산식으로 다시 평가합니다.')
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
    if cohort_policy == 'leader_v1':
        st.markdown('### ⭐ 개인 관찰목록 · 주도주 후보 추적')
        st.caption('저장 종목 중 시장 대비 강한 상승 추세와 거래대금을 기준으로 최대 20개를 선정합니다. 수렴은 참고이며, 선정 후 현재가·필수·보조조건을 10초마다 조회합니다.')
        with st.expander('주도주 선정 기준 보기', expanded=False):
            st.caption('마감 일봉 기준: 보통주 · 1,000원 이상 · 20일 평균 거래대금 약 20억원 이상 · 현재가 > 20일선 > 60일선 · 20일선 상승 · 20·60거래일 모두 상승하고 해당 KOSPI/KOSDAQ 지수보다 강한 종목')
            st.caption('주도력 순서: 저장 보통주 내 20거래일 시장 초과 수익률 백분위 45% + 60거래일 25% + 평균 거래대금 30%. 거래대금은 종가×거래량 근사치입니다. 수렴 간격은 선발·정렬 점수에 넣지 않습니다.')
            st.caption('검증된 수익 예측이나 매수 추천이 아닌 관찰용 기준입니다. 급등 종목도 포함될 수 있으며 업종별 대표성·실적·외국인 수급은 별도 검증하지 않습니다. 현재 매수 조건은 아래 필수·보조로 따로 확인하세요.')
    else:
        st.markdown('### ⭐ 개인 관찰목록 · 수렴 후보 추적')
        st.caption('수렴 후보를 선정해 계속 관찰합니다. 상승·돌파로 수렴이 풀려도 유지하고, 현재가와 필수·보조조건만 갱신합니다.')
    total = len(watchlist) if saved_count is None else saved_count
    st.caption(f'관찰 {len(watchlist)}개 / 전체 저장 {total:,}개 · 최근 일일 검토 {cohort_asof or "확인 대기"} · 새 후보는 보관·대기 · 원본 기록 보존')
    _render_live_watchlist(watchlist)





