"""No AI/network: exact owner HTTP actions, cancellation and restart receipts."""
import copy
import datetime as dt
import hashlib
import threading
import time
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from backend.toolbox.api import create_toolbox_app
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.ssh import file_helper as h
from backend.toolbox.ssh.remote_files import RemoteFiles, RemoteFileError


def future():return (dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=20)).isoformat()
def meta(inode,size=0,kind='file'):
    return dict(device=1,inode=inode,size=size,type=kind,mtime_ns=1,ctime_ns=1,mode=0o600 if kind=='file' else 0o700)


class MemoryRemote(RemoteFiles):
    def __init__(self,world):self.world=world;self.manager=None;self.hpc=world
    def endpoint(self):return copy.deepcopy(self.world.endpoint)
    def inspect_root(self,path,*,root_id=None,version=1):
        return dict(root_id=root_id,version=version,requested_path=path,canonical_path=path,endpoint_digest=self.endpoint()['endpoint_digest'],
                    identity=meta(10,kind='directory'),resolution_chain=[],ancestors=[{'path':'/',**meta(1,kind='directory')},{'path':path,**meta(10,kind='directory')}])
    def inspect(self,path,view='stat',**kwargs):
        if kwargs.get('expected_endpoint') is not None:assert kwargs['expected_endpoint']==self.endpoint()
        info=self.world.files[path]
        observed={**info['metadata'],'requested_path':path,'canonical_path':path,'endpoint_digest':self.endpoint()['endpoint_digest'],'resolution_chain':[],'link_target':None,
                  'content_class':h.content_class(path),'verification_level':'metadata','sha256':None}
        if view=='text':
            h.check_observation(observed,kwargs.get('expected_evidence'),self.endpoint()['endpoint_digest'])
            label=h.provenance_class(observed,kwargs['provenance'],self.endpoint()['endpoint_digest'])
            h.require(label not in {'potcar','large_vasp','credential'},'CONTENT_READ_DENIED','restricted')
            return {'text':info['text']}
        return observed
    def _read(self,request,**kwargs):
        if request['op']=='destination':
            root=request['root'];path=root['canonical_path']+'/'+request['path']
            return dict(root_id=root['root_id'],root_version=root['version'],relative_path=request['path'],parent_chain=root['ancestors'],missing_components=[],parent_item_id=None,
                        target_exists=self.world.files.get(path,{}).get('metadata'))
        if request['op']=='source':
            observed=self.inspect(request['path'])
            h.check_observation(observed,request.get('expected_evidence'),self.endpoint()['endpoint_digest'])
            observed['content_class']=h.provenance_class(observed,request['provenance'],self.endpoint()['endpoint_digest'])
            return observed
        raise AssertionError(request)
    def begin(self,manifest,dispatch_context,*,dispatch_guard=None):
        with dispatch_guard('begin',None) if dispatch_guard else nullcontext():self.world.begins+=1
        return Session(self,manifest,dispatch_guard)
    def reconcile(self,manifest,*,item_id=None):
        receipt=self.world.receipts.get((manifest['action_id'],item_id))
        return {'items':[{'state':'confirmed','receipt':receipt,'item_id':item_id,**{key:receipt.get(key) for key in ('remote_receipt_id','content_class','sha256')}}] if receipt else [{'state':'unknown','item_id':item_id}]}


class Session:
    def __init__(self,files,manifest,guard):self.files=files;self.manifest=manifest;self.guard=guard;self.index=0;self.closed=False
    def prepare_next(self,*,should_cancel=None):
        world=self.files.world;world.preparing.set()
        while world.block and not world.release.wait(.01):
            if should_cancel and should_cancel():raise RemoteFileError('ACTION_ABORTED','cancelled',published=False)
        item=self.manifest['items'][self.index]
        self.prepared=dict(item_id=item['item_id'],state='prepared',temporary_evidence=meta(world.next_inode(),item['bytes']),source_evidence=item['source'],
                           content_class=item['content_class'],bytes_processed=item['bytes'],prepare_token='fixture',prepared_digest='digest',remote_receipt_id='fixture-prepared')
        return copy.deepcopy(self.prepared)
    def commit(self,permit):
        world=self.files.world;item=self.manifest['items'][self.index]
        with self.guard('commit',item['item_id']) if self.guard else nullcontext():world.commits+=1
        root=next(r for r in self.manifest['roots'] if r['root_id']==item['destination']['root_id']);path=root['canonical_path']+'/'+item['destination']['relative_path']
        target={**self.prepared['temporary_evidence'],'canonical_path':path,'endpoint_digest':self.files.endpoint()['endpoint_digest']}
        receipt={**self.prepared,'state':'committed','target_evidence':target,'action_id':self.manifest['action_id'],
                 'manifest_digest':self.manifest['manifest_digest'],
                 'remote_receipt_id':item['names']['action_directory']+'/'+item['names']['committed_name']}
        world.files[path]={'metadata':target,'text':item.get('text') or 'copied'}
        world.receipts[(self.manifest['action_id'],item['item_id'])]=receipt
        if world.lose_commit:raise RemoteFileError('ACTION_UNKNOWN','lost commit receipt',published='unknown',dispatched=True)
        self.index+=1
        return receipt
    def abort(self):return {'leftovers':[]}
    def close(self):self.closed=True


