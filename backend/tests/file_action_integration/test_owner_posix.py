"""Owner and real fixed helper connected solely through local subprocess pipes."""
import datetime as dt
import sys

import pytest
from fastapi.testclient import TestClient

from backend.toolbox.api import create_toolbox_app
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.ssh.connection import SSHManager
from backend.toolbox.ssh.remote_files import RemoteFiles
from backend.tests.remote_file_foundation.test_adapter_posix import Client

pytestmark=pytest.mark.skipif(not sys.platform.startswith('linux'),reason='real Linux helper filesystem required')


def test_owner_committed_text_reopens_with_persistent_provenance(tmp_path):
    remote_root=tmp_path/'remote';remote_root.mkdir()
    def factory():
        client=Client();manager=SSHManager(client_factory=lambda:client)
        manager.switch(host='fixture.invalid',username='fixture')
        return RemoteFiles(manager,scheduler_target={'scheduler':'fixture'})
    app=create_toolbox_app(root=tmp_path/'owner',settings_loader=lambda:ExecutionConfig(data_dir=tmp_path/'owner'),monitor_enabled=False,file_factory=factory)
    with TestClient(app) as client:
        svc=app.state.toolbox;p=svc.store.create_project('p')['id'];t=svc.store.create_task(p,'t')['id']
        svc.store.update_task(p,t,flow={'plan':{'jobs':[{'key':'j','attempt_id':'a','status':'draft'}]}})
        base=f'/api/v1/toolbox/projects/{p}/tasks/{t}'
        response=client.put(base+'/file-roots',json={'expected_version':0,'roots':[{'path':str(remote_root)}]})
        assert response.status_code==200,response.text
        root=response.json()['data']['roots'][0]
        response=client.post(base+'/computation-scopes',json={'job_key':'j','attempt_id':'a','root_bindings':[{'root_id':root['root_id'],'version':1,'destination_prefixes':['']}],
            'allowed_operations':['write_text'],'source_paths':[],'max_operations':1,'max_total_bytes':100,'expires_at':(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=10)).isoformat(),'approval_mode':'human'})
        assert response.status_code==201,response.text
        scope=response.json()['data']
        response=client.post(base+'/tools',json={'name':'remote_file_plan','args':{'scope_id':scope['scope_id'],'scope_version':1,'job_key':'j','attempt_id':'a','idempotency_key':'one',
            'items':[{'item_id':'text','op':'write_text','text':'actual Linux text\n','destination':{'root_id':root['root_id'],'relative_path':'result.txt'}}]}})
        assert response.status_code==200,response.text
        action=response.json()['data']
        response=client.post(base+'/consents/'+action['action_id'],json={'approved':True,'scope_confirmation':{'scope_id':scope['scope_id'],'version':1}})
        assert response.status_code==202,response.text
        assert svc.files.wait_idle(10)
        saved=client.get(base+'/consents/'+action['action_id']).json()['card']
        assert saved['state']=='executed',saved
        response=client.post(base+'/tools',json={'name':'remote_inspect','args':{'path':str(remote_root/'result.txt'),'view':'text'}})
        assert response.status_code==200,response.text
        assert response.json()['data']['text']=='actual Linux text\n'
    # A fresh owner reconstructs classification from the same execution store.
    with TestClient(app) as client:
        response=client.post(base+'/tools',json={'name':'remote_inspect','args':{'path':str(remote_root/'result.txt'),'view':'text'}})
        assert response.status_code==200,response.text
        assert response.json()['data']['text']=='actual Linux text\n'
