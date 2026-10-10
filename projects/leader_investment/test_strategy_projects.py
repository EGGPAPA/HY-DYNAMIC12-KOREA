from copy import deepcopy
from datetime import datetime
import unittest

import pandas as pd

from projects.leader_investment.strategy_projects import (KST, candidate_report, change_state, combined_positions,
    empty_state, ledger, risk_quantity, technical_view, validate_state)

NOW = datetime(2026, 10, 10, 12, tzinfo=KST)
ROW = {'ticker': '000001', 'name': '가상 종목', 'market': 'KOSPI'}
CTX = {'day': '20261008', 'previous': '20261007', 'basis': 'close', 'intraday': False, 'mode': '휴일 종가 참고'}


def history():
    return pd.DataFrame({'Close': [10000 + 2 * i for i in range(90)], 'Volume': [1000] * 89 + [1800]},
                        index=pd.bdate_range(end='2026-10-08', periods=90))


def snapshot():
    item = {**ROW, 'date': '2026-10-08', 'close': 10178, 'ma5': 10174,
        'ma20': 10159, 'ma60': 10119, 'ma20_prev5': 10149,
        'ret10_pct': 2., 'ret20_pct': 4., 'ret60_pct': 8., 'mean_value20': 3e9,
        'strength_version': 1, 'eligible': True, 'ordinary': True, 'tradable': True,
        'span_pct': 1., 'state': '수렴 관찰'}
    return {'asof': '2026-10-08', 'complete': True, 'pending': False, 'leader_version': 1,
        'items': {'000001': item}, 'benchmarks': {m: {'date': '2026-10-08', 'eligible': True,
        'strength_version': 1, 'ret20_pct': 1., 'ret60_pct': 2.} for m in ('KOSPI', 'KOSDAQ')}}


def enroll(state=None, project='short'):
    return change_state(state or empty_state(), project, 'add', {'rows': [ROW], 'asof': '2026-10-08'}, '2026-10-10')


def trade(state, project='short', side='buy', qty=10, price=10000, fees=100, uid='a', day='2026-10-08'):
    return change_state(state, project, 'trade', dict(id=uid, ticker='000001', side=side,
        qty=qty, price=price, fees=fees, date=day), '2026-10-10')


