"""Standard-library tests for owned environment preparation; no package/network calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'environment.py'
spec = importlib.util.spec_from_file_location('installer_under_test', SOURCE)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@unittest.skipUnless(sys.platform == 'win32', 'Windows process and file lock tests')
class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='中文 installer tests ')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base/'source'
        (self.root/'backend').mkdir(parents=True)
        self.lock = self.root/'backend/requirements-win-cp312-x64.lock'
        self.lock.write_text('demo==1.0 --hash=sha256:' + '0'*64 + '\n', encoding='utf-8')
        self.state = self.base/'独立 状态'
        self.state.mkdir()
        self.log = self.state/'install.local.log'
        self.log.touch()
        self.args = SimpleNamespace(root=self.root, state=self.state)
        self.stages = []
        self.commands = []
        self.addCleanup(patch.stopall)
        patch.object(installer, 'platform_spec', return_value='cp312').start()
        patch.dict(os.environ, {}, clear=True).start()
        self.command_patch = patch.object(installer, 'command', side_effect=self.fake_command).start()
        self.probe_patcher = patch.object(installer, 'probe', return_value=True)
        self.probe_patch = self.probe_patcher.start()

    def fake_command(self, arguments, log, env):
        self.commands.append(arguments)
        if 'venv' in arguments:
            python = Path(arguments[-1])/'Scripts/python.exe'
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
        return 0

    def prepare(self):
        return installer.prepare(self.args, lambda stage, message: self.stages.append(stage), self.log)

    def test_install_command_is_hash_locked_binary_only_official_and_cached(self):
        result = self.prepare()
        self.assertTrue(result['ok'])
        install = next(c for c in self.commands if 'install' in c)
        for option in ('--isolated', '--no-user', '--require-hashes', '--only-binary=:all:'):
            self.assertIn(option, install)
        self.assertEqual(install[install.index('--index-url')+1], 'https://pypi.org/simple')
        self.assertEqual(install[install.index('--cache-dir')+1], str(self.state/'pip-cache'))
        self.assertEqual(Path(result['python']).parent.parent.parent, self.state/'environments')
        self.assertEqual(self.stages, ['create', 'install', 'verify'])
        for command in self.commands:
            self.assertEqual(command[1:4], ['-I', '-X', 'utf8'])

    def test_dependency_probe_explicitly_uses_utf8_with_isolation(self):
        python = self.base/'python.exe'
        python.touch()
        # Exercise the real probe argument construction, rather than its prepare stub.
        self.probe_patcher.stop()
        self.assertTrue(installer.probe(python, {'demo':'1.0'}, self.log, {}))
        self.assertEqual(self.commands[-1][1:4], ['-I', '-X', 'utf8'])

    def test_success_reuses_without_pip_or_creation(self):
        result = self.prepare()
        before = len(self.commands)
        self.assertEqual(self.prepare(), result)
        self.assertEqual(len(self.commands), before)
        self.assertEqual(self.stages[-1], 'verify')

    def test_failed_install_never_marks_success_and_retry_recovers(self):
        def failing(arguments, log, env):
            self.fake_command(arguments, log, env)
            return 1 if 'install' in arguments else 0
        self.command_patch.side_effect = failing
        with self.assertRaises(installer.PreparationError) as failure:
            self.prepare()
        self.assertEqual(failure.exception.code, 'DEPENDENCY_INSTALL_FAILED')
        self.assertEqual(list(self.state.rglob('.complete.json')), [])
        self.command_patch.side_effect = self.fake_command
        self.assertTrue(self.prepare()['ok'])
        self.assertEqual(len(list(self.state.rglob('.complete.json'))), 1)

    def test_cancellation_releases_lock_and_retry_recovers(self):
        def cancelled(arguments, log, env):
            self.fake_command(arguments, log, env)
            if 'install' in arguments:
                raise KeyboardInterrupt()
            return 0
        self.command_patch.side_effect = cancelled
        with self.assertRaises(KeyboardInterrupt):
            self.prepare()
        self.assertEqual(list(self.state.rglob('.complete.json')), [])
        self.command_patch.side_effect = self.fake_command
        self.assertTrue(self.prepare()['ok'])

    def test_new_lock_keeps_old_usable_venv_at_original_path(self):
        first = self.prepare()
        old_marker = Path(first['python']).parent.parent/'.complete.json'
        old_bytes = old_marker.read_bytes()
        self.lock.write_text('demo==2.0 --hash=sha256:' + '1'*64 + '\n', encoding='utf-8')
        second = self.prepare()
        self.assertNotEqual(first['environment_id'], second['environment_id'])
        self.assertTrue(Path(first['python']).exists())
        self.assertEqual(old_marker.read_bytes(), old_bytes)

    def test_proxy_marker_refuses_install_without_credentials_in_error(self):
        os.environ['VASP_INSTALLER_PROXY_CONFIGURED'] = '1'
        os.environ['HTTPS_PROXY'] = 'http://synthetic-user:synthetic-password@invalid'
        with self.assertRaises(installer.PreparationError) as failure:
            self.prepare()
        self.assertEqual(failure.exception.code, 'PROXY_UNSUPPORTED')
        self.assertNotIn('synthetic-password', str(failure.exception))
        self.assertEqual(self.commands, [])

    def test_ready_environment_reuses_even_with_proxy_marker(self):
        result = self.prepare()
        os.environ['VASP_INSTALLER_PROXY_CONFIGURED'] = '1'
        self.assertEqual(self.prepare(), result)

    def test_child_environment_excludes_proxy_pip_settings_and_business_secrets(self):
        traps = {'HTTPS_PROXY':'credential-proxy', 'PIP_EXTRA_INDEX_URL':'credential-index',
                 'OPENAI_API_KEY':'synthetic-key', 'AI_MODE_LLM_API_KEY':'synthetic-key',
                 'PYTHONPATH':'synthetic-import-path', 'PATH':'system-path', 'WINDIR':'C:\\Windows'}
        os.environ.update(traps)
        before = dict(os.environ)
        env = installer.child_environment(self.state)
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(env['PATH'], 'system-path')
        self.assertEqual(env['PIP_CONFIG_FILE'], os.devnull)
        for name in traps.keys()-{'PATH','WINDIR'}:
            self.assertNotIn(name, env)

    def test_missing_pip_has_explicit_code(self):
        def failing(arguments, log, env):
            self.fake_command(arguments, log, env)
            return 1 if '--version' in arguments else 0
        self.command_patch.side_effect = failing
        with self.assertRaises(installer.PreparationError) as failure:
            self.prepare()
        self.assertEqual(failure.exception.code, 'PIP_UNAVAILABLE')

    def test_network_failure_has_safe_explicit_code(self):
        self.log.write_text('ConnectionError: max retries exceeded', encoding='utf-8')
        failure = installer.installation_error(self.log)
        self.assertEqual(failure.code, 'DEPENDENCY_NETWORK_FAILED')
        self.assertNotIn('ConnectionError', failure.message)

    def test_missing_wheel_lock_and_invalid_lock_are_explicit(self):
        self.lock.unlink()
        with self.assertRaises(installer.PreparationError) as failure:
            self.prepare()
        self.assertEqual(failure.exception.code, 'LOCK_MISSING')
        self.lock.write_text('demo>=1\n', encoding='utf-8')
        with self.assertRaises(installer.PreparationError) as failure:
            self.prepare()
        self.assertEqual(failure.exception.code, 'LOCK_INVALID')

    def test_half_venv_never_reuses_without_complete_marker(self):
        self.prepare()
        marker = next(self.state.rglob('.complete.json'))
        marker.unlink()
        self.commands.clear()
        self.assertTrue(self.prepare()['ok'])
        self.assertTrue(any('install' in c for c in self.commands))
        self.assertIn('--clear', next(c for c in self.commands if 'venv' in c))

    def test_completed_but_corrupt_environment_rebuilds_in_place(self):
        first = self.prepare()
        self.commands.clear()
        self.probe_patch.side_effect = [False, True]
        repaired = self.prepare()
        self.assertEqual(first['environment_id'], repaired['environment_id'])
        self.assertEqual(first['python'], repaired['python'])
        self.assertIn('--clear', next(c for c in self.commands if 'venv' in c))
        self.assertTrue(any('install' in c for c in self.commands))

    def test_reparse_environment_parent_is_rejected(self):
        original = installer.is_reparse
        with patch.object(installer, 'is_reparse', side_effect=lambda path: path.name == 'environments' or original(path)):
            with self.assertRaises(installer.PreparationError) as failure:
                self.prepare()
        self.assertEqual(failure.exception.code, 'ENVIRONMENT_INVALID')
        self.assertEqual(self.commands, [])

    def test_process_exit_releases_file_lock_and_concurrent_owner_is_rejected(self):
        lock = self.state/'shared.lock'
        ready = self.state/'holder.ready'
        code = ("import importlib.util,time;from pathlib import Path;"
                "s=importlib.util.spec_from_file_location('helper'," + repr(str(SOURCE)) + ");"
                "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                "c=m.environment_lock(Path(" + repr(str(lock)) + "));c.__enter__();"
                "Path(" + repr(str(ready)) + ").touch();time.sleep(60)")
        process = subprocess.Popen([sys.executable, '-I', '-c', code],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic()+10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(ready.exists())
            with self.assertRaises(installer.PreparationError) as failure:
                with installer.environment_lock(lock):
                    self.fail('concurrent acquisition succeeded')
            self.assertEqual(failure.exception.code, 'ENVIRONMENT_BUSY')
        finally:
            process.terminate()
            process.wait(timeout=10)
        with installer.environment_lock(lock):
            pass


class PureTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32' and sys.version_info[:2] in {(3, 11), (3, 12)},
                         'Supported Windows Python default stream encoding')
    def test_isolated_child_needs_explicit_utf8_for_chinese_log_output(self):
        # Model a redirected Western-locale stream without changing the machine's
        # code page. The actual interpreter UTF-8 flag chooses the output encoding.
        code = ("import io,sys;"
                "print('utf8_mode='+str(sys.flags.utf8_mode),file=sys.stderr);"
                "sys.stdout=io.TextIOWrapper(sys.stdout.buffer,"
                "encoding=('utf-8' if sys.flags.utf8_mode else 'ascii'),errors='strict');"
                "print(sys.argv[1])")
        env = {k:v for k,v in os.environ.items() if k.upper() in
               {'SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP','COMSPEC'}}
        env['PYTHONUTF8'] = '1'
        path = 'C:\\中文 环境\\Lib\\site-packages\\pip'
        baseline = subprocess.run([sys.executable, '-I', '-c', code, path], env=env,
                                  capture_output=True)
        self.assertNotEqual(baseline.returncode, 0)
        self.assertIn(b'utf8_mode=0', baseline.stderr)
        self.assertIn(b'UnicodeEncodeError', baseline.stderr)
        explicit = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', code, path],
                                  env=env, capture_output=True)
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertIn(b'utf8_mode=1', explicit.stderr)
        self.assertEqual(explicit.stdout.decode('utf-8').strip(), path)

    def test_unsupported_python_is_explicit(self):
        with patch.object(installer.sys, 'platform', 'not-windows'):
            with self.assertRaises(installer.PreparationError) as failure:
                installer.platform_spec()
        self.assertEqual(failure.exception.code, 'UNSUPPORTED_PYTHON')

    def test_absolute_paths_and_atomic_json(self):
        with self.assertRaises(Exception):
            installer.absolute('relative-path')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'result.json'
            installer.atomic_json(path, {'message':'中文 结果'})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'message':'中文 结果'})
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
