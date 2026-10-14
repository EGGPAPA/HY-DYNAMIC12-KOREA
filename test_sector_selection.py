import base64
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

import sector_selection_store as store
from sector_selection_ui import MEMORY_KEY, WIDGET_KEY, clean_selection


def response(status=200, value=None, sha='version-1'):
    result = Mock(status_code=status)
    result.json.return_value = {
        'content': base64.b64encode(json.dumps(value, ensure_ascii=False).encode()).decode(),
        'sha': sha,
    }
    return result


class SelectionStoreTests(unittest.TestCase):
    def setUp(self):
        self.headers = {'Authorization': 'Bearer test-only'}
        self.value = {'schema_version': 1, 'selected': ['금융', '반도체']}

    @patch.object(store.requests, 'get')
    def test_read_uses_dedicated_state_branch(self, get):
        get.return_value = response(value=self.value)
        self.assertEqual(store.read_selection(self.headers), (self.value, 'version-1'))
        self.assertEqual(get.call_args.kwargs['params'], {'ref': 'monitor-state'})
        self.assertTrue(get.call_args.args[0].endswith('/data/sector_comparison_preferences.json'))

    @patch.object(store.requests, 'get')
    def test_first_use_requires_existing_accessible_branch(self, get):
        get.side_effect = [response(404), response(200)]
        self.assertEqual(store.read_selection(self.headers), (None, None))
        self.assertTrue(get.call_args.args[0].endswith('/branches/monitor-state'))

    @patch.object(store.requests, 'get')
    def test_access_failure_is_not_empty_preferences(self, get):
        for status in (403, 404, 500):
            get.side_effect = [response(404), response(status)]
            with self.assertRaises(RuntimeError):
                store.read_selection(self.headers)

    @patch.object(store.requests, 'get')
    def test_invalid_schema_rejected(self, get):
        for value in ([], {}, {'schema_version': 2, 'selected': []},
                      {'schema_version': 1, 'selected': '금융'},
                      {'schema_version': 1, 'selected': [None]}):
            get.return_value = response(value=value)
            with self.assertRaises(RuntimeError):
                store.read_selection(self.headers)

    @patch.object(store.requests, 'get')
    def test_bad_encoding_and_missing_version_rejected(self, get):
        bad = response(value=self.value)
        bad.json.return_value = {'content': 'not-base64!', 'sha': 'x'}
        get.return_value = bad
        with self.assertRaises(RuntimeError):
            store.read_selection(self.headers)
        get.return_value = response(value=self.value, sha='')
        with self.assertRaises(RuntimeError):
            store.read_selection(self.headers)

    @patch.object(store.requests, 'put')
    @patch.object(store, 'read_selection')
    def test_save_preserves_unknown_fields_and_uses_cas(self, read, put):
        previous = dict(self.value, other_preference='preserve')
        read.return_value = previous, 'latest-sha'
        put.return_value = response(200)
        store.save_selection(['자동차'], self.headers)
        payload = put.call_args.kwargs['json']
        self.assertEqual(payload['sha'], 'latest-sha')
        self.assertEqual(payload['branch'], 'monitor-state')
        self.assertEqual(put.call_args.args[0], store.API)
        saved = json.loads(base64.b64decode(payload['content']))
        self.assertEqual(saved['selected'], ['자동차'])
        self.assertEqual(saved['other_preference'], 'preserve')
        self.assertEqual(previous['selected'], self.value['selected'])

    @patch.object(store.requests, 'put')
    @patch.object(store, 'read_selection', return_value=(None, None))
    def test_save_first_selection_including_empty(self, read, put):
        put.return_value = response(201)
        store.save_selection([], self.headers)
        payload = put.call_args.kwargs['json']
        self.assertNotIn('sha', payload)
        self.assertEqual(json.loads(base64.b64decode(payload['content']))['selected'], [])

    @patch.object(store.requests, 'put')
    @patch.object(store, 'read_selection')
    def test_no_redundant_save(self, read, put):
        read.return_value = self.value, 'sha'
        store.save_selection(self.value['selected'], self.headers)
        put.assert_not_called()

    @patch.object(store.requests, 'put')
    @patch.object(store, 'read_selection', side_effect=RuntimeError('read failed'))
    def test_no_overwrite_on_read_failure(self, read, put):
        with self.assertRaises(RuntimeError):
            store.save_selection(['금융'], self.headers)
        put.assert_not_called()

    @patch.object(store, 'read_selection')
    def test_missing_auth_does_not_read_or_write(self, read):
        with self.assertRaises(RuntimeError):
            store.save_selection(['금융'], {})
        read.assert_not_called()

    @patch.object(store.requests, 'put')
    @patch.object(store, 'read_selection')
    def test_conflict_does_not_force_overwrite(self, read, put):
        read.return_value = self.value, 'old'
        put.return_value = response(409)
        with self.assertRaises(RuntimeError):
            store.save_selection(['자동차'], self.headers)
        self.assertEqual(put.call_count, 1)

    def test_clean_retains_order_and_rejects_unknown_names(self):
        self.assertEqual(clean_selection(['금융', 'removed', '반도체', '금융', None]),
                         ['금융', '반도체'])


