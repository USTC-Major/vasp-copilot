"""Workspace selection never confers implicit permission."""
from pathlib import Path
from backend.toolbox import api
def test_task_grant_helpers_removed():
    for name in ('record_task_file_grant','hydrate_task_file_grant','schedule_task_file_grant_hydration'): assert not hasattr(api,name)
def test_api_has_no_background_grant_thread():
    assert 'toolbox-file-grant' not in Path(api.__file__).read_text(encoding='utf-8')