class World:
    def __init__(self):
        self.endpoint=dict(host='fixture.invalid',port=22,username='fixture',scheduler_target={'scheduler':'fixture'},host_key={'algorithm':'fixture','sha256':'a'*64,'verification':'known_hosts'})
        self.endpoint['endpoint_digest']=h.digest(self.endpoint)
        self.files={};self.receipts={};self.begins=self.commits=0;self.inode=100;self.block=False;self.lose_commit=False
        self.preparing=threading.Event();self.release=threading.Event()
    def next_inode(self):self.inode+=1;return self.inode
    def factory(self):return MemoryRemote(self)


@pytest.fixture
def owner(tmp_path):
    world=World()
    app=create_toolbox_app(root=tmp_path,settings_loader=lambda:ExecutionConfig(data_dir=tmp_path),monitor_enabled=False,file_factory=world.factory)
    with TestClient(app) as client:
        svc=app.state.toolbox
        project=svc.store.create_project('fixture')['id'];task=svc.store.create_task(project,'fixture')['id']
        svc.store.update_task(project,task,flow={'plan':{'jobs':[{'key':'j','attempt_id':'attempt','status':'draft'}]}})
        yield client,svc,world,project,task


def plan(client,project,task,*,name='result.txt',key='key'):
    base=f'/api/v1/toolbox/projects/{project}/tasks/{task}'
    roots=client.put(base+'/file-roots',json={'expected_version':0,'roots':[{'path':'/research'}]})
    assert roots.status_code==200,roots.text
    root=roots.json()['data']['roots'][0]
    scope=client.post(base+'/computation-scopes',json=dict(job_key='j',attempt_id='attempt',root_bindings=[{'root_id':root['root_id'],'version':1,'destination_prefixes':['']}],
                         allowed_operations=['write_text'],source_paths=[],max_operations=2,max_total_bytes=100,expires_at=future(),approval_mode='human'))
    assert scope.status_code==201,scope.text
    scope=scope.json()['data']
    args=dict(scope_id=scope['scope_id'],scope_version=1,job_key='j',attempt_id='attempt',idempotency_key=key,
              items=[dict(item_id='text',op='write_text',text='fixture',destination={'root_id':root['root_id'],'relative_path':name})])
    response=client.post(base+'/tools',json={'name':'remote_file_plan','args':args})
    assert response.status_code==200,response.text
    return base,scope,args,response.json()['data']


def approve(client,base,scope,action):
    response=client.post(base+'/consents/'+action['action_id'],json={'approved':True,'scope_confirmation':{'scope_id':scope['scope_id'],'version':1}})
    assert response.status_code==202,response.text


def test_http_no_ai_once_and_audit_delete(owner):
    client,svc,world,project,task=owner
    base,scope,args,action=plan(client,project,task)
    approve(client,base,scope,action)
    assert svc.files.wait_idle()
    card=client.get(base+'/consents/'+action['action_id']).json()['card']
    assert card['state']=='executed',card
    assert card['receipt']['spent']=={'operations':1,'bytes':7}
    assert 'prepare_token' not in str(card)
    replay=client.post(base+'/tools',json={'name':'remote_file_plan','args':args}).json()['data']
    assert replay['action_id']==action['action_id']
    assert world.begins==world.commits==1
    read=client.post(base+'/tools',json={'name':'remote_inspect','args':{'path':'/research/result.txt','view':'text'}})
    assert read.status_code==200 and read.json()['data']['text']=='fixture',read.text
    assert client.delete(base).status_code==409
    assert client.delete('/api/v1/toolbox/projects/'+project).status_code==409


def test_revoke_while_prepare_returns_and_never_commits(owner):
    client,svc,world,project,task=owner
    world.block=True
    base,scope,args,action=plan(client,project,task)
    approve(client,base,scope,action)
    assert world.preparing.wait(2)
    started=time.monotonic()
    response=client.post(base+'/computation-scopes/'+scope['scope_id']+'/revoke',json={'expected_version':1})
    assert response.status_code==200 and time.monotonic()-started<1
    assert svc.files.wait_idle()
    card=client.get(base+'/consents/'+action['action_id']).json()['card']
    assert card['state']=='failed' and world.commits==0
    assert card['receipt']['released']=={'operations':1,'bytes':7}


