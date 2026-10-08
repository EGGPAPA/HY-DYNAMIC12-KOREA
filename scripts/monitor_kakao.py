"""Checked Kakao delivery with bounded, secret-safe diagnostics."""
import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kakao_error_details import kakao_error_message


class KakaoError(RuntimeError):
    pass


def payload(response, operation):
    if not response.ok:
        raise KakaoError(kakao_error_message(response, operation))
    try:
        data = response.json()
    except ValueError:
        raise KakaoError("Kakao returned invalid JSON") from None
    if not isinstance(data, dict):
        raise KakaoError("Kakao returned an invalid response")
    return data


def post(url, **kwargs):
    try:
        return requests.post(url, timeout=20, **kwargs)
    except requests.RequestException:
        raise KakaoError("Kakao network failure; delivery is unconfirmed") from None


def access_token():
    required = ("KAKAO_REST_API_KEY", "KAKAO_REFRESH_TOKEN")
    if any(not os.getenv(key, "").strip() for key in required):
        raise KakaoError("Kakao credentials missing")
    data = {"grant_type": "refresh_token", "client_id": os.environ[required[0]],
            "refresh_token": os.environ[required[1]]}
    if os.getenv("KAKAO_CLIENT_SECRET"):
        data["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
    result = payload(post("https://kauth.kakao.com/oauth/token", data=data), "token")
    token = result.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise KakaoError("Kakao access token missing")
    return token


def send_text(text, link, button):
    token = access_token()
    template = {"object_type": "text", "text": text,
                "link": {"web_url": link, "mobile_web_url": link}, "button_title": button}
    response = post("https://kapi.kakao.com/v2/api/talk/memo/default/send",
                    headers={"Authorization": "Bearer " + token},
                    data={"template_object": json.dumps(template, ensure_ascii=False)})
    data = payload(response, "message")
    if type(data.get("result_code")) is not int or data["result_code"] != 0:
        raise KakaoError(kakao_error_message(response, "message"))
