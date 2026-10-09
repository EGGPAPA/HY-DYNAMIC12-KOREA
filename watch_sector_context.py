"""Read-only watchlist context. Business labels are NOT official KRX industries.

Reviewed business descriptions, not keyword/theme guesses. A basket is only a
comparison proxy; mixed businesses without a defensible match stay unmapped.
Never influences cohort membership, entry conditions, or their ordering.
"""
from datetime import date
from math import isfinite

REVIEWED_ON = '2026-10-09'
# primary business, related businesses/themes, optional graph basket, source
BUSINESSES = {
    '009830': ('화학·태양광', '석유화학 / 태양광', None, 'https://www.hanwhasolutions.com/m/ko/company/about/'),
    '098460': ('전자검사장비', 'SMT / 반도체 패키징 / 의료로봇', None, 'https://kohyoung.com/kr/'),
    '158430': ('핀테크·보안', '인증 / 보안 솔루션', None, 'https://www.atoncorp.com/'),
    '403870': ('반도체 장비', '고압 수소 어닐링', '반도체', 'https://thehpsp.com/index'),
    '077360': ('반도체 패키징 소재', '솔더볼 / 솔더페이스트', '반도체', 'https://dshm.co.kr/IR_Reference/ESG_Report/HM_ESG_Report%282023%29.pdf'),
    '272210': ('방산전자·ICT', '방산 / 정보통신', '방산·조선', 'https://www.hanwha.co.kr/business/manufacture/systems.do'),
    '011790': ('첨단소재·동박', '2차전지 / 반도체 소재', None, 'https://www.skc.co.kr/m/kor/corporation/intro/company.do'),
    '059090': ('세라믹 소재·부품', '반도체 / 이차전지 / EV', '반도체', 'https://mico.kr/kr/business/overview.php'),
    '067630': ('바이오·의료용품', '의약 / 의료기기 / 에너지', '헬스케어', 'https://kind.krx.co.kr/external/dst/irReference/18282/HLB%EC%83%9D%EB%AA%85%EA%B3%BC%ED%95%99_IR%EC%9E%90%EB%A3%8C_260401.pdf'),
    '011930': ('클린룸·태양광', '반도체 클린룸 / 2차전지 드라이룸 / 태양광', None, 'https://www.shinsungeng.com/m11.php'),
    '071090': ('철강·강관', '용접 강관', None, 'https://www.kosa.or.kr/member/member_list01.jsp?index=63'),
    '039030': ('반도체 레이저장비', '레이저 / 디스플레이', '반도체', 'https://kind.krx.co.kr/corpdetail/totalinfo.do?isurCd=03903&kisComCd=020140&method=searchTotalInfo&repIsuCd=KR7039030002'),
    '001120': ('종합상사·자원', '자원 / 트레이딩 / 물류', None, 'https://www.lxinternational.com/kr/about'),
    '089030': ('반도체 검사장비', '테스트 핸들러 / HBM 검사', '반도체', 'https://kind.krx.co.kr/external/2023/11/14/001156/20231114002413/11013.htm'),
    '089010': ('전자·반도체·화학소재', '디스플레이 / 유리기판 / 전장', None, 'https://chemtronics.co.kr/kr/index.php'),
    '128820': ('에너지·산업기계', '석유가스 / 기계 / 발전', None, 'https://www.daesung.co.kr/Company'),
    '450080': ('2차전지 전구체', '양극재용 전구체 / 니켈·코발트 정제', '2차전지', 'https://www.ecopromaterials.com/sub0102'),
    '112290': ('반도체 공정소재', '반도체 / 디스플레이 화학재료', '반도체', 'https://www.ycchem.co.kr/eng/doc/intro1.php'),
    '084370': ('반도체 증착장비', 'LPCVD / ALD', '반도체', 'https://www.samsungpop.com/common.do?cmd=down&contentType=application%2Fpdf&fileName=2010%2F2025102015594494K_02_02.pdf&inlineYn=Y&saveKey=research.pdf'),
    '102710': ('전자재료·공정소재', '반도체 / 디스플레이', '반도체', 'https://www.enftech.com/en/'),
}


def business_context(code):
    primary, themes, group, source = BUSINESSES.get(str(code).zfill(6),
        ('분류 확인 중', '확인 전에는 테마로 추정하지 않습니다', None, None))
    return dict(primary=primary, themes=themes, group=group, source=source, reviewed_on=REVIEWED_ON)


def sector_flow(code, snapshot, reference_day):
    """Use the same completed-day full-universe rotation as the graph; no I/O."""
    group = business_context(code)['group']
    if group is None:
        return '— 비교업종 미지정'
    try:
        rotation = snapshot['rotation']
        age = (date.fromisoformat(str(reference_day)[:10]) - date.fromisoformat(snapshot['asof'])).days
        if (not rotation.get('valid') or not 0 <= age <= 14
                or rotation['asof'] != snapshot['asof']):
            return '— 평가 없음'
        latest = rotation['daily'][-1]
        strengths = latest['strengths']
        value = strengths[group]
        if latest['date'] != snapshot['asof'] or not isfinite(value):
            return '— 평가 없음'
        if group in rotation['leaders']:
            if len(rotation['leaders']) > 1:
                label = '공동 1위'
            elif value <= 0:
                label = '상대 1위·시장 하회'
            elif rotation['streak'] >= rotation.get('confirmation_days', 3):
                label = '주도'
            else:
                label = '주도 후보'
        elif group == rotation.get('challenger') and rotation.get('closing_days', 0) >= 3:
            label = '추격'
        elif value < 0:
            label = '시장 하회'
        elif value == 0:
            label = '중립'
        else:
            label = '강세·관찰'
        return f'{group} · {label}'
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return '— 평가 없음'


def dated_snapshot(snapshot, today):
    try:
        age = (date.fromisoformat(today) - date.fromisoformat(snapshot['asof'])).days
        return 0 <= age <= 14
    except (KeyError, TypeError, ValueError):
        return False


def leader_comparison(saved_rows, cohort, snapshot, today):
    """Preview saved but inactive leaders, excluding explicit retirements."""
    from rise_leaders import leader_candidates, leader_snapshot_ready
    from rise_watch_cohort import validate_cohort
    try:
        validate_cohort(cohort)
    except (ValueError, TypeError, KeyError):
        return {'ready': False, 'reason': '관찰 설정 확인 필요', 'rows': []}
    if not leader_snapshot_ready(snapshot) or not dated_snapshot(snapshot, today):
        return {'ready': False, 'reason': '완료된 주도력·시장지수 자료 확인 중', 'rows': []}
    if snapshot['asof'] < cohort.get('last_review_asof', ''):
        return {'ready': False, 'reason': '관찰 선정일보다 이전 자료', 'rows': []}
    active = {x['ticker'] for x in cohort['active']}
    ranked = leader_candidates(snapshot, saved_rows)
    rows = [dict(row, comparison_rank=rank) for rank, row in enumerate(ranked, 1)
            if row['ticker'] not in active and row['ticker'] not in cohort.get('archived', {})]
    return {'ready': True, 'asof': snapshot['asof'], 'rows': rows, 'ranked': ranked}
