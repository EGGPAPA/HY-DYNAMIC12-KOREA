"""Explicit, optimistic-concurrency storage on the existing state branch."""
import base64
import json

import requests

from projects.leader_investment.strategy_projects import PROJECT_PATH, empty_state, validate_state

API = f'https://api.github.com/repos/EGGPAPA/HY-DYNAMIC12-KOREA/contents/{PROJECT_PATH}'
BRANCH = 'monitor-state'


def read_projects(headers):
    response = requests.get(API, headers=headers, params={'ref': BRANCH}, timeout=15)
    if response.status_code == 404:
        # A branch/access failure must not masquerade as a new empty document.
        check = requests.get('https://api.github.com/repos/EGGPAPA/HY-DYNAMIC12-KOREA/branches/' + BRANCH,
                             headers=headers, timeout=15)
        if check.status_code != 200:
            raise RuntimeError('프로젝트 저장 위치에 접근할 수 없습니다. 기존 기록을 덮어쓰지 않습니다.')
        return empty_state(), None
    if response.status_code != 200:
        raise RuntimeError('프로젝트를 읽지 못했습니다. 잠시 후 다시 조회해 주세요.')
    try:
        doc = response.json()
        value = json.loads(base64.b64decode(doc['content']).decode('utf-8'))
        return validate_state(value), doc['sha']
    except (ValueError, KeyError, TypeError):
        raise RuntimeError('프로젝트 저장자료를 확인할 수 없어 변경을 중단했습니다.') from None


def save_projects(state, expected_sha, headers):
    validate_state(state)
    if not headers.get('Authorization'):
        raise RuntimeError('프로젝트 저장 권한을 확인해 주세요.')
    _, actual_sha = read_projects(headers)
    if actual_sha != expected_sha:
        raise RuntimeError('다른 화면에서 기록이 변경됐습니다. 다시 조회한 뒤 입력해 주세요.')
    payload = {'message': 'Save explicit short/medium project record', 'branch': BRANCH,
               'content': base64.b64encode(json.dumps(state, ensure_ascii=False, indent=2,
                                                    allow_nan=False).encode()).decode()}
    if expected_sha:
        payload['sha'] = expected_sha
    response = requests.put(API, headers=headers, json=payload, timeout=20)
    if response.status_code not in (200, 201):
        raise RuntimeError('프로젝트 저장을 확인하지 못했습니다. 재입력 전에 다시 조회해 기록을 확인하세요.')