def test_unknown_holds_budget_and_target_reconcile_only(owner):
    client,svc,world,project,task=owner
    world.lose_commit=True
    base,scope,args,action=plan(client,project,task)
    approve(client,base,scope,action)
    assert svc.files.wait_idle()
    card=client.get(base+'/consents/'+action['action_id']).json()['card']
    assert card['state']=='unknown' and card['receipt']['held_unknown']=={'operations':1,'bytes':7}
    another=copy.deepcopy(args);another['idempotency_key']='new-key'
    assert client.post(base+'/tools',json={'name':'remote_file_plan','args':another}).status_code==409
    response=client.post(base+'/file-actions/'+action['action_id']+'/reconcile',json={})
    assert response.status_code==200 and response.json()['data']['state']=='executed',response.text
    assert world.commits==world.begins==1


def test_legacy_unidentified_unknown_global_write_block_and_delete(owner):
    client,svc,world,project,task=owner
    def legacy(flow):flow.setdefault('consent',{}).setdefault('actions',{})['legacy']={'kind':'hpc_upload','state':'unknown','binding':{},'action_id':'legacy'}
    svc.store.mutate_file_task(project,task,legacy)
    base=f'/api/v1/toolbox/projects/{project}/tasks/{task}'
    assert client.delete(base).status_code==409
    with pytest.raises(Exception,match='历史上传结果待核查'):svc.files._global_write()


@pytest.mark.parametrize('reservation', ['unknown_upload', 'active_lease'])
@pytest.mark.parametrize('destination', ['parent', 'parent/held', 'parent/held/child'])
def test_legacy_mkdir_respects_upload_and_live_lease_targets(owner, reservation, destination):
    from backend.toolbox.contracts import ToolboxError
    _client,svc,world,project,task=owner
    identity=svc.files.legacy_identity('/research','parent/held')
    if reservation=='unknown_upload':
        def save(flow):
            flow.setdefault('consent',{}).setdefault('actions',{})['legacy']={
                'kind':'hpc_upload','state':'unknown','action_id':'legacy','binding':{'file_identity':identity}}
        svc.store.mutate_file_task(project,task,save)
    else:
        with svc.files.guard:svc.files.leases['upload']=identity
    entered=[]
    try:
        with pytest.raises(ToolboxError) as raised:
            with svc.files.legacy_mkdir('/research',destination,hpc=world,cfg=svc.settings_loader()):entered.append(destination)
        assert raised.value.code=='DESTINATION_CONFLICT'
        assert not entered
        # Independent paths remain usable; a blocked request must not leak a lease.
        with svc.files.legacy_mkdir('/research','sibling',hpc=world,cfg=svc.settings_loader()):entered.append('sibling')
        assert entered==['sibling']
        assert set(svc.files.leases)==({'upload'} if reservation=='active_lease' else set())
    finally:
        with svc.files.guard:svc.files.leases.pop('upload',None)


def test_guarded_legacy_mkdir_uses_actual_connection_but_unmanaged_keeps_compatibility(owner):
    from backend.toolbox.contracts import ToolboxError
    _client,svc,_world,_project,_task=owner
    wrong_connection=object()
    with svc.files.legacy_mkdir('/research','ordinary',hpc=wrong_connection,cfg=svc.settings_loader()):
        assert any(lease.get('global') for lease in svc.files.leases.values())
    identity=svc.files.legacy_identity('/research','held')
    with svc.files.guard:svc.files.leases['upload']=identity
    try:
        with pytest.raises(ToolboxError) as raised:
            with svc.files.legacy_mkdir('/research','sibling',hpc=wrong_connection,cfg=svc.settings_loader()):
                pytest.fail('guarded mkdir accepted a different write connection')
        assert raised.value.code=='ENDPOINT_CHANGED'
    finally:
        with svc.files.guard:svc.files.leases.pop('upload',None)


def test_completed_approval_replay_survives_unrelated_legacy_unknown(owner):
    client,svc,world,project,task=owner
    base,scope,_args,action=plan(client,project,task)
    approve(client,base,scope,action)
    assert svc.files.wait_idle()
    other=svc.store.create_task(project,'unresolved-upload')['id']
    def legacy(flow):
        flow.setdefault('consent',{}).setdefault('actions',{})['legacy']={
            'kind':'hpc_upload','state':'unknown','binding':{},'action_id':'legacy'}
    svc.store.mutate_file_task(project,other,legacy)
    response=client.post(base+'/consents/'+action['action_id'],json={'approved':True})
    assert response.status_code==200,response.text
    assert response.json()['card']['state']=='executed'
    assert world.begins==world.commits==1
    assert not svc.files.queue


