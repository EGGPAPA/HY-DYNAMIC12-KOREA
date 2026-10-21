import base64
import ast
from copy import deepcopy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from early_watch_data import DataFormatError, RetrievalError, load_json, load_early_watch
from test_dual_leader_watch import sample


def response(value, envelope=False):
    if envelope:
        value = {'encoding': 'base64', 'content': base64.encodebytes(
            json.dumps(value).encode()).decode()}
    result = MagicMock()
    result.__enter__.return_value.read.return_value = json.dumps(value).encode()
    return result


def snapshot():
    day = '2026-10-20'
    return dict(complete=True, asof=day, leader_version=1,
                benchmarks={market: dict(eligible=True, date=day, strength_version=1,
                                         ret20_pct=3, ret60_pct=2)
                            for market in ('KOSPI', 'KOSDAQ')},
                items={'000001': sample('000001', date=day, strength_version=1,
                                        market='KOSPI', ret60_pct=-1, ret10_pct=2)})


class EarlyWatchDataTests(unittest.TestCase):
    @patch('early_watch_data.urlopen')
    def test_public_raw_json(self, get):
        get.return_value = response([{'ticker': '000001'}])
        self.assertEqual(load_json('rise_timing_watchlist.json', 'main', list)[0]['ticker'], '000001')
        req = get.call_args.args[0]
        self.assertIn('raw.githubusercontent.com', req.full_url)
        self.assertNotIn('Authorization', req.headers)
        self.assertEqual(get.call_args.kwargs['timeout'], 8)

    @patch('early_watch_data.urlopen')
    def test_authenticated_api_and_wrapped_base64(self, get):
        get.return_value = response({'complete': True}, envelope=True)
        self.assertEqual(load_json('data/scan.json', 'monitor-state', dict, 'test-token'), {'complete': True})
        req = get.call_args.args[0]
        self.assertIn('ref=monitor-state', req.full_url)
        self.assertEqual(req.headers['Authorization'], 'Bearer test-token')

    @patch('early_watch_data.urlopen')
    def test_api_rate_limit_and_auth_failure_fall_back_without_token_leak(self, get):
        for status in (401, 403, 429, 503):
            with self.subTest(status=status):
                get.side_effect = [HTTPError('api', status, '', {}, None), response({'ok': True})]
                self.assertEqual(load_json('data/scan.json', 'monitor-state', dict, 'secret'), {'ok': True})
                req = get.call_args.args[0]
                self.assertIn('raw.githubusercontent.com', req.full_url)
                self.assertNotIn('Authorization', req.headers)

    @patch('early_watch_data.urlopen')
    def test_raw_outage_falls_back_to_api(self, get):
        get.side_effect = [URLError('unavailable'), response([], envelope=True)]
        self.assertEqual(load_json('watch.json', 'main', list), [])
        self.assertIn('api.github.com', get.call_args.args[0].full_url)

    @patch('early_watch_data.urlopen')
    def test_contents_without_inline_content_falls_back_to_raw(self, get):
        get.side_effect = [response({'encoding': 'none', 'content': ''}), response({'items': {}})]
        self.assertEqual(load_json('data/scan.json', 'monitor-state', dict, 'token'), {'items': {}})

    @patch('early_watch_data.urlopen')
    def test_wrong_shape_and_invalid_json_not_successful_empty_results(self, get):
        get.side_effect = [response([]), response({'encoding': 'base64', 'content': '!!!'})]
        with self.assertRaises(DataFormatError):
            load_json('scan.json', 'monitor-state', dict)

    @patch('early_watch_data.urlopen')
    def test_both_sources_fail_then_next_call_recovers(self, get):
        get.side_effect = [TimeoutError(), URLError('offline'), response([])]
        with self.assertRaises(RetrievalError):
            load_json('watch.json', 'main', list)
        self.assertEqual(load_json('watch.json', 'main', list), [])

    @patch('early_watch_data.load_json')
    def test_completed_data_produces_a_without_mutating_b_inputs(self, load):
        daily = snapshot()
        saved = [{'ticker': '000001'}]
        before = deepcopy((saved, daily))
        load.side_effect = [saved, daily]
        rows, day, error = load_early_watch('token')
        self.assertEqual(([r['ticker'] for r in rows], day, error), (['000001'], '2026-10-20', ''))
        self.assertEqual((saved, daily), before)
        self.assertEqual(load.call_args_list[1].args[1], 'monitor-state')

    @patch('early_watch_data.load_json')
    def test_pending_and_legitimate_empty_candidates(self, load):
        for pending in (False, True):
            daily = snapshot()
            daily['pending'] = pending
            load.side_effect = [[], daily]
            rows, day, error = load_early_watch()
            self.assertEqual(rows, [])
            self.assertEqual(error, '확정 일봉 검증 대기' if pending else '')

    @patch('early_watch_data.load_json')
    def test_malformed_records_are_format_errors(self, load):
        for saved, daily in [([None], snapshot()), ([], dict(snapshot(), benchmarks=[])),
                             ([], dict(snapshot(), items={'000001': None}))]:
            load.side_effect = [saved, daily]
            with self.assertRaises(DataFormatError):
                load_early_watch()

    @patch('early_watch_data.load_early_watch')
    def test_ui_failure_retries_after_thirty_seconds_and_success_uses_long_cache(self, load):
        clock = [0]
        def cache_data(ttl, **kwargs):
            def decorate(fn):
                cached = []
                def wrapped():
                    if not cached or clock[0] >= cached[0]:
                        value = fn()  # Exceptions must not populate the success cache.
                        cached[:] = [clock[0] + ttl, value]
                    return cached[1]
                return wrapped
            return decorate
        source = ast.parse(Path('rise_current_price_ui.py').read_text(encoding='utf-8'))
        functions = [node for node in source.body if isinstance(node, ast.FunctionDef)
                     and node.name in ('_early_watch_data', '_early_watch_snapshot')]
        namespace = {'st': SimpleNamespace(cache_data=cache_data, secrets={'GITHUB_PAT': 'token'})}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<early-ui>', 'exec'), namespace)
        load.side_effect = [RetrievalError(), ([{'ticker': '000001'}], '2026-10-20', '')]
        read = namespace['_early_watch_snapshot']
        self.assertIn('30초', read()[2])
        clock[0] = 29
        self.assertIn('30초', read()[2])
        self.assertEqual(load.call_count, 1)
        clock[0] = 30
        self.assertEqual(read()[1:], ('2026-10-20', ''))
        clock[0] = 90
        self.assertEqual(read()[1:], ('2026-10-20', ''))
        self.assertEqual(load.call_count, 2)
        load.assert_called_with('token')


if __name__ == '__main__':
    unittest.main()
