"""Bounded, read-only inputs for the manual market update."""
from concurrent.futures import Future
import json
from threading import Lock, Thread
from time import monotonic
from urllib.request import Request, urlopen

_lock = Lock()
_inflight = {}


def bounded_call(key, function, seconds=12):
    """Limit UI waiting for providers which do not expose a request timeout.

    A timeout cannot terminate provider code. Reuse its in-flight call and cap
    outstanding daemon workers so repeated clicks cannot accumulate threads.
    Workers must not touch Streamlit or session state.
    """
    with _lock:
        future = _inflight.get(key)
        if future is None:
            if len(_inflight) >= 4:
                raise TimeoutError('Market provider busy')
            future = Future()
            _inflight[key] = future
            def work():
                try:
                    future.set_result(function())
                except BaseException as exc:
                    future.set_exception(exc)
                finally:
                    with _lock:
                        if _inflight.get(key) is future:
                            del _inflight[key]
            Thread(target=work, daemon=True, name='market-provider').start()
    return future.result(timeout=seconds)


def fetch_market_universe(progress=None):
    """Require both paginated stock markets; never label 32 fallback names full."""
    stocks = {}
    deadline = monotonic() + 90
    for market in ('KOSPI', 'KOSDAQ'):
        seen = set()
        total = None
        for page in range(1, 81):
            if monotonic() >= deadline:
                raise TimeoutError('Market universe deadline exceeded')
            if progress:
                progress(f'{market} 종목목록 확인 · {page}페이지')
            url = f'https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100'
            request = Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                           'Referer': 'https://m.stock.naver.com/'})
            with urlopen(request, timeout=min(8, max(.1, deadline - monotonic()))) as response:
                doc = json.loads(response.read().decode('utf-8'))
            rows, count = doc.get('stocks'), doc.get('totalCount')
            if (not isinstance(rows, list) or not rows or type(count) is not int
                    or count < 500 or (total is not None and total != count)):
                raise ValueError('Incomplete market universe')
            total = count
            for row in rows:
                code = str(row.get('itemCode', ''))
                if not code.isdigit() or len(code) != 6:
                    continue
                seen.add(code)
                if row.get('stockEndType') == 'stock' and row.get('stockName'):
                    if code in stocks and stocks[code]['시장'] != market:
                        raise ValueError('Conflicting market universe')
                    stocks[code] = {'종목코드': code, '종목명': row['stockName'], '시장': market}
            if page * 100 >= total:
                break
        if total is None or len(seen) < total * .98:
            raise ValueError('Incomplete market universe pages')
    if len(stocks) < 1800:
        raise ValueError('Unexpectedly small market universe')
    return list(stocks.values())


def require_full_universe(universe):
    if (len(universe) < 1800 or '시장' not in universe
            or not {'KOSPI', 'KOSDAQ'}.issubset(set(universe['시장']))):
        raise RuntimeError('전체시장 종목목록을 확보하지 못했습니다. 비상 후보군으로 정밀 전체 업데이트를 진행하지 않습니다. 기존 분석은 유지되며 잠시 후 재시도할 수 있습니다.')
