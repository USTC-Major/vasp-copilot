"""No background waking module or server wiring remains."""
import importlib.util
from pathlib import Path
def test_auto_wake_module_removed():
    assert importlib.util.find_spec('backend.ai_mode.auto_wake') is None
def test_server_no_wake_or_combined_stop_wiring():
    from backend.ai_mode import server
    text=Path(server.__file__).read_text(encoding='utf-8')
    for symbol in ('AutoWakeLoop','stop_everything','_clear_auto_wake_stop','_set_auto_wake_stopped'): assert symbol not in text