APP = """
import streamlit as st
from sector_selection_ui import render_comparison_selection
view = st.radio('화면', ['비교', '다른 화면'])
@st.fragment
def comparison():
    available = st.session_state.get('test_available', ['2차전지', '화학·소재', '반도체', '금융', '자동차'])
    selected = render_comparison_selection(available)
    st.text('graph:' + ','.join(selected))
if view == '비교':
    comparison()
"""


class SelectionUiTests(unittest.TestCase):
    def setUp(self):
        self.saved = None
        self.read_patch = patch('sector_selection_ui.read_selection', side_effect=self.read)
        self.write_patch = patch('sector_selection_ui.save_selection', side_effect=self.write)
        self.header_patch = patch('sector_selection_ui._headers', return_value={'Authorization': 'test-only'})
        self.read_mock = self.read_patch.start()
        self.write_mock = self.write_patch.start()
        self.header_patch.start()
        self.addCleanup(self.read_patch.stop)
        self.addCleanup(self.write_patch.stop)
        self.addCleanup(self.header_patch.stop)

    def read(self, headers):
        return deepcopy(self.saved), 'sha' if self.saved is not None else None

    def write(self, selected, headers):
        self.saved = {'schema_version': 1, 'selected': list(selected)}

    def app(self):
        app = AppTest.from_string(APP).run()
        self.assertEqual(len(app.exception), 0)
        return app

    def test_first_visit_defaults_not_written_without_change(self):
        app = self.app()
        self.assertEqual(app.multiselect[0].value, ['2차전지', '화학·소재', '반도체'])
        self.write_mock.assert_not_called()

    def test_change_auto_saves_and_fresh_session_restores(self):
        app = self.app()
        app.multiselect[0].set_value(['금융', '자동차']).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(self.saved['selected'], ['금융', '자동차'])
        fresh = self.app()
        self.assertEqual(fresh.multiselect[0].value, ['금융', '자동차'])

    def test_page_navigation_retains_even_after_widget_cleanup(self):
        app = self.app()
        app.multiselect[0].set_value(['금융']).run()
        app.radio[0].set_value('다른 화면').run()
        app.run()
        self.assertEqual(app.session_state[MEMORY_KEY], ['금융'])
        app.radio[0].set_value('비교').run()
        self.assertEqual(app.multiselect[0].value, ['금융'])
        self.assertEqual(self.write_mock.call_count, 1)

    def test_rank_changes_and_regular_reruns_do_not_reset_or_write(self):
        app = self.app()
        app.multiselect[0].set_value(['금융', '반도체']).run()
        old_options = app.multiselect[0].options
        app.session_state['test_available'] = ['자동차', '금융', '반도체', '화학·소재', '2차전지']
        app.run().run()
        self.assertEqual(app.multiselect[0].value, ['금융', '반도체'])
        self.assertEqual(app.multiselect[0].options, old_options)
        self.assertEqual(self.write_mock.call_count, 1)

    def test_temporary_missing_data_does_not_erase_choice(self):
        self.saved = {'schema_version': 1, 'selected': ['금융', '반도체']}
        app = self.app()
        app.session_state['test_available'] = ['반도체', '자동차']
        app.run()
        self.assertEqual(app.multiselect[0].value, ['금융', '반도체'])
        self.assertEqual(app.text[0].value, 'graph:반도체')
        self.assertTrue(any('자료 확인 중인 선택 업종: 금융' in c.value for c in app.caption))
        app.session_state['test_available'] = ['반도체', '금융', '자동차']
        app.run()
        self.assertEqual(app.text[0].value, 'graph:금융,반도체')
        self.write_mock.assert_not_called()

    def test_clear_all_stays_empty_after_reconnect(self):
        app = self.app()
        app.multiselect[0].set_value([]).run()
        self.assertEqual(self.saved['selected'], [])
        self.assertEqual(self.app().multiselect[0].value, [])

    def test_invalid_old_sector_removed_without_adding_defaults(self):
        self.saved = {'schema_version': 1, 'selected': ['old sector']}
        app = self.app()
        self.assertEqual(app.multiselect[0].value, [])
        self.write_mock.assert_not_called()

    def test_initial_read_failure_waits_instead_of_saving_defaults(self):
        self.read_mock.side_effect = requests.Timeout()
        app = self.app()
        self.assertEqual(len(app.multiselect), 0)
        self.assertIn('덮어쓰지', app.warning[0].value)
        self.write_mock.assert_not_called()
        self.saved = {'schema_version': 1, 'selected': ['자동차']}
        self.read_mock.side_effect = self.read
        app.button[0].click().run()
        self.assertEqual(app.multiselect[0].value, ['자동차'])

    def test_failed_save_keeps_local_selection_and_explicit_retry(self):
        app = self.app()
        self.write_mock.side_effect = requests.Timeout()
        app.multiselect[0].set_value(['금융']).run()
        self.assertEqual(app.multiselect[0].value, ['금융'])
        self.assertEqual(len(app.warning), 1)
        self.write_mock.side_effect = self.write
        app.button[0].click().run()
        self.assertEqual(self.saved['selected'], ['금융'])
        self.assertEqual(len(app.warning), 0)


if __name__ == '__main__':
    unittest.main()