class ProjectStateTests(unittest.TestCase):
    def test_isolated_projects_and_no_mutation(self):
        original = empty_state()
        state = enroll(original)
        self.assertEqual(original, empty_state())
        self.assertEqual(state['projects']['medium']['watch'], [])
        after = trade(state)
        self.assertEqual(state['projects']['short']['trades'], [])
        self.assertEqual(after['projects']['medium']['trades'], [])

    def test_deduplicates_watches_and_caps_twenty(self):
        state = enroll(enroll())
        self.assertEqual(len(state['projects']['short']['watch']), 1)
        rows = [{**ROW, 'ticker': f'{i:06}'} for i in range(1, 22)]
        with self.assertRaises(ValueError):
            change_state(empty_state(), 'medium', 'add', {'rows': rows, 'asof': '2026-10-08'}, '2026-10-10')

    def test_average_cost_partial_sale_and_fees(self):
        state = trade(enroll())
        state = trade(state, uid='b', qty=10, price=12000, fees=100)
        state = trade(state, uid='c', side='sell', qty=5, price=13000, fees=50)
        pos = ledger(state['projects']['short']['trades'])['000001']
        self.assertEqual(pos['qty'], 15)
        self.assertAlmostEqual(pos['cost'], 165150)
        self.assertAlmostEqual(pos['realized'], 9900)

    def test_oversell_negative_nan_fraction_future_rejected(self):
        for kwargs in ({'qty': 11, 'side': 'sell', 'uid': 's'}, {'qty': -1}, {'price': float('nan')},
                       {'fees': -1}, {'qty': 1.5}, {'day': '2026-10-11'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                trade(trade(enroll()), uid=kwargs.get('uid', 'b'), **{k:v for k,v in kwargs.items() if k != 'uid'})

    def test_same_submission_id_is_idempotent(self):
        state = trade(enroll())
        self.assertEqual(trade(state), state)

    def test_trade_requires_watch_and_chronological_record(self):
        with self.assertRaises(ValueError):
            trade(empty_state())
        with self.assertRaises(ValueError):
            trade(trade(enroll()), uid='b', day='2026-10-07')

    def test_holding_cannot_be_archived_and_retired_history_preserved(self):
        state = trade(enroll())
        with self.assertRaises(ValueError):
            change_state(state, 'short', 'archive', {'ticker': '000001'}, '2026-10-10')
        state = trade(state, side='sell', uid='s')
        state = change_state(state, 'short', 'archive', {'ticker': '000001'}, '2026-10-10')
        self.assertEqual(len(state['projects']['short']['trades']), 2)
        self.assertEqual(len(state['projects']['short']['archived']), 1)
        self.assertEqual(state['projects']['short']['watch'], [])

    def test_void_preserves_record_and_cannot_break_later_sale(self):
        bought = trade(enroll())
        sold = trade(bought, side='sell', qty=5, uid='s')
        with self.assertRaises(ValueError):
            change_state(sold, 'short', 'void_trade', {'id':'a', 'reason':'실수'}, '2026-10-10')
        fixed = change_state(bought, 'short', 'void_trade', {'id':'a', 'reason':'실수'}, '2026-10-10')
        self.assertTrue(fixed['projects']['short']['trades'][0]['voided'])
        self.assertEqual(ledger(fixed['projects']['short']['trades']), {})

    def test_same_ticker_combined_without_merging_projects(self):
        state = trade(enroll())
        state = trade(enroll(state, 'medium'), project='medium', qty=20, uid='m')
        pos = combined_positions(state)[0]
        self.assertEqual(pos['qty'], 30)
        self.assertEqual(pos['projects'], ['short','medium'])
        self.assertEqual(len(state['projects']['short']['trades']), 1)

    def test_settings_do_not_change_other_project(self):
        state = change_state(empty_state(), 'short', 'settings', {'budget':1e7, 'risk_pct':.5}, '2026-10-10')
        self.assertEqual(state['projects']['medium']['budget'], 0)
        for risk in (0, .01, 3, float('inf')):
            with self.assertRaises(ValueError):
                change_state(state, 'short', 'settings', {'budget':1e7, 'risk_pct':risk}, '2026-10-10')

    def test_manual_fundamental_verification_requires_source_and_reason(self):
        payload = {'ticker':'000001', 'fundamental_ok':True, 'thesis':'실적 확인', 'source':'', 'stop':9500, 'next_review':'2026-10-17'}
        with self.assertRaises(ValueError):
            change_state(enroll(), 'short', 'review', payload, '2026-10-10')
        payload['source'] = 'https://example.com/report'
        state = change_state(enroll(), 'short', 'review', payload, '2026-10-10')
        self.assertTrue(state['projects']['short']['reviews']['000001']['fundamental_ok'])
        self.assertEqual(state['projects']['medium']['reviews'], {})

    def test_risk_calculator_invalid_and_boundaries(self):
        self.assertEqual(risk_quantity(1e7, .5, 10000, 9500), 100)
        self.assertEqual(risk_quantity(10000, 2, 10000, 9999), 1)
        for args in ((0,.5,100,90),(100,.5,100,100),(100,.5,100,110),(100,3,100,90),(100,.5,float('nan'),90)):
            self.assertIsNone(risk_quantity(*args))


class CandidateTests(unittest.TestCase):
    def test_distinct_admission_rules(self):
        snap = snapshot()
        snap['items']['000001']['ret60_pct'] = -2
        self.assertEqual(len(candidate_report(snap,[ROW],'short','2026-10-10')['rows']),1)
        self.assertEqual(candidate_report(snap,[ROW],'medium','2026-10-10')['rows'],[])

    def test_short_medium_rank_differ(self):
        snap = snapshot()
        second = {**ROW, 'ticker':'000002', 'name':'중기 강세'}
        snap['items']['000002'] = {**snap['items']['000001'], **second, 'ret10_pct':1., 'ret60_pct':40.}
        self.assertEqual(candidate_report(snap,[ROW,second],'short','2026-10-10')['rows'][0]['ticker'],'000001')
        self.assertEqual(candidate_report(snap,[ROW,second],'medium','2026-10-10')['rows'][0]['ticker'],'000002')

    def test_invalid_snapshot_stale_future_pending_and_missing_benchmark(self):
        changes = ({'asof':'2026-09-01'}, {'asof':'2026-10-11'}, {'complete':False}, {'pending':True}, {'benchmarks':{}})
        for update in changes:
            snap = {**snapshot(), **update}
            self.assertFalse(candidate_report(snap,[ROW],'short','2026-10-10')['ready'])
        self.assertFalse(candidate_report(None,[ROW],'short','2026-10-10')['ready'])

    def test_same_day_unclosed_denied(self):
        snap = snapshot()
        self.assertFalse(candidate_report(snap,[ROW],'short','2026-10-08', NOW.replace(day=8,hour=12))['ready'])

    def test_stock_safety_filters_no_forced_twenty(self):
        for patch in ({'ordinary':False},{'tradable':False},{'eligible':False},{'close':float('nan')},
                      {'date':'2026-10-07'},{'market':'UNKNOWN'},{'ticker':'000002'},{'mean_value20':1}):
            snap = snapshot()
            snap['items']['000001'].update(patch)
            self.assertEqual(candidate_report(snap,[ROW],'short','2026-10-10')['rows'],[],patch)

    def test_no_saved_data_mutation_and_nonconvergence_allowed(self):
        snap = snapshot()
        snap['items']['000001']['span_pct'] = 12
        saved = deepcopy(snap)
        self.assertEqual(len(candidate_report(snap,[ROW],'medium','2026-10-10')['rows']),1)
        self.assertEqual(saved,snap)


class TechnicalTests(unittest.TestCase):
    def test_holiday_uses_confirmed_close_ignores_quote(self):
        for project in ('short','medium'):
            a = technical_view(project,ROW,history(),None,CTX,NOW)
            b = technical_view(project,ROW,history(),{'price':999999},CTX,NOW)
            self.assertTrue(a['valid'])
            self.assertEqual(a['price'],10178)
            self.assertEqual(a['breakout_state'],b['breakout_state'])

    def test_medium_frozen_during_intraday_short_changes(self):
        ctx = {'day':'20261009','previous':'20261008','basis':'quote','intraday':True,'mode':'잠정'}
        now = NOW.replace(day=9)
        views = {}
        for project in ('short','medium'):
            views[project] = []
            for price in (10200,14000):
                q = {'ok':True,'price':price,'volume':2000,'previous_close':10178,'received_at':now.isoformat()}
                views[project].append(technical_view(project,ROW,history(),q,ctx,now))
        self.assertNotEqual(views['short'][0]['price'],views['short'][1]['price'])
        self.assertEqual(views['medium'][0]['price'],views['medium'][1]['price'])
        self.assertEqual(views['medium'][0]['analysis_day'],'2026-10-08')

    def test_no_future_bars_or_input_mutation(self):
        h = history()
        old = h.copy()
        before = technical_view('medium',ROW,h,None,CTX,NOW)
        pd.testing.assert_frame_equal(h,old)
        h.loc[pd.Timestamp('2026-10-12')] = [999999,999999]
        after = technical_view('medium',ROW,h,None,CTX,NOW)
        self.assertEqual(before['stop_reference'],after['stop_reference'])
        self.assertEqual(before['breakout_state'],after['breakout_state'])

    def test_missing_stale_bad_history_holds(self):
        for h in (None, pd.DataFrame(), history().iloc[:-1]):
            result = technical_view('medium',ROW,h,None,CTX,NOW)
            self.assertFalse(result['valid'])
            self.assertEqual(result['breakout_state'],'평가 보류')
        result = technical_view('short',ROW,history(),None,{'error':'통신 실패'},NOW)
        self.assertFalse(result['valid'])

    def test_medium_requires_manual_fundamentals(self):
        before = technical_view('medium',ROW,history(),None,CTX,NOW)
        self.assertEqual(before['breakout_state'],'기술 4/4 · 실적·공시 확인 대기')
        review = {'fundamental_ok':True,'thesis':'가상 근거','source':'https://example.com','checked_on':'2026-10-10'}
        after = technical_view('medium',ROW,history(),None,CTX,NOW,review)
        self.assertTrue(after['fundamental_verified'])
        self.assertIn('검토 4/4',after['breakout_state'])
        review['checked_on'] = '2026-01-01'
        self.assertFalse(technical_view('medium',ROW,history(),None,CTX,NOW,review)['fundamental_verified'])

    def test_pullback_not_breakout_relabel(self):
        result = technical_view('short',ROW,history(),None,CTX,NOW)
        self.assertTrue(all(result['breakout_checks'].values()))
        self.assertFalse(result['pullback_checks']['조정 후 반등'])


if __name__ == '__main__':
    unittest.main()
