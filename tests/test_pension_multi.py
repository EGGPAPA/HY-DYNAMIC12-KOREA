import copy
from datetime import date
import unittest

from pension_transactions import (apply_trade, portfolio_summary, pension_positions,
                                  normalize_ticker, adjust_extra_position)
from test_pension_transactions import opening, trade


def new_trade(saved=None, **overrides):
    args = dict(trade_id='new-1', asset='new', side='buy', quantity=10, price=10000,
                trade_date='2026-10-08', today=date(2026, 10, 12),
                new_etf={'ticker': '375770', 'name': '추가 ETF A', 'group': 'korea'})
    args.update(overrides)
    return apply_trade(opening() if saved is None else saved, **args)


class MultiETFTest(unittest.TestCase):
    def test_new_funds_keep_legacy_opening_and_unknown_metadata(self):
        old = trade()
        original = copy.deepcopy(old)
        first = new_trade(old)
        second = new_trade(first, trade_id='new-2', new_etf={
            'ticker': '123450.KS', 'name': '추가 ETF B', 'group': 'sp'})
        self.assertEqual(old, original)
        for key in original:
            if key not in ('transactions', 'schema_version'):
                self.assertEqual(second[key], original[key])
        self.assertEqual(second['transactions'][0], original['transactions'][0])
        self.assertEqual(len(second['etf_positions']), 2)
        self.assertEqual(len(second['transactions']), 3)
        self.assertEqual(second['etf_positions']['etf:375770.KS']['qty'], 10)

    def test_repeated_buys_sale_and_reentry_per_fund(self):
        first = new_trade()
        second = new_trade(first, trade_id='add', asset='etf:375770.KS', new_etf=None, quantity=5, price=12000)
        row = second['etf_positions']['etf:375770.KS']
        self.assertEqual(row['qty'], 15)
        self.assertAlmostEqual(row['avg'], 160000 / 15)
        sold = new_trade(second, trade_id='sell', asset='etf:375770.KS', new_etf=None,
                         side='sell', quantity=3, price=11000)
        self.assertAlmostEqual(sold['transactions'][-1]['realized_profit'], 1000)
        self.assertAlmostEqual(sold['etf_positions']['etf:375770.KS']['avg'], row['avg'])
        closed = new_trade(sold, trade_id='close', asset='etf:375770.KS', new_etf=None, side='sell', quantity=12)
        self.assertEqual(closed['etf_positions']['etf:375770.KS']['qty'], 0)
        self.assertEqual(closed['etf_positions']['etf:375770.KS']['avg'], 0)
        entered = new_trade(closed, trade_id='again', asset='etf:375770.KS', new_etf=None, quantity=2, price=9000)
        self.assertEqual(entered['etf_positions']['etf:375770.KS']['avg'], 9000)
        self.assertEqual(len(entered['transactions']), 5)

    def test_cash_optional_and_idempotent(self):
        first = new_trade(reflect_cash=True)
        self.assertEqual(first['safe_now'], 28532)
        self.assertEqual(new_trade(first, reflect_cash=True), first)
        self.assertEqual(new_trade()['safe_now'], opening()['safe_now'])

    def test_duplicates_include_legacy_and_different_market_suffix(self):
        for ticker in ('292150', '360750.KS', '292150.KQ'):
            with self.subTest(ticker=ticker), self.assertRaisesRegex(ValueError, '이미 등록'):
                new_trade(new_etf={'ticker': ticker, 'name': '중복', 'group': 'korea'})
        with self.assertRaisesRegex(ValueError, '이미 등록'):
            new_trade(new_trade(), trade_id='duplicate')

    def test_new_sale_and_bad_definition_fail_without_mutation(self):
        saved = opening()
        for overrides in ({'side': 'sell'}, {'new_etf': None},
                {'new_etf': {'ticker': 'bad', 'name': 'X', 'group': 'korea'}},
                {'new_etf': {'ticker': '375770', 'name': '', 'group': 'korea'}},
                {'new_etf': {'ticker': '375770', 'name': 'X', 'group': 'wrong'}}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                new_trade(saved, **overrides)
        self.assertEqual(saved, opening())

    def test_oversell_backdate_and_bad_numbers_fail(self):
        saved = new_trade()
        for overrides in ({'side': 'sell', 'quantity': 11}, {'trade_date': '2026-10-07'},
                          {'quantity': 1.5}, {'price': float('nan')}, {'quantity': float('inf')}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                new_trade(saved, trade_id='fail', asset='etf:375770.KS', new_etf=None, **overrides)

    def test_valuation_and_allocation_aggregate_buckets_once(self):
        saved = new_trade()
        prices = {'sp': 26230, 'korea': 39860, 'etf:375770.KS': 11000}
        summary = portfolio_summary(saved, prices)
        self.assertEqual(summary['total'], 4020082)
        self.assertEqual(summary['profit'], 242810)
        self.assertEqual(summary['group_values']['korea'], 3498100)
        self.assertAlmostEqual(sum(summary['weights'].values()), 100)
        self.assertAlmostEqual(sum(summary['buys'].values()), 500000)
        bond = new_trade(new_etf={'ticker': '123450', 'name': '채권 ETF', 'group': 'safe'})
        summary = portfolio_summary(bond, {'sp': 26230, 'korea': 39860, 'etf:123450.KS': 11000})
        self.assertEqual(summary['total'], 4020082)
        self.assertEqual(summary['group_values']['safe'], 238532)

    def test_missing_extra_quote_blocks_totals_not_balances(self):
        saved = new_trade()
        summary = portfolio_summary(saved, {'sp': 26230, 'korea': 39860})
        self.assertFalse(summary['complete'])
        self.assertIsNone(summary['profit'])
        self.assertIsNone(summary['positions']['etf:375770.KS']['profit'])
        saved = new_trade(saved, trade_id='close', asset='etf:375770.KS', new_etf=None, side='sell')
        self.assertTrue(portfolio_summary(saved, {'sp': 26230, 'korea': 39860})['complete'])

    def test_malformed_extra_data_fails_closed(self):
        for extra in ([], {'etf:375770.KS': {}}, {'bad': {'ticker': '375770', 'name': 'X', 'group': 'korea', 'qty': 1, 'avg': 1}}):
            saved = opening()
            saved['etf_positions'] = extra
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                pension_positions(saved)

    def test_adjustment_keeps_ledger_and_audit_no_ticker_change(self):
        saved = new_trade()
        updated = adjust_extra_position(saved, 'etf:375770.KS', quantity=12, average=10500,
                                         name='이름 수정', group='sp')
        self.assertEqual(updated['transactions'], saved['transactions'])
        self.assertEqual(updated['etf_positions']['etf:375770.KS']['ticker'], '375770.KS')
        self.assertEqual(updated['balance_adjustments'][-1]['before']['qty'], 10)
        self.assertEqual(updated['balance_adjustments'][-1]['after']['qty'], 12)
        self.assertNotIn('balance_adjustments', saved)

    def test_normalize_and_reject_wrong_suffix(self):
        self.assertEqual(normalize_ticker(' 375770.ks '), '375770.KS')
        for value in ('375770.US', '005930.KS.evil', '１２３４５６', '', 'AAPL', '1234567'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_ticker(value)


if __name__ == '__main__':
    unittest.main()
