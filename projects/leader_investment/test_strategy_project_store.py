import base64
import json
import unittest
from unittest.mock import Mock, patch

from projects.leader_investment.strategy_projects import empty_state
from projects.leader_investment.strategy_project_store import read_projects, save_projects


def response(status=200, sha='abc'):
    return Mock(status_code=status, json=lambda: {'sha':sha,
        'content':base64.b64encode(json.dumps(empty_state()).encode()).decode()})


class StorageTests(unittest.TestCase):
    def test_read_is_read_only(self):
        with patch('projects.leader_investment.strategy_project_store.requests.get', return_value=response()), patch('projects.leader_investment.strategy_project_store.requests.put') as put:
            state, sha = read_projects({})
            self.assertEqual(sha,'abc')
            self.assertEqual(state,empty_state())
            put.assert_not_called()

    def test_missing_document_only_empty_if_branch_accessible(self):
        with patch('projects.leader_investment.strategy_project_store.requests.get', side_effect=[response(404),response(200)]):
            self.assertEqual(read_projects({}),(empty_state(),None))
        with patch('projects.leader_investment.strategy_project_store.requests.get', side_effect=[response(404),response(404)]):
            with self.assertRaises(RuntimeError): read_projects({})

    def test_errors_and_corruption_not_empty_overwrite(self):
        for resp in (response(403), response(500), Mock(status_code=200,json=lambda:{'content':'bad'})):
            with patch('projects.leader_investment.strategy_project_store.requests.get', return_value=resp):
                with self.assertRaises((RuntimeError,ValueError)): read_projects({})

    def test_conflict_never_puts(self):
        with patch('projects.leader_investment.strategy_project_store.requests.get', return_value=response(sha='new')), patch('projects.leader_investment.strategy_project_store.requests.put') as put:
            with self.assertRaises(RuntimeError): save_projects(empty_state(),'old',{'Authorization':'test'})
            put.assert_not_called()

    def test_success_uses_exact_sha_and_state_branch(self):
        with patch('projects.leader_investment.strategy_project_store.requests.get', return_value=response()), patch('projects.leader_investment.strategy_project_store.requests.put', return_value=response()) as put:
            save_projects(empty_state(),'abc',{'Authorization':'test'})
            payload = put.call_args.kwargs['json']
            self.assertEqual(payload['sha'],'abc')
            self.assertEqual(payload['branch'],'monitor-state')

    def test_write_error_and_unauthorized_fail_closed(self):
        with patch('projects.leader_investment.strategy_project_store.requests.get',return_value=response()), patch('projects.leader_investment.strategy_project_store.requests.put',return_value=response(409)):
            with self.assertRaises(RuntimeError): save_projects(empty_state(),'abc',{'Authorization':'test'})
        with self.assertRaises(RuntimeError): save_projects(empty_state(),None,{})


if __name__ == '__main__': unittest.main()
