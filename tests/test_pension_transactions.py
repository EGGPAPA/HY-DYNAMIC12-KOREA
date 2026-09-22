import copy
from datetime import date
import unittest

from pension_transactions import apply_trade, portfolio_summary, valid_price


def opening():
    return {"monthly": 500000, "korea_ticker": "292150.KS", "korea_qty": 85,
            "korea_avg": 37114, "sp_ticker": "360750.KS", "sp_qty": 15,
            "sp_avg": 26270, "safe_now": 128532, "custom_metadata": {"keep": True}}


def trade(saved=None, **overrides):
    args = dict(trade_id="unique-1", asset="sp", side="buy", quantity=5, price=26000,
                trade_date="2026-09-22", today=date(2026, 9, 22))
    args.update(overrides)
    return apply_trade(opening() if saved is None else saved, **args)


class TradesTest(unittest.TestCase):
    def test_buy_weighted_average_preserves_data(self):
        original = opening()
        result = trade(original)
        self.assertEqual(result["sp_qty"], 20)
        self.assertEqual(result["sp_avg"], 26202.5)
        self.assertEqual(result["korea_qty"], 85)
        self.assertEqual(result["safe_now"], 128532)
        self.assertEqual(result["custom_metadata"], original["custom_metadata"])
        self.assertEqual(result["opening_balance"]["sp_qty"], 15)
        self.assertNotIn("transactions", original)
        self.assertIsNone(result["transactions"][0]["realized_profit"])

    def test_partial_sale_preserves_average(self):
        result = trade(side="sell", quantity=3, price=27000)
        self.assertEqual(result["sp_qty"], 12)
        self.assertEqual(result["sp_avg"], 26270)
        self.assertEqual(result["transactions"][0]["realized_profit"], 2190)

    def test_full_sale_and_reentry(self):
        result = trade(side="sell", quantity=15)
        self.assertEqual((result["sp_qty"], result["sp_avg"]), (0, 0))
        result = trade(result, trade_id="reenter", quantity=2, price=28000)
        self.assertEqual((result["sp_qty"], result["sp_avg"]), (2, 28000))
        self.assertEqual(len(result["transactions"]), 2)

    def test_no_oversell_or_mutation(self):
        saved = opening()
        original = copy.deepcopy(saved)
        with self.assertRaisesRegex(ValueError, "초과"):
            trade(saved, side="sell", quantity=16)
        self.assertEqual(saved, original)

    def test_bad_quantity_price_rejected(self):
        for field in ("price", "quantity"):
            for bad in (0, -1, None, float("nan"), float("inf"), "bad", 1.5):
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    trade(**{field: bad})

    def test_future_or_invalid_date_rejected(self):
        for bad in ("2026-09-23", "invalid", "2026-02-30"):
            with self.subTest(date=bad), self.assertRaises(ValueError):
                trade(trade_date=bad)

    def test_backdated_same_asset_blocked(self):
        saved = trade()
        with self.assertRaisesRegex(ValueError, "순서"):
            trade(saved, trade_id="earlier", trade_date="2026-09-21")
        self.assertEqual(len(trade(saved, trade_id="korea-earlier", asset="korea",
                                   trade_date="2026-09-21")["transactions"]), 2)

    def test_cash_reflection_opt_in(self):
        bought = trade(quantity=2, reflect_cash=True)
        self.assertEqual(bought["safe_now"], 76532)
        sold = trade(bought, trade_id="sale", side="sell", quantity=1, price=27000,
                     reflect_cash=True)
        self.assertEqual(sold["safe_now"], 103532)

    def test_cash_insufficient_blocked(self):
        with self.assertRaisesRegex(ValueError, "잔액"):
            trade(reflect_cash=True)

    def test_duplicate_id_does_not_apply_twice(self):
        first = trade()
        self.assertEqual(trade(first), first)

    def test_fractional_average_retained_on_repeated_buys(self):
        result = trade(quantity=1, price=26001)
        self.assertAlmostEqual(result["sp_avg"], (15 * 26270 + 26001) / 16)
        second = trade(result, trade_id="second", quantity=1, price=26002)
        self.assertAlmostEqual(second["sp_avg"], (15 * 26270 + 26001 + 26002) / 17)

    def test_invalid_existing_cost_basis_blocked(self):
        saved = opening()
        saved["sp_avg"] = 0
        with self.assertRaisesRegex(ValueError, "기존 평균"):
            trade(saved)

    def test_invalid_ledger_and_asset_blocked(self):
        for transactions in ({}, [None]):
            saved = opening()
            saved["transactions"] = transactions
            with self.assertRaises(ValueError):
                trade(saved)
        with self.assertRaises(ValueError):
            trade(asset="unknown")


class ValuationTest(unittest.TestCase):
    def test_missing_quote_blocks_pnl_and_allocation(self):
        for missing in (None, 0, -1, float("nan"), float("inf")):
            with self.subTest(missing=missing):
                summary = portfolio_summary(opening(), {"sp": missing, "korea": 39920})
                self.assertFalse(summary["complete"])
                self.assertIsNone(summary["positions"]["sp"]["profit"])
                for key in ("total", "rate", "profit", "weights", "buys"):
                    self.assertIsNone(summary[key])
                self.assertEqual(summary["positions"]["korea"]["value"], 3393200)

    def test_good_quotes_reproduce_arithmetic(self):
        summary = portfolio_summary(opening(), {"sp": 26230, "korea": 39860})
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["total"], 3910082)
        self.assertEqual(summary["profit"], 232810)
        self.assertAlmostEqual(sum(summary["weights"].values()), 100)
        self.assertAlmostEqual(sum(summary["buys"].values()), 500000)

    def test_zero_holdings_dont_require_quote(self):
        saved = opening()
        saved["sp_qty"] = 0
        self.assertTrue(portfolio_summary(saved, {"korea": 39920})["complete"])

    def test_all_cash_allocation(self):
        saved = opening()
        saved.update(sp_qty=0, korea_qty=0, safe_now=0)
        summary = portfolio_summary(saved, {})
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["buys"], {"sp": 250000, "korea": 150000, "safe": 100000})

    def test_valid_price(self):
        self.assertTrue(valid_price(26230))
        for bad in (None, 0, -1, "bad", float("nan"), float("inf")):
            self.assertFalse(valid_price(bad))


if __name__ == "__main__":
    unittest.main()
