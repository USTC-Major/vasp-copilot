"""HTTP consent facade; the AI process never claims execution actions."""
from contextlib import nullcontext
from backend.toolbox.contracts import ToolboxError

def task_lock(*args):
    return nullcontext()

def list_cards(store, project_id, task_id):
    return store.client.request('GET', store.client.task_path(project_id, task_id) + '/consents')['cards']

def get_card(store, project_id, task_id, card_id):
    try:
        return store.client.request('GET', store.client.task_path(project_id, task_id) + '/consents/' + card_id)['card']
    except ToolboxError as exc:
        if exc.status == 404:
            return None
        raise

def resolve_card(store, project_id, task_id, card_id, *, approved, note=''):
    return store.client.request('POST', store.client.task_path(project_id, task_id) + '/consents/' + card_id,
                                json={'approved': approved, 'note': note})

def spawn_submit_card(store, project_id, task_id):
    result = store.client.tool(project_id, task_id, 'submit', {})
    if not result.get('pending'):
        raise ValueError(result.get('result') or '提交前置条件不满足')
    return result['pending']
