"""Retired expansions reject tools and old cards without side effects."""
import copy
import pytest
from .conftest import call_tool, create_project_task
from backend.toolbox.consent import card_payload, save_card

@pytest.mark.parametrize('name', ['generate_potcar', 'deploy_submit_script', 'request_file_prepare', 'resume_flow', 'run_exec', 'hpc_exec', 'hpc_write_script'])
def test_retired_tools_cannot_run_with_legacy_arguments(api, name):
    p, t = create_project_task(api)
    before = dict(api.hpc.files)
    response = api.client.post(f"/api/v1/toolbox/projects/{p['id']}/tasks/{t['id']}/tools",
        json={'name': name, 'args': {'command': 'vaspkit -task 103', 'job_key': 'relax'}})
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'AI_TOOL_NOT_ALLOWED'
    assert api.hpc.files == before
    assert api.hpc.run_calls == api.hpc.write_calls == []

@pytest.mark.parametrize('operation', ['potcar_generate', 'script_deploy', 'file_prepare_grant'])
@pytest.mark.parametrize('state', ['pending', 'approved'])
def test_old_extension_card_cannot_execute(api, operation, state):
    p, t = create_project_task(api); svc=api.app.state.toolbox
    flow=svc.store.get_task(p['id'],t['id']).get('flow') or {}
    binding={'operation': operation, 'execution_mode': 'Fake', 'project_id': p['id'], 'task_id': t['id']}
    card=save_card(svc.store,p['id'],t['id'],flow,card_payload(tool=operation,args={},kind=operation,risk='high',reason='legacy',summary='legacy',batch_key='legacy',binding=binding))
    card=card['card_id']
    if state=='approved':
        flow=svc.store.get_task(p['id'],t['id'])['flow']
        flow['consent']['actions'][card]['state']='approved'
        svc.store.update_task(p['id'],t['id'],flow=flow)
    response=api.client.post(f"/api/v1/toolbox/projects/{p['id']}/tasks/{t['id']}/consents/{card}",json={'approved':True})
    assert response.status_code==200
    assert response.json()['card']['state']=='failed'
    assert api.hpc.run_calls==api.hpc.write_calls==[]

def test_stop_removed_resume_preserve_live_job_identity(api):
    p,t=create_project_task(api); svc=api.app.state.toolbox
    job={'key':'relax','status':'running','attempt_id':'old-attempt','slurm_id':1234,
         'submission_state':'submitted','submission_action_id':'old-card','requires':[]}
    svc.store.update_task(p['id'],t['id'],flow={'phase':'monitoring','local_dir':str(api.workspace),
        'hpc_dir':'/review/calc','execution_mode':'Fake','plan':{'jobs':[job]}})
    call_tool(api,p['id'],t['id'],'stop_monitor',{})
    response=api.client.post(f"/api/v1/toolbox/projects/{p['id']}/tasks/{t['id']}/tools",json={'name':'resume_flow','args':{}})
    assert response.status_code==400
    after=svc.store.get_task(p['id'],t['id'])['flow']['plan']['jobs'][0]
    for key in ('attempt_id','slurm_id','submission_state','submission_action_id'): assert after[key]==job[key]
    assert after['stop_note']=='用户终止'
    assert svc.store.monitoring_tasks()==[]
    assert not any(command.startswith('sbatch') for command,_ in api.hpc.run_calls)
    # The generic selection route cannot reset the stopped live submission.
    call_tool(api,p['id'],t['id'],'select_jobs',{'submit':['relax']})
    refused=call_tool(api,p['id'],t['id'],'precheck',{'job_key':'relax','attempt_id':'old-attempt'})
    assert refused['ok'] is False
    assert refused['error']['code']=='JOB_NOT_READY'

@pytest.mark.parametrize('source', ['matching', 'changed', 'missing_subdirectory', 'old_root_only'])
def test_precheck_never_stages_dependency_files(api, source):
    p,t=create_project_task(api); svc=api.app.state.toolbox
    call_tool(api,p['id'],t['id'],'plan',{'strategy':'chain','jobs':[{'key':'relax'},{'key':'relax/static','requires':['relax']}]})
    flow=svc.store.get_task(p['id'],t['id'])['flow']; jobs=flow['plan']['jobs']; jobs[0]['status']='completed'
    flow['staged_inputs']={'relax/static':{'items':[{'name':'POSCAR','verified_only':True}]}}
    flow['file_prepare_grant']={'active':True}; svc.store.update_task(p['id'],t['id'],flow=flow)
    target='/review/calc/relax/static/POSCAR'; api.hpc.files[target]=b'user-owned'
    if source in {'matching','changed'}: api.hpc.files['/review/calc/relax/CONTCAR']=b'user-owned' if source=='matching' else b'changed'
    if source=='old_root_only': api.hpc.files['/review/calc/CONTCAR']=b'old root'
    before=copy.deepcopy(api.hpc.files)
    call_tool(api,p['id'],t['id'],'precheck',{'job_key':jobs[1]['key'],'attempt_id':jobs[1]['attempt_id']})
    assert api.hpc.files==before
    assert api.hpc.write_calls==[]
    assert not any(command.startswith('cp ') for command,_ in api.hpc.run_calls)

def test_old_vaspkit_skill_cannot_advertise_potcar_capability():
    from backend.toolbox.tools.vaspkit import VaspkitSkill
    skill=VaspkitSkill.from_dict({'found':True,'tasks':{'potcar':['103','401'],'structure':['103','111']},
        'notes':'generate POTCAR task 103'})
    assert 'potcar' not in skill.tasks
    assert '103' not in skill.tasks['structure']
    assert 'generate POTCAR' not in skill.notes

def test_legacy_resume_damage_cannot_authorize_same_attempt_again(api):
    p,t=create_project_task(api); svc=api.app.state.toolbox
    job={'key':'relax','status':'draft','attempt_id':'old-attempt','slurm_id':None,
         'submission_state':None,'stop_note':'用户终止','requires':[]}
    original={'kind':'submit','state':'executed','binding':{'job_key':'relax','attempt_id':'old-attempt'}}
    svc.store.update_task(p['id'],t['id'],flow={'phase':'monitoring','local_dir':str(api.workspace),
        'hpc_dir':'/review/calc','execution_mode':'Fake','plan':{'jobs':[job]},
        'consent':{'actions':{'historical':original},'computation_scopes':{}}})
    before=svc.store.get_task(p['id'],t['id'])['flow']
    out=call_tool(api,p['id'],t['id'],'precheck',{'job_key':'relax','attempt_id':'old-attempt'})
    assert out['ok'] is False and out['error']['code']=='SUBMISSION_UNKNOWN'
    assert svc.store.get_task(p['id'],t['id'])['flow']==before
    assert api.hpc.run_calls==api.hpc.write_calls==[]

def test_new_attempt_after_explicit_retry_is_not_old_submission():
    from backend.toolbox.computation import select_job
    job={'key':'relax','status':'draft','attempt_id':'new-attempt','attempt_history':[{'job':{'attempt_id':'old-attempt','slurm_id':1234}}]}
    flow={'plan':{'jobs':[job]},'consent':{'actions':{'original':{'kind':'submit','state':'executed',
        'binding':{'job_key':'relax','attempt_id':'old-attempt'}}}}}
    assert select_job(flow,{'job_key':'relax','attempt_id':'new-attempt'}) is job
