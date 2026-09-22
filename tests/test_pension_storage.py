import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from test_pension_transactions import opening, trade


class PensionStorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fake = types.ModuleType("korea_live_price")
        fake.get_live_price = lambda code, market: None
        fake.price_source_label = lambda: "TEST"
        spec = importlib.util.spec_from_file_location(
            "pension_storage_under_test", Path(__file__).resolve().parents[1] / "pension_manager_ui.py")
        cls.ui = importlib.util.module_from_spec(spec)
        previous = sys.modules.get("korea_live_price")
        sys.modules["korea_live_price"] = fake
        try:
            spec.loader.exec_module(cls.ui)
        finally:
            if previous is None:
                sys.modules.pop("korea_live_price", None)
            else:
                sys.modules["korea_live_price"] = previous

    def response(self, status=200, data=None):
        return Mock(status_code=status, json=Mock(return_value=data))

    def test_read_existing_legacy_snapshot(self):
        response = self.response(data={"sha": "old", "content": base64.b64encode(
            json.dumps(opening()).encode()).decode()})
        with patch.object(self.ui.requests, "get", return_value=response):
            data, sha = self.ui._load_pension()
        self.assertEqual(sha, "old")
        self.assertEqual(data["sp_qty"], 15)

    def test_auth_and_missing_file_fail_closed(self):
        for status in (401, 403, 404, 500):
            with self.subTest(status=status), patch.object(self.ui.requests, "get",
                    return_value=self.response(status)), self.assertRaises(RuntimeError):
                self.ui._load_pension()

    def test_malformed_response_fail_closed(self):
        with patch.object(self.ui.requests, "get", return_value=self.response(data={})), \
                self.assertRaises(RuntimeError):
            self.ui._load_pension()

    def test_save_atomic_payload_preserves_ledger(self):
        data = trade()
        with patch.object(self.ui, "_secret", return_value="TEST-NOT-A-REAL-KEY"), \
             patch.object(self.ui.requests, "put", return_value=self.response(data={"content": {"sha": "new"}})) as put:
            self.assertEqual(self.ui._save_pension(data, "old"), "new")
        payload = put.call_args.kwargs["json"]
        self.assertEqual(payload["sha"], "old")
        decoded = json.loads(base64.b64decode(payload["content"]))
        self.assertEqual(decoded, data)
        self.assertEqual(len(decoded["transactions"]), 1)

    def test_conflict_never_force_overwrites(self):
        with patch.object(self.ui, "_secret", return_value="TEST"), \
             patch.object(self.ui.requests, "put", return_value=self.response(409)) as put, \
             self.assertRaisesRegex(RuntimeError, "변경"):
            self.ui._save_pension(opening(), "old")
        self.assertEqual(put.call_count, 1)

    def test_missing_sha_no_write(self):
        with patch.object(self.ui, "_secret", return_value="TEST"), \
             patch.object(self.ui.requests, "put") as put, self.assertRaises(RuntimeError):
            self.ui._save_pension(opening(), None)
        put.assert_not_called()

    def test_trade_rechecks_snapshot_before_write(self):
        with patch.object(self.ui, "_load_pension", return_value=(opening(), "new")), \
             patch.object(self.ui, "_save_pension") as save, self.assertRaisesRegex(RuntimeError, "변경"):
            self.ui._persist_trade("old", trade_id="different")
        save.assert_not_called()

    def test_trade_retry_after_lost_response_is_noop(self):
        saved = trade()
        original = copy.deepcopy(saved)
        with patch.object(self.ui, "_load_pension", return_value=(saved, "new")), \
             patch.object(self.ui, "_save_pension") as save:
            data, sha = self.ui._persist_trade("old", trade_id="unique-1")
        save.assert_not_called()
        self.assertEqual(data, original)
        self.assertEqual(sha, "new")


if __name__ == "__main__":
    unittest.main()
