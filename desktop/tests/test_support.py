"""Portable test paths and exact-process teardown; never use normal user state."""
import atexit
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
children = []

def output(group):
    base = Path(os.environ.get('VASP_DESKTOP_TEST_OUTPUT', str(ROOT/'desktop/.cache/test-runs')))
    if not base.is_absolute():
        raise ValueError('VASP_DESKTOP_TEST_OUTPUT must be an absolute test-only directory')
    return base/group/(time.strftime('%Y%m%dT%H%M%S') + '-' + str(os.getpid()))

def harness(name):
    return ROOT/'desktop/.cache/test-bin'/name

def start_process(*args, **kwargs):
    env = dict(kwargs.get('env', os.environ))
    state = Path(env.get('VASP_LAUNCHER_STATE_DIR', str(ROOT/'desktop/.cache/test-runs/runtime-state')))
    # Dependency imports and synthetic normal-priority probes also get a test HOME.
    env.update(HOME=str(state/'user'), USERPROFILE=str(state/'user'), PYTHONUTF8='1')
    kwargs['env'] = env
    child = subprocess.Popen(*args, **kwargs)
    children.append(child)
    return child

@atexit.register
def cleanup():
    for child in reversed(children):
        if child.poll() is not None:
            continue
        # Harness termination closes its owned Windows Job; it never targets port owners.
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
