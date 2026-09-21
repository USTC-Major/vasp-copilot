"""Corrupt containers must fail before rewriting any existing store bytes."""
import json
import pytest
from backend.ai_mode.chat_store import ChatStore
from backend.toolbox.projects import ProjectStore
from backend.toolbox.owner import ProcessOwner


CHAT = {'schema_version': 1, 'messages': {}, 'task_metadata': {}}
EXECUTION = {'schema_version': 1, 'projects': [], 'tasks': [], 'waiting': [], 'events': []}


@pytest.mark.parametrize('patch', [
    {'messages': []}, {'messages': {'p:t': {}}}, {'messages': {'p:t': ['bad']}},
    {'task_metadata': []}, {'task_metadata': {'p:t': []}},
    {'task_metadata': {'p:t': {'generation': []}}}, {'schema_version': 99},
])
def test_invalid_chat_target_preserves_bytes_and_releases_owner(tmp_path, patch):
    target = tmp_path / 'chat_store.json'
    original = json.dumps({**CHAT, **patch}, separators=(',', ':')).encode()
    target.write_bytes(original)
    with pytest.raises(ValueError, match='Invalid'):
        ChatStore(tmp_path)
    assert target.read_bytes() == original
    owner = ProcessOwner(tmp_path, 'chat').acquire()
    owner.close()


@pytest.mark.parametrize('patch', [
    {'projects': {}}, {'projects': ['bad']}, {'tasks': [3]},
    {'waiting': {}}, {'waiting': [None]}, {'events': {}},
    {'tasks': [{'flow': []}]}, {'tasks': [{'flow': {'plan': []}}]},
    {'tasks': [{'flow': {'plan': {'jobs': ['bad']}}}]},
    {'tasks': [{'flow': {'consent': {'actions': []}}}]},
    {'tasks': [{'flow': {'consent': {'actions': {'a': []}}}}]},
    {'tasks': [{'flow': {'monitor': []}}]}, {'schema_version': 99},
])
def test_invalid_execution_target_preserves_bytes(tmp_path, patch):
    target = tmp_path / 'execution_store.json'
    original = json.dumps({**EXECUTION, **patch}, separators=(',', ':')).encode()
    target.write_bytes(original)
    with pytest.raises(ValueError, match='Invalid'):
        ProjectStore(tmp_path)
    assert target.read_bytes() == original


@pytest.mark.parametrize('store_type,destination', [(ChatStore, 'chat_store.json'), (ProjectStore, 'execution_store.json')])
@pytest.mark.parametrize('patch', [
    {'messages': {'p:t': {}}}, {'messages': {'p:t': [False]}},
    {'tasks': ['bad']}, {'tasks': [{'generation': []}]},
])
def test_invalid_legacy_never_creates_chat_or_execution_destination(tmp_path, store_type, destination, patch):
    source = tmp_path / 'ai_store.json'
    source.write_bytes(json.dumps({'projects': [], 'tasks': [], 'messages': {}, **patch}).encode())
    original = source.read_bytes()
    with pytest.raises(ValueError, match='Invalid'):
        store_type(tmp_path)
    assert source.read_bytes() == original
    assert not (tmp_path / destination).exists()


def test_valid_unknown_chat_fields_survive_reload_and_append(tmp_path):
    value = {**CHAT, 'future_field': {'a': [1, 2]}, 'messages': {'p:t': [{'role': 'user', 'content': 'saved', 'future': True}]},
             'task_metadata': {'p:t': {'future': [7]}}}
    (tmp_path / 'chat_store.json').write_text(json.dumps(value), encoding='utf-8')
    store = ChatStore(tmp_path)
    try:
        store.append('p', 't', 'assistant', 'answer')
        actual = json.loads(store.path.read_text(encoding='utf-8'))
        assert actual['future_field'] == value['future_field']
        assert actual['messages']['p:t'][0] == value['messages']['p:t'][0]
        assert actual['task_metadata'] == value['task_metadata']
    finally:
        store.close()
