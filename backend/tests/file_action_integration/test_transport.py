import time
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from backend.toolbox.ssh import remote_files as remote
from backend.tests.remote_file_foundation.test_protocol import manager_fixture, ReplyChannel


def test_commit_permission_expiring_inside_dispatch_guard_sends_nothing(monkeypatch):
    manager,_,transport=manager_fixture()
    channel=ReplyChannel(b'{"ok":true,"data":{}}\n');transport.open_session.return_value=channel
    session=remote.FileSession.__new__(remote.FileSession)
    session.wire=remote._Wire(manager);session._check=lambda:None
    session.prepared={};session.prepared_at=time.monotonic();session.closed=False
    session.manifest={'expires_at':'2099-01-01T00:00:00+00:00'}
    expired=[False]
    @contextmanager
    def gate(*args):
        expired[0]=True
        yield
    session.dispatch_guard=gate
    def remaining(value):
        if expired[0]:raise remote.RemoteFileError('SCOPE_EXPIRED','expired while saving owner dispatch')
        return 1
    monkeypatch.setattr(remote,'_remaining',remaining)
    with pytest.raises(remote.RemoteFileError,match='expired while'):
        session.commit({'valid_until':'2098-01-01T00:00:00+00:00','item_id':'item'})
    assert channel.sent==[] and channel.closed


def test_send_deadline_applies_to_whole_frame_not_each_partial_send():
    manager,_,transport=manager_fixture()
    class Partial(ReplyChannel):
        def __init__(self):super().__init__(b'');self.timeouts=[];self.count=0
        def settimeout(self,value):self.timeouts.append(value)
        def send(self,data):
            time.sleep(min(.01,self.timeouts[-1]))
            self.count+=1
            return 1
    channel=Partial();transport.open_session.return_value=channel
    wire=remote._Wire(manager)
    started=time.monotonic()
    with pytest.raises(remote.RemoteFileError) as caught:
        wire.call({'op':'begin','payload':'x'*1000},writing=True)
    elapsed=time.monotonic()-started
    assert caught.value.code=='ACTION_UNKNOWN' and .18<=elapsed<.5
    assert 0<channel.count<1000 and min(channel.timeouts)<.1
    wire.close()
