"""Run with unittest; all API responses are fake and no messages are sent."""
import ast
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kakao_error_details import kakao_error_message

SECRET = "TEST_ONLY_SECRET_MUST_NEVER_APPEAR"


class FakeResponse:
    def __init__(self, payload, status=400):
        self.payload = payload
        self.status_code = status
        self.ok = type(status) is int and 200 <= status < 300

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class NetworkError(Exception):
    pass


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / "korea_holdings_ui.py").read_text(encoding="utf-8"))
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name in {"refresh_kakao_token", "send_kakao_message"}]
        self.requests = types.SimpleNamespace(post=Mock(), RequestException=NetworkError)
        self.env = {"requests": self.requests, "json": json,
                    "secret_value": lambda key, default="": SECRET,
                    "kakao_error_message": kakao_error_message}
        exec(compile(ast.Module(body=selected, type_ignores=[]), "kakao_functions", "exec"), self.env)

    def test_expired_refresh_token_hint(self):
        value = kakao_error_message(FakeResponse({"error_code":"KOE322", "error":"invalid_grant"}))
        self.assertIn("HTTP 400 · KOE322 / invalid_grant", value)
        self.assertIn("KAKAO_REFRESH_TOKEN", value)

    def test_client_secret_hint(self):
        value = kakao_error_message(FakeResponse({"error_code":"KOE010"}))
        self.assertIn("KAKAO_CLIENT_SECRET", value)
        self.assertIn("일치하지", value)

    def test_rest_key_hint(self):
        self.assertIn("KAKAO_REST_API_KEY", kakao_error_message(FakeResponse({"error_code":"KOE101"})))

    def test_empty_refresh_token_hint(self):
        self.assertIn("전달되지", kakao_error_message(FakeResponse({"error_code":"KOE319"})))

    def test_rate_limit_hint(self):
        self.assertIn("한도", kakao_error_message(FakeResponse({"error_code":"KOE237"}, 429)))

    def test_message_consent_hint(self):
        value = kakao_error_message(FakeResponse({"code":-402,"msg":SECRET},403), "message")
        self.assertIn("메시지 전송 실패", value)
        self.assertIn("사용자 동의", value)
        self.assertNotIn(SECRET, value)

    def test_all_free_text_fields_hidden(self):
        payload = {"error_code":"KOE322", "error":"invalid_grant",
                   "error_description":SECRET, "msg":SECRET, "message":SECRET,
                   "access_token":SECRET, "refresh_token":SECRET, "client_id":SECRET,
                   "client_secret":SECRET, "request":{"headers":{"Authorization":SECRET}}}
        self.assertNotIn(SECRET, kakao_error_message(FakeResponse(payload)))

    def test_arbitrary_code_and_error_not_echoed(self):
        for field in ("error_code","code","error"):
            with self.subTest(field=field):
                self.assertNotIn(SECRET, kakao_error_message(FakeResponse({field:SECRET})))

    def test_unknown_well_formed_oauth_code_is_not_guessed(self):
        value = kakao_error_message(FakeResponse({"error_code":"KOE999", "error_description":SECRET}))
        self.assertIn("KOE999", value)
        self.assertIn("확정할 수 없습니다", value)
        self.assertNotIn(SECRET, value)

    def test_unknown_numeric_id_not_echoed(self):
        self.assertNotIn("123456789", kakao_error_message(FakeResponse({"code":123456789})))

    def test_non_json_and_wrong_shapes_safe(self):
        for payload in (ValueError(SECRET), [SECRET], SECRET, None, 42):
            with self.subTest(shape=type(payload).__name__):
                value = kakao_error_message(FakeResponse(payload))
                self.assertIn("HTTP 400", value)
                self.assertIn("상세코드 없음", value)
                self.assertNotIn(SECRET, value)

    def test_malformed_status_not_echoed(self):
        self.assertNotIn(SECRET, kakao_error_message(FakeResponse({}, SECRET)))

    def test_token_function_uses_safe_formatter(self):
        self.requests.post.return_value = FakeResponse({"error_code":"KOE322", "error_description":SECRET})
        with self.assertRaises(RuntimeError) as context:
            self.env["refresh_kakao_token"]()
        self.assertIn("KOE322", str(context.exception))
        self.assertNotIn(SECRET, str(context.exception))
        self.assertEqual(self.requests.post.call_count, 1)

    def test_failed_refresh_never_sends_message(self):
        self.requests.post.return_value = FakeResponse({"error_code":"KOE010"})
        with self.assertRaises(RuntimeError):
            self.env["send_kakao_message"]("sample")
        self.assertEqual(self.requests.post.call_count, 1)

    def test_refresh_network_error_hidden(self):
        self.requests.post.side_effect = NetworkError(SECRET)
        with self.assertRaises(RuntimeError) as context:
            self.env["refresh_kakao_token"]()
        self.assertNotIn(SECRET, str(context.exception))
        self.assertTrue(context.exception.__suppress_context__)

    def test_refresh_bad_success_payload_hidden(self):
        for payload in (ValueError(SECRET), [SECRET], {}, {"access_token":123}, {"access_token":" "}):
            with self.subTest(shape=type(payload).__name__):
                self.requests.post.return_value = FakeResponse(payload, 200)
                with self.assertRaises(RuntimeError) as context:
                    self.env["refresh_kakao_token"]()
                self.assertNotIn(SECRET, str(context.exception))

    def test_refresh_success_request_unchanged(self):
        self.requests.post.return_value = FakeResponse({"access_token":"FAKE_ACCESS"}, 200)
        self.assertEqual(self.env["refresh_kakao_token"](), "FAKE_ACCESS")
        args, kwargs = self.requests.post.call_args
        self.assertEqual(args[0], "https://kauth.kakao.com/oauth/token")
        self.assertEqual(kwargs["data"], {"grant_type":"refresh_token", "client_id":SECRET,
                         "refresh_token":SECRET, "client_secret":SECRET})

    def test_message_network_error_hides_token(self):
        self.requests.post.side_effect = [FakeResponse({"access_token":"FAKE_ACCESS"},200), NetworkError(SECRET)]
        with self.assertRaises(RuntimeError) as context:
            self.env["send_kakao_message"]("sample")
        self.assertIn("수신 여부", str(context.exception))
        self.assertNotIn(SECRET, str(context.exception))
        self.assertTrue(context.exception.__suppress_context__)

    def test_message_api_error_safe(self):
        self.requests.post.side_effect = [FakeResponse({"access_token":"FAKE_ACCESS"},200),
                                         FakeResponse({"code":-401, "msg":SECRET},401)]
        with self.assertRaises(RuntimeError) as context:
            self.env["send_kakao_message"]("sample")
        self.assertIn("HTTP 401 · -401", str(context.exception))
        self.assertNotIn(SECRET, str(context.exception))

    def test_successful_message_behavior_unchanged(self):
        self.requests.post.side_effect = [FakeResponse({"access_token":"FAKE_ACCESS"},200), FakeResponse({"result_code":0},200)]
        self.assertIsNone(self.env["send_kakao_message"]("sample"))
        self.assertEqual(self.requests.post.call_count, 2)
        _, kwargs = self.requests.post.call_args
        self.assertEqual(kwargs["headers"], {"Authorization":"Bearer FAKE_ACCESS"})
        self.assertEqual(json.loads(kwargs["data"]["template_object"])["text"], "sample")


if __name__ == "__main__":
    unittest.main()
