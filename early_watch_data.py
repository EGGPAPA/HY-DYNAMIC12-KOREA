"""Read-only daily inputs for A; no writes to B membership or alert state."""
import base64
import binascii
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO = 'EGGPAPA/HY-DYNAMIC12-KOREA'


class RetrievalError(Exception):
    pass


class DataFormatError(Exception):
    pass


def _decode(body, api):
    try:
        value = json.loads(body.decode('utf-8-sig'))
        if api:
            if not isinstance(value, dict) or value.get('encoding') != 'base64':
                raise DataFormatError('API content unavailable')
            content = value.get('content')
            if not isinstance(content, str) or not content.strip():
                raise DataFormatError('API content empty')
            value = json.loads(base64.b64decode(''.join(content.split()), validate=True)
                               .decode('utf-8-sig'))
        return value
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise DataFormatError('Invalid JSON content') from exc


def load_json(path, branch, expected_type, token=''):
    """Bounded fallback between public raw JSON and GitHub Contents envelopes.

    Prefer authenticated API when configured. Raw fallback also handles Contents
    responses with omitted content (large files) and shared API rate limits.
    Credentials are sent exclusively to the fixed GitHub API endpoint.
    """
    raw = f'https://raw.githubusercontent.com/{REPO}/{branch}/{path}'
    api = f'https://api.github.com/repos/{REPO}/contents/{path}?{urlencode({"ref": branch})}'
    sources = [(api, True), (raw, False)] if token else [(raw, False), (api, True)]
    format_error = None
    for url, is_api in sources:
        headers = {'Accept': 'application/vnd.github+json' if is_api else 'application/json',
                   'User-Agent': 'HY-DYNAMIC12-KOREA', 'Cache-Control': 'no-cache'}
        if is_api and token:
            headers['Authorization'] = f'Bearer {token}'
        try:
            with urlopen(Request(url, headers=headers), timeout=8) as response:
                value = _decode(response.read(), is_api)
            if not isinstance(value, expected_type):
                raise DataFormatError('Unexpected document type')
            return value
        except DataFormatError as exc:
            format_error = exc
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
    if format_error:
        raise format_error
    raise RetrievalError('Daily input unavailable')


def load_early_watch(token=''):
    from dual_leader_watch import early_candidates
    from rise_leaders import leader_snapshot_ready
    saved = load_json('rise_timing_watchlist.json', 'main', list, token)
    snapshot = load_json('data/ma_convergence_daily.json', 'monitor-state', dict, token)
    # Reject malformed records explicitly; never mislabel them as a network error.
    if (any(not isinstance(row, dict) for row in saved)
            or not isinstance(snapshot.get('items', {}), dict)
            or any(not isinstance(row, dict) for row in snapshot.get('items', {}).values())
            or not isinstance(snapshot.get('benchmarks', {}), dict)
            or any(not isinstance(row, dict) for row in snapshot.get('benchmarks', {}).values())):
        raise DataFormatError('Invalid daily records')
    if not leader_snapshot_ready(snapshot):
        return [], '', '확정 일봉 검증 대기'
    return early_candidates(snapshot, saved), snapshot['asof'], ''
