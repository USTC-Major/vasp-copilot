"""Run selected application regressions in a prepared runtime with isolated test tools.

pytest is installed into --tools, never into the managed runtime. External
service calls and real credentials are forbidden. Tests use synthetic inputs.
"""
import argparse
import ipaddress
import os
from pathlib import Path
import runpy
import socket
import sys

p = argparse.ArgumentParser()
p.add_argument('--tools', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
ROOT = Path(__file__).resolve().parents[2]
out = a.output.resolve()
out.mkdir(parents=True, exist_ok=True)
for key in list(os.environ):
    if key.startswith(('AI_MODE_', 'TOOLBOX_', 'VASP_REVIEWER_', 'OPENAI_', 'MP_API_')):
        os.environ.pop(key)
os.environ.update(HOME=str(out/'home'), USERPROFILE=str(out/'home'),
                  VASP_AI_HOME=str(out/'home'), DATA_DIR=str(out/'data'),
                  ENABLE_LLM='false', ENABLE_AI_MODE='false', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
import dotenv
dotenv.dotenv_values = lambda *a, **k: {}
dotenv.load_dotenv = lambda *a, **k: False
import keyring
from keyring.backend import KeyringBackend


class NoCredentials(KeyringBackend):
    priority = 1
    def get_password(self, *args): return None
    def set_password(self, *args): raise AssertionError('Real credential writes forbidden')
    def delete_password(self, *args): raise AssertionError('Real credential writes forbidden')


keyring.set_keyring(NoCredentials())
original = socket.socket.connect


def local_connect(self, address):
    if not ipaddress.ip_address(address[0]).is_loopback:
        raise AssertionError('External service network forbidden')
    return original(self, address)


socket.socket.connect = local_connect
sys.path.append(str(a.tools.resolve()))
sys.path.insert(0, str(ROOT/'backend'))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
sys.argv = ['pytest', '-q', '--basetemp', str(out/'pytest'), '--junitxml', str(out/'results.xml'),
            '-o', 'cache_dir=' + str(out/'cache'),
            'backend/tests/test_workflow_api.py', 'backend/tests/test_workflow_potcar.py',
            'backend/tests/test_diagnosis_real_case_regressions.py', 'backend/tests/test_potcar_assembly.py']
runpy.run_module('pytest', run_name='__main__')