def test_old_root_observation_cannot_save_after_new_owner_acquires(owner, monkeypatch):
    from backend.toolbox.service import ExecutionService
    client,svc,world,project,task=owner
    observed,release=threading.Event(),threading.Event()
    original=MemoryRemote.inspect_root
    def pause(self,*args,**kwargs):
        result=original(self,*args,**kwargs)
        observed.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(MemoryRemote,'inspect_root',pause)
    results=[]
    thread=threading.Thread(target=lambda:results.append(client.put(
        f'/api/v1/toolbox/projects/{project}/tasks/{task}/file-roots',
        json={'expected_version':0,'roots':[{'path':'/research'}]})))
    thread.start()
    assert observed.wait(2)
    next_owner=None
    try:
        svc.close()
        next_owner=ExecutionService(svc.store.root,settings_loader=svc.settings_loader,monitor_enabled=False,file_factory=world.factory).start()
        next_owner.store.create_project('new-owner-preserved')
        durable=(svc.store.root/'execution_store.json').read_bytes()
        release.set();thread.join(3)
        assert not thread.is_alive()
        assert results[0].status_code==409,results[0].text
        assert (svc.store.root/'execution_store.json').read_bytes()==durable
        assert not svc.store.get_task(project,task)['flow'].get('file_roots')
    finally:
        release.set();thread.join(3)
        if next_owner:next_owner.close()


def test_changed_local_upload_input_fails_before_dispatch_marker_or_remote_write(owner, tmp_path):
    from types import SimpleNamespace
    from backend.toolbox import consent
    from backend.toolbox.commands import ToolExecutor
    _client,svc,world,project,task=owner
    workspace=tmp_path/'inputs';workspace.mkdir()
    source=workspace/'INCAR';source.write_bytes(b'approved')
    svc.store.update_task(project,task,local_workspace=str(workspace),hpc_workspace='/research')
    world.execution_mode='Fake'
    remote_calls=[]
    world.mkdir=lambda path:remote_calls.append(('mkdir',path))
    world.atomic_write_file=lambda path,data,**kwargs:remote_calls.append(('write',path))
    binding=dict(operation='hpc_upload',execution_mode='Fake',project_id=project,task_id=task,job_key='j',
        artifact_id='input',source_relative_path='INCAR',source_size=8,source_sha256=hashlib.sha256(b'approved').hexdigest(),
        local_root=str(workspace.resolve()),remote_root='/research',remote_relative_path='j/INCAR',
        file_identity=svc.files.legacy_identity('/research','j/INCAR'))
    card=consent.card_payload(tool='hpc_upload',args={},risk='medium',reason='fixture',batch_key='fixture',kind='hpc_upload',summary='fixture',binding=binding)
    consent.save_card(svc.store,project,task,svc.store.get_task(project,task)['flow'],card)
    consent.resolve_card(svc.store,project,task,card['action_id'],approved=True)
    source.write_bytes(b'changed-after-approval')
    executor=ToolExecutor(store=svc.store,project_id=project,task_id=task,cfg=svc.settings_loader(),orch=SimpleNamespace(hpc=world,execution_mode='Fake'))
    executor.execute_action(card['action_id'])
    saved=consent.get_card(svc.store,project,task,card['action_id'])
    assert saved['state']=='failed',saved
    assert not saved.get('file_dispatch_at')
    assert not remote_calls and not svc.files.leases


@pytest.mark.parametrize('code', ['DESTINATION_CONFLICT', 'SCOPE_EXPIRED'])
def test_confirmed_no_publication_releases_despite_durable_commit_marker(owner, monkeypatch, code):
    client,svc,world,project,task=owner
    def refused(self,permit):
        with self.guard('commit',permit['item_id']):
            pass
        raise RemoteFileError(code,'confirmed no publication',dispatched=code=='DESTINATION_CONFLICT',published=False)
    monkeypatch.setattr(Session,'commit',refused)
    base,scope,_args,action=plan(client,project,task)
    approve(client,base,scope,action)
    assert svc.files.wait_idle()
    saved=client.get(base+'/consents/'+action['action_id']).json()['card']
    assert saved['state']=='failed',saved
    assert any(d['stage']=='commit' for d in saved['receipt']['dispatches'])
    assert saved['receipt']['held_unknown']=={'operations':0,'bytes':0}
    assert saved['receipt']['released']=={'operations':1,'bytes':7}
    assert saved['receipt']['item_outcomes']['text']['state']=='not_executed'
    assert not world.files and world.commits==0
