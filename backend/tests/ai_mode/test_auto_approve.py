"""Old grants cannot bypass individual KPOINTS approval."""
from types import SimpleNamespace
from backend.tests.toolbox.legacy_bridge import ProjectStore, ToolExecutor
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.consent import PendingConsentError
import pytest
@pytest.mark.parametrize('legacy', ['file_prepare_grant','file_grant','auto_approve_kinds'])
def test_old_authority_does_not_auto_write(tmp_path, legacy):
    store=ProjectStore(tmp_path/'owner'); p=store.create_project('legacy')['id']
    workspace=tmp_path/'workspace'; workspace.mkdir(); t=store.create_task(p,local_workspace=str(workspace))['id']
    cfg=ExecutionConfig(data_dir=tmp_path)
    if legacy=='auto_approve_kinds': cfg=SimpleNamespace(**cfg.model_dump(),auto_approve_kinds=['generate_kpoints'])
    ex=ToolExecutor(store=store,project_id=p,task_id=t,cfg=cfg)
    store.update_task(p,t,flow={'plan':{'jobs':[{'key':'relax','attempt_id':'a','status':'draft'}]},legacy:{'active':True}})
    with pytest.raises(PendingConsentError): ex.tool_generate_kpoints({'job_key':'relax','grid':[4,4,4],'centering':'Gamma'})
    assert not list(tmp_path.rglob('KPOINTS'))
