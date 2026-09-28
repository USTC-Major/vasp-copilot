"""Persisted implicit scopes fail before remote observation."""
import pytest
from backend.toolbox.file_actions import FileActions
@pytest.mark.parametrize('source',['task_workspace_choice','task_creation','legacy_task_backfill'])
def test_old_derived_scope_refused(source):
    owner=object.__new__(FileActions)
    scope={'scope_id':'s','kind':'file','version':1,'state':'proposed','derived_from':source}
    flow={'consent':{'computation_scopes':{'s':scope}}}
    with pytest.raises(Exception,match='旧自动派生范围'): owner._scope(flow,'s',1)
def test_grant_methods_removed():
    for name in ('task_file_grant','derive_task_scopes','ensure_task_file_grant'): assert not hasattr(FileActions,name)

def test_persisted_ambiguous_root_refused_then_manual_registration_recovers(file_api):
    from fastapi.testclient import TestClient
    from backend.toolbox.api import create_toolbox_app
    from .conftest import FakeRemoteFiles, FileApi
    from .test_manual_http_flow import _setup_scope, _plan, _url
    root, scope = _setup_scope(file_api)
    service=file_api.app.state.toolbox
    flow=service.store.get_task(file_api.project_id,file_api.task_id)['flow']
    flow['file_grant']={'active':True,'hpc_workspace':'/review/root','recorded_by':'legacy_task_backfill'}
    flow['file_roots'][0].pop('registered_by',None)
    service.store.update_task(file_api.project_id,file_api.task_id,flow=flow)
    service.close()
    rebuilt=create_toolbox_app(root=service.root,settings_loader=service.settings_loader,
        monitor_enabled=False,file_factory=lambda:FakeRemoteFiles(file_api.state))
    with TestClient(rebuilt) as client:
        resumed=FileApi(client,rebuilt,file_api.state,file_api.project_id,file_api.task_id)
        blocked=_plan(resumed,root,scope)
        assert blocked.status_code==409
        assert blocked.json()['error']['code']=='SCOPE_STALE'
        assert resumed.state.begin_count==0 and resumed.state.writes==[]
        registered=client.put(_url(resumed,'/file-roots'),json={'expected_version':1,
            'roots':[{'root_id':root['root_id'],'path':'/review/root'}]})
        assert registered.status_code==200,registered.text
        restored=registered.json()['data']['roots'][0]
        assert restored['registered_by']=='human'
        # The existing explicit human scope retains its exact identity and is usable again.
        planned=_plan(resumed,restored,scope,key='after-manual-registration')
        assert planned.status_code==200,planned.text
        card=planned.json()['pending']
        approved=client.post(_url(resumed,f"/consents/{card['card_id']}"),json={'approved':True,
            'scope_confirmation':{'scope_id':scope['scope_id'],'version':scope['version']}})
        assert approved.status_code==202,approved.text
        assert rebuilt.state.toolbox.files.wait_idle(3)
        assert resumed.state.begin_count==1 and len(resumed.state.writes)==1
