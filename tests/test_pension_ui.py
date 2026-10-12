from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

APP = Path(__file__).parent / "fixtures" / "pension_app.py"


def widget(at, kind, label):
    return next(w for w in getattr(at, kind) if w.label == label)


class PensionUITest(unittest.TestCase):
    def app(self):
        at = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assertFalse(at.exception)
        return at

    def buy(self, at, qty=5, price=26000, side="buy", reflect_cash=False):
        widget(at, "selectbox", "거래 자산").select("sp").run()
        widget(at, "selectbox", "매수 / 매도").select(side)
        widget(at, "checkbox", "거래대금을 채권·현금성 평가액에도 반영").set_value(reflect_cash)
        widget(at, "number_input", "실제 체결수량 (주)").set_value(qty)
        widget(at, "number_input", "실제 체결가격 (원/주)").set_value(price)
        widget(at, "checkbox", "증권사에서 체결된 수량·가격을 확인했습니다.").check()
        widget(at, "button", "체결내역 저장").click().run()
        self.assertFalse(at.exception)

    def test_initial_no_writes(self):
        at = self.app()
        self.assertEqual(at.session_state["fixture_writes"], 0)
        self.assertEqual(widget(at, "number_input", "S&P500 ETF 보유수량").value, 15)

    def test_quote_refresh_no_writes(self):
        at = self.app()
        widget(at, "button", "🔄 현재가 다시 조회").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["fixture_writes"], 0)

    def test_manual_cash_option_updates_cash_balance(self):
        at = self.app()
        self.buy(at, qty=2, reflect_cash=True)
        self.assertEqual(at.session_state["fixture_data"]["safe_now"], 76532)
        self.assertEqual(widget(at, "text_input", "채권·현금성 평가액").value, "76,532원")

    def test_buy_then_partial_sale_updates_widgets_and_ledger(self):
        at = self.app()
        self.buy(at)
        self.assertEqual(widget(at, "number_input", "S&P500 ETF 보유수량").value, 20)
        self.assertEqual(widget(at, "number_input", "S&P500 ETF 평균매수가").value, 26202.5)
        self.assertEqual(at.session_state["fixture_data"]["extra_metadata"], "keep-me")
        self.assertEqual(widget(at, "number_input", "실제 체결수량 (주)").value, 0)
        self.buy(at, qty=3, price=27000, side="sell")
        data = at.session_state["fixture_data"]
        self.assertEqual(data["sp_qty"], 17)
        self.assertEqual(data["sp_avg"], 26202.5)
        self.assertEqual(data["transactions"][-1]["realized_profit"], 2392.5)
        self.assertEqual(at.session_state["fixture_writes"], 2)
        at.run()
        self.assertEqual(at.session_state["fixture_writes"], 2)

    def test_oversell_not_saved(self):
        at = self.app()
        self.buy(at, qty=16, side="sell")
        self.assertTrue(any("초과" in e.value for e in at.error))
        self.assertEqual(at.session_state["fixture_writes"], 0)

    def test_confirmation_required(self):
        at = self.app()
        widget(at, "button", "체결내역 저장").click().run()
        self.assertTrue(any("체크" in e.value for e in at.error))
        self.assertEqual(at.session_state["fixture_writes"], 0)

    def test_settings_save_preserves_ledger_and_fractional_average(self):
        at = self.app()
        self.buy(at)
        widget(at, "number_input", "월 납입액").set_value(600000).run()
        self.assertTrue(widget(at, "button", "체결내역 저장").disabled)
        widget(at, "button", "💾 연금 보유정보 저장").click().run()
        self.assertFalse(at.exception)
        data = at.session_state["fixture_data"]
        self.assertEqual(len(data["transactions"]), 1)
        self.assertEqual(data["sp_avg"], 26202.5)
        self.assertEqual(data["monthly"], 600000)

    def test_missing_quote_blocks_totals_not_input(self):
        at = self.app()
        at.session_state["fixture_missing"] = True
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(any("계산을 보류" in w.value for w in at.warning))
        self.assertEqual(widget(at, "metric", "총 평가액").value, "-")
        table_text = str(at.dataframe[-1].value)
        self.assertNotIn("-100.00%", table_text)
        self.assertFalse(widget(at, "button", "체결내역 저장").disabled)
        widget(at, "number_input", "S&P500 수동 현재가 (0=미입력)").set_value(26230).run()
        self.assertFalse(at.exception)
        self.assertEqual(widget(at, "metric", "총 평가액").value, "3,910,082원")

    def test_load_failure_no_zero_portfolio_or_save_controls(self):
        at = self.app()
        at.session_state["fixture_load_failure"] = True
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(at.error)
        self.assertEqual(len(at.button), 0)
        self.assertEqual(len(at.dataframe), 0)

    def test_stale_snapshot_blocks_save_and_reload_recovers(self):
        at = self.app()
        data = dict(at.session_state["fixture_data"])
        data["sp_qty"] = 25.0
        at.session_state["fixture_data"] = data
        at.session_state["fixture_sha"] = "other-session-sha"
        at.run()
        self.assertTrue(widget(at, "button", "체결내역 저장").disabled)
        self.assertTrue(widget(at, "button", "💾 연금 보유정보 저장").disabled)
        widget(at, "button", "저장된 최신정보 불러오기").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(widget(at, "number_input", "S&P500 ETF 보유수량").value, 25)
        self.assertFalse(widget(at, "button", "체결내역 저장").disabled)

    def new_buy(self, at, code="375770", name="추가 ETF A"):
        widget(at, "selectbox", "거래 자산").select("new").run()
        widget(at, "text_input", "새 ETF 이름").set_value(name)
        widget(at, "text_input", "새 ETF 종목코드").set_value(code)
        widget(at, "number_input", "실제 체결수량 (주)").set_value(10)
        widget(at, "number_input", "실제 체결가격 (원/주)").set_value(10000)
        widget(at, "checkbox", "증권사에서 체결된 수량·가격을 확인했습니다.").check()
        widget(at, "button", "체결내역 저장").click().run()
        self.assertFalse(at.exception)

    def test_multiple_new_funds_and_same_fund_buy_preserve_legacy(self):
        at = self.app()
        self.new_buy(at)
        self.new_buy(at, "123450", "추가 ETF B")
        data = at.session_state["fixture_data"]
        self.assertEqual(len(data["etf_positions"]), 2)
        self.assertEqual(data["korea_qty"], 85)
        self.assertEqual(data["sp_qty"], 15)
        self.assertIn("추가 ETF A", str(at.dataframe[-1].value))
        self.assertIn("추가 ETF B", str(at.dataframe[-1].value))
        widget(at, "selectbox", "거래 자산").select("etf:375770.KS").run()
        widget(at, "number_input", "실제 체결수량 (주)").set_value(5)
        widget(at, "number_input", "실제 체결가격 (원/주)").set_value(12000)
        widget(at, "checkbox", "증권사에서 체결된 수량·가격을 확인했습니다.").check()
        widget(at, "button", "체결내역 저장").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["fixture_data"]["etf_positions"]["etf:375770.KS"]["qty"], 15)
        self.assertAlmostEqual(at.session_state["fixture_data"]["etf_positions"]["etf:375770.KS"]["avg"], 160000/15)
        self.assertEqual(at.session_state["fixture_writes"], 3)

    def test_new_duplicate_base_or_extra_does_not_save(self):
        at = self.app()
        self.new_buy(at, "292150")
        self.assertTrue(any("이미 등록" in e.value for e in at.error))
        self.assertEqual(at.session_state["fixture_writes"], 0)
        self.new_buy(at)
        self.new_buy(at)
        self.assertTrue(any("이미 등록" in e.value for e in at.error))
        self.assertEqual(at.session_state["fixture_writes"], 1)

    def test_new_invalid_input_no_save(self):
        at = self.app()
        self.new_buy(at, "bad")
        self.assertTrue(at.error)
        self.assertEqual(at.session_state["fixture_writes"], 0)

    def test_settings_preserve_extras_and_transaction_names(self):
        at = self.app()
        self.new_buy(at)
        expected = dict(at.session_state["fixture_data"]["etf_positions"])
        widget(at, "number_input", "월 납입액").set_value(600000).run()
        widget(at, "button", "💾 연금 보유정보 저장").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["fixture_data"]["etf_positions"], expected)
        self.assertEqual(at.session_state["fixture_data"]["transactions"][-1]["name"], "추가 ETF A")

    def test_extra_missing_price_holds_totals_not_record_input(self):
        at = self.app()
        self.new_buy(at)
        at.session_state["fixture_missing_extra"] = True
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(widget(at, "metric", "총 평가액").value, "-")
        self.assertFalse(widget(at, "button", "체결내역 저장").disabled)
        self.assertNotIn("-100.00%", str(at.dataframe[-1].value))
        widget(at, "number_input", "추가 ETF A (375770.KS) 수동 현재가 (0=미입력)").set_value(11000).run()
        self.assertEqual(widget(at, "metric", "총 평가액").value, "4,020,082원")

    def test_extra_correction_requires_confirmation_and_keeps_trade(self):
        at = self.app()
        self.new_buy(at)
        widget(at, "number_input", "보정 보유수량").set_value(12)
        widget(at, "button", "추가 ETF 잔고 보정 저장").click().run()
        self.assertEqual(at.session_state["fixture_writes"], 1)
        widget(at, "checkbox", "해당 ETF 잔고를 직접 보정하는 것을 확인했습니다.").check()
        widget(at, "button", "추가 ETF 잔고 보정 저장").click().run()
        self.assertFalse(at.exception)
        data = at.session_state["fixture_data"]
        self.assertEqual(len(data["transactions"]), 1)
        self.assertEqual(data["etf_positions"]["etf:375770.KS"]["qty"], 12)
        self.assertEqual(len(data["balance_adjustments"]), 1)

    def test_held_base_ticker_cannot_be_replaced(self):
        at = self.app()
        widget(at, "text_input", "KOREA TOP10 티커").set_value("375770").run()
        widget(at, "button", "💾 연금 보유정보 저장").click().run()
        self.assertTrue(any("덮어쓸" in e.value for e in at.error))
        self.assertEqual(at.session_state["fixture_writes"], 0)

    def test_quote_rerun_keeps_new_form_and_never_saves(self):
        at = self.app()
        widget(at, "selectbox", "거래 자산").select("new").run()
        widget(at, "text_input", "새 ETF 이름").set_value("임시 ETF")
        widget(at, "number_input", "실제 체결수량 (주)").set_value(3)
        widget(at, "button", "🔄 현재가 다시 조회").click().run()
        self.assertEqual(widget(at, "text_input", "새 ETF 이름").value, "임시 ETF")
        self.assertEqual(widget(at, "number_input", "실제 체결수량 (주)").value, 3)
        self.assertEqual(at.session_state["fixture_writes"], 0)

    def clear_base_holdings(self, at):
        data = dict(at.session_state["fixture_data"])
        data.update(korea_qty=0, korea_avg=0, sp_qty=0, sp_avg=0)
        at.session_state["fixture_data"] = data
        at.run()
        self.assertFalse(at.exception)

    def test_replacement_only_held_etf_gets_price_card_and_table_row(self):
        at = self.app()
        self.new_buy(at)
        self.clear_base_holdings(at)
        labels = [m.label for m in at.metric]
        self.assertIn("추가 ETF A 현재가", labels)
        self.assertNotIn("KOREA TOP10 현재가", labels)
        self.assertNotIn("S&P500 현재가", labels)
        self.assertEqual(at.session_state["fixture_quote_calls"], ["375770.KS"])
        self.assertEqual(list(at.dataframe[-1].value["자산"]), ["추가 ETF A", "채권·현금성"])
        self.assertEqual(len(at.session_state["fixture_data"]["transactions"]), 1)
        self.assertEqual(at.session_state["fixture_writes"], 1)

    def test_all_held_etfs_have_cards_including_more_than_three(self):
        at = self.app()
        self.new_buy(at)
        self.new_buy(at, "123450", "추가 ETF B")
        self.assertEqual({m.label for m in at.metric if m.label.endswith(" 현재가")},
                         {"KOREA TOP10 현재가", "S&P500 현재가", "추가 ETF A 현재가", "추가 ETF B 현재가"})
        self.assertEqual(set(at.session_state["fixture_quote_calls"]), {"292150.KS", "360750.KS", "375770.KS", "123450.KS"})
        self.assertEqual(len(at.dataframe[-1].value), 5)

    def test_no_holdings_no_quote_calls_and_no_phantom_etf_rows(self):
        at = self.app()
        self.clear_base_holdings(at)
        self.assertFalse(any(m.label.endswith(" 현재가") for m in at.metric))
        self.assertEqual(at.session_state["fixture_quote_calls"], [])
        self.assertTrue(any("현재 보유 중인 ETF가 없습니다" in i.value for i in at.info))
        self.assertEqual(list(at.dataframe[-1].value["자산"]), ["채권·현금성"])
        self.assertEqual(widget(at, "metric", "총 평가액").value, "128,532원")
        self.assertEqual(at.session_state["fixture_writes"], 0)

    def test_full_sale_hides_card_preserves_history_and_reentry_option(self):
        at = self.app()
        self.new_buy(at)
        widget(at, "selectbox", "거래 자산").select("etf:375770.KS").run()
        widget(at, "selectbox", "매수 / 매도").select("sell")
        widget(at, "number_input", "실제 체결수량 (주)").set_value(10)
        widget(at, "number_input", "실제 체결가격 (원/주)").set_value(11000)
        widget(at, "checkbox", "증권사에서 체결된 수량·가격을 확인했습니다.").check()
        widget(at, "button", "체결내역 저장").click().run()
        self.assertFalse(at.exception)
        self.assertNotIn("추가 ETF A 현재가", [m.label for m in at.metric])
        self.assertNotIn("375770.KS", at.session_state["fixture_quote_calls"])
        self.assertNotIn("추가 ETF A", list(at.dataframe[-1].value["자산"]))
        self.assertEqual(len(at.session_state["fixture_data"]["transactions"]), 2)
        self.assertTrue(any("추가 ETF A" in option for option in widget(at, "selectbox", "거래 자산").options))
        self.assertEqual(at.session_state["fixture_writes"], 2)

    def test_failed_held_quote_still_shows_card_without_old_etfs(self):
        at = self.app()
        self.new_buy(at)
        self.clear_base_holdings(at)
        at.session_state["fixture_missing_extra"] = True
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(widget(at, "metric", "추가 ETF A 현재가").value, "조회 불가")
        self.assertEqual(widget(at, "metric", "총 평가액").value, "-")
        self.assertEqual(at.session_state["fixture_quote_calls"], ["375770.KS"])


if __name__ == "__main__":
    unittest.main()

