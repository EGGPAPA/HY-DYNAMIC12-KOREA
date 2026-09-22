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

    def buy(self, at, qty=5, price=26000, side="buy"):
        widget(at, "selectbox", "거래 자산").select("sp")
        widget(at, "selectbox", "매수 / 매도").select(side)
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
        widget(at, "checkbox", "거래대금을 채권·현금성 평가액에도 반영").check()
        self.buy(at, qty=2)
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


if __name__ == "__main__":
    unittest.main()
