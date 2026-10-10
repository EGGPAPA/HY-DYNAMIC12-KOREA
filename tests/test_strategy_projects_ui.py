from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

FIXTURE = Path(__file__).parent / 'fixtures' / 'strategy_app.py'


def element(at, kind, label):
    return next(x for x in getattr(at, kind) if x.label == label)


class StrategyUITests(unittest.TestCase):
    def app(self):
        at = AppTest.from_file(str(FIXTURE), default_timeout=20).run()
        self.assertEqual(len(at.exception),0)
        return at

    def start(self, at):
        element(at,'button','현재 후보 최대 20개로 관찰 시작').click().run()
        self.assertEqual(len(at.exception),0)

    def test_preview_read_only_and_switch_isolated(self):
        at = self.app()
        self.assertEqual(at.session_state['fixture_revision'],0)
        self.start(at)
        self.assertEqual(len(at.session_state['fixture_state']['projects']['short']['watch']),1)
        at.radio(key='fixture_project').set_value('② 중기 · 1~3개월').run()
        self.assertEqual(len(at.exception),0)
        self.assertEqual(at.session_state['fixture_state']['projects']['medium']['watch'],[])
        self.start(at)
        self.assertEqual(len(at.session_state['fixture_state']['projects']['medium']['watch']),1)

    def test_save_budget_and_trade_and_void(self):
        at = self.app()
        self.start(at)
        element(at,'number_input','이 프로젝트 배정금액(원) · 0은 미설정').set_value(1e7)
        element(at,'button','프로젝트 자금 설정 저장').click().run()
        self.assertEqual(at.session_state['fixture_state']['projects']['short']['budget'],1e7)
        element(at,'number_input','체결 수량(주)').set_value(10)
        element(at,'number_input','체결 단가(원)').set_value(10000.)
        element(at,'checkbox','실제 체결 내역을 확인했으며 이 프로젝트에만 기록합니다').check()
        element(at,'button','체결 기록 저장').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertEqual(len(at.session_state['fixture_state']['projects']['short']['trades']),1)
        self.assertEqual(at.session_state['fixture_state']['projects']['medium']['trades'],[])
        element(at,'text_input','정정 사유').set_value('검증 입력 정정')
        element(at,'checkbox','실제 주문 취소가 아닌 이 기록의 정정임을 확인합니다').check()
        element(at,'button','선택 기록 무효 처리 · 원본 보존').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertTrue(at.session_state['fixture_state']['projects']['short']['trades'][0]['voided'])

    def test_medium_fundamental_plan_form(self):
        at = self.app()
        at.radio(key='fixture_project').set_value('② 중기 · 1~3개월').run()
        self.start(at)
        element(at,'text_area','매수 근거·실적/수주 변화').set_value('가상 공시 확인')
        element(at,'text_input','직접 확인한 공시·기업자료 주소').set_value('https://example.com/report')
        element(at,'checkbox','자료를 직접 확인했고 현재 매수 근거가 유효합니다').check()
        element(at,'number_input','내 손절 기준 가격(원) · 0은 미설정').set_value(9500.)
        element(at,'button','이 프로젝트의 계획 저장').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertTrue(at.session_state['fixture_state']['projects']['medium']['reviews']['000001']['fundamental_ok'])
        self.assertEqual(at.session_state['fixture_state']['projects']['short']['reviews'],{})

    def test_failed_load_does_not_present_empty_start_form(self):
        at = self.app()
        at.session_state['fixture_fail_read'] = True
        at.run()
        self.assertEqual(len(at.exception),0)
        self.assertTrue(any('덮어쓰지 않습니다' in x.value for x in at.warning))
        self.assertFalse(any(x.label == '현재 후보 최대 20개로 관찰 시작' for x in at.button))


if __name__ == '__main__': unittest.main()
