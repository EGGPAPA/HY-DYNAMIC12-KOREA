"""Only the comparison-chart selection; never portfolio or analysis state."""
import base64
import json

import requests

REPO_API = 'https://api.github.com/repos/EGGPAPA/HY-DYNAMIC12-KOREA'
BRANCH = 'monitor-state'
PATH = 'data/sector_comparison_preferences.json'
API = REPO_API + '/contents/' + PATH


def _validate(value):
    if (not isinstance(value, dict) or value.get('schema_version') != 1
            or not isinstance(value.get('selected'), list)
            or any(not isinstance(item, str) for item in value['selected'])):
        raise RuntimeError('비교 업종 저장자료를 확인하지 못했습니다. 기존 설정은 변경하지 않습니다.')
    return value


def read_selection(headers):
    response = requests.get(API, headers=headers, params={'ref': BRANCH}, timeout=10)
    if response.status_code == 404:
        # Do not confuse missing access/a missing branch with first-time use.
        check = requests.get(REPO_API + '/branches/' + BRANCH, headers=headers, timeout=10)
        if check.status_code != 200:
            raise RuntimeError('비교 업종 저장 위치에 접근할 수 없습니다.')
        return None, None
    if response.status_code != 200:
        raise RuntimeError('마지막 비교 업종을 불러오지 못했습니다.')
    try:
        doc = response.json()
        value = json.loads(base64.b64decode(doc['content'], validate=False).decode('utf-8'))
        sha = doc['sha']
        if not isinstance(sha, str) or not sha:
            raise ValueError('missing version')
        return _validate(value), sha
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise RuntimeError('비교 업종 저장자료를 확인하지 못했습니다. 기존 설정은 변경하지 않습니다.') from None


def save_selection(selected, headers):
    """Fresh read plus file-level compare-and-swap for an explicit UI change."""
    _validate({'schema_version': 1, 'selected': selected})
    if not headers.get('Authorization'):
        raise RuntimeError('비교 업종 저장 권한을 확인해 주세요.')
    current, sha = read_selection(headers)
    if current is not None and current['selected'] == selected:
        return
    value = dict(current or {'schema_version': 1})
    value['selected'] = list(selected)
    payload = {'message': 'Remember last comparison sectors', 'branch': BRANCH,
               'content': base64.b64encode(json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')).decode('ascii')}
    if sha:
        payload['sha'] = sha
    response = requests.put(API, headers=headers, json=payload, timeout=15)
    if response.status_code not in (200, 201):
        raise RuntimeError('비교 업종 저장을 확인하지 못했습니다. 선택값은 이 화면에 유지됩니다. 저장 재시도를 눌러 주세요.')
