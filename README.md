# HY DYNAMIC12 KOREA V3.0

## 핵심
- KOSPI/KOSDAQ 상대평가
- 강세장 78점+상위10%, 중립장 78점+상위5%, 약세장 82점+상위3%
- 외국인/기관 수급 필터
- KOSPI vs 수출 YoY / 반도체 수출 YoY
- 과열 추격매수 방지
- 적극매수만 watchlist 저장

## 데이터
1. `korea_universe.csv`: 분석 종목 목록
2. `investor_flow.csv`: KRX 투자자별 종목 수급을 입력/자동수집 연결
3. `export_history.csv`: 관세청 월별 수출 YoY 및 반도체 수출 YoY

현재 버전은 데이터 출처가 불명확한 값을 자동으로 꾸며내지 않습니다.
KRX 수급 자동수집과 Kakao GitHub Actions는 다음 연결 단계에서 붙일 수 있도록 분리했습니다.
