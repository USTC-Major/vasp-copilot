"""Replanning must not erase a historical or uncertain scheduler identity."""
import copy

import pytest

from .conftest import call_tool, create_project_task
from backend.tests.ai_mode.test_rw3_backend_fixes import _historical_action


@pytest.mark.parametrize('new_key', ['static', 'another-job'])
@pytest.mark.parametrize('state', ['executed', 'executing', 'unknown'])
def test_replan_cannot_wash_missing_submission_identity(api, new_key, state):
    p, t = create_project_task(api)
    flow, _ = _historical_action(state=state, dispatch='dispatched', limit=0)
    flow.update(phase='running', local_dir=str(api.workspace),
                hpc_dir='/review/calc', execution_mode='Fake')
    api.app.state.toolbox.store.update_task(p['id'], t['id'], flow=flow)
    before = copy.deepcopy(api.app.state.toolbox.store.get_task(p['id'], t['id'])['flow'])
    result = call_tool(api, p['id'], t['id'], 'plan', {'jobs': [{'key': new_key}]})
    assert result['ok'] is False
    assert result['error']['code'] == 'SUBMISSION_UNKNOWN'
    assert api.app.state.toolbox.store.get_task(p['id'], t['id'])['flow'] == before
    assert api.hpc.run_calls == api.hpc.write_calls == []


def test_proven_capacity_hold_does_not_block_explicit_replan(api):
    p, t = create_project_task(api)
    flow, _ = _historical_action()
    flow.update(phase='await_submit', local_dir=str(api.workspace),
                hpc_dir='/review/calc', execution_mode='Fake')
    api.app.state.toolbox.store.update_task(p['id'], t['id'], flow=flow)
    result = call_tool(api, p['id'], t['id'], 'plan', {'jobs': [{'key': 'static'}]})
    assert result['ok'] is True
    latest = api.app.state.toolbox.store.get_task(p['id'], t['id'])['flow']
    assert latest['plan']['jobs'][0]['attempt_id'] != 'attempt-1'
    assert latest['consent']['actions']['action-1']['dispatch_state'] == 'not_dispatched'
    assert not any(command.startswith('sbatch') for command, _ in api.hpc.run_calls)
