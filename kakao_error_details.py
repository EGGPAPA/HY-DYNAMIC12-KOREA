"""Safe Kakao diagnostics: never display raw bodies, URLs, headers, or secrets."""
import re


_HINTS = {
    "KOE101": "REST API 키가 올바르지 않습니다. 같은 카카오 앱의 KAKAO_REST_API_KEY인지 확인하세요.",
    "KOE010": "Client Secret이 누락되었거나 일치하지 않습니다. 같은 카카오 앱의 KAKAO_CLIENT_SECRET을 확인하세요.",
    "KOE319": "리프레시 토큰이 전달되지 않았습니다. KAKAO_REFRESH_TOKEN 설정을 확인하세요.",
    "KOE322": "리프레시 토큰이 만료되었거나 유효하지 않습니다. 카카오 로그인으로 새 토큰을 발급받아 KAKAO_REFRESH_TOKEN을 교체하세요.",
    "KOE320": "인가 코드가 만료되었거나 이미 사용되었습니다. 카카오 로그인으로 새 인가 코드를 발급받으세요.",
    "KOE237": "카카오 토큰 발급 요청 한도를 초과했습니다. 반복 실행을 멈추고 잠시 후 다시 시도하세요.",
    "-2": "카카오 요청 인자나 형식이 올바르지 않습니다. 메시지 형식과 요청 설정을 확인해야 합니다.",
    "-401": "액세스 토큰 또는 앱 인증 정보가 유효하지 않습니다. 카카오 연결 정보를 확인하세요.",
    "-402": "메시지 전송에 필요한 사용자 동의가 없습니다. 카카오 로그인에서 필요한 동의를 다시 확인하세요.",
}
_ERROR_NAMES = {
    "invalid_client", "invalid_grant", "invalid_request", "misconfigured",
    "unauthorized_client", "unsupported_grant_type", "invalid_scope",
    "access_denied", "server_error", "temporarily_unavailable",
}
_LABELS = {"token": "카카오 토큰 갱신 실패", "message": "카카오 메시지 전송 실패"}


def kakao_error_message(response, operation="token"):
    """Only bounded public error codes and predefined Korean advice leave here.

    Kakao's error_description/msg may contain client_id, tokens, or request
    parameters. They must not be included even when the code is unknown.
    """
    label = _LABELS.get(operation, "카카오 요청 실패")
    status = getattr(response, "status_code", None)
    status_text = str(status) if type(status) is int and 100 <= status <= 599 else "확인 불가"
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    code = None
    for candidate in (payload.get("error_code"), payload.get("code")):
        if isinstance(candidate, str) and re.fullmatch(r"KOE[0-9]{3}", candidate):
            code = candidate
            break
        # Only known REST codes: an arbitrary integer can be a user identifier.
        if type(candidate) in (int, str) and str(candidate) in _HINTS:
            code = str(candidate)
            break
    error = payload.get("error")
    error = error if isinstance(error, str) and error in _ERROR_NAMES else None
    detail = " / ".join(value for value in (code, error) if value)
    headline = f"{label}: HTTP {status_text}" + (f" · {detail}" if detail else " · 상세코드 없음")
    hint = _HINTS.get(code, "상세 원인을 확정할 수 없습니다. 표시된 오류코드로 카카오 연결 설정을 확인해야 합니다.")
    return f"{headline} — {hint} (비밀키·토큰 값은 공유하지 마세요.)"
