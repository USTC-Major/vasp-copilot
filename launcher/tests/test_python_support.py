import importlib.util
import json
from pathlib import Path
from collections import namedtuple
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('support_under_test', ROOT/'launcher/python_support.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
Version = namedtuple('Version', 'major minor micro releaselevel serial')


class SupportTests(unittest.TestCase):
    def check(self, minor, *, machine='AMD64', bits=8, free=False, release='final'):
        with patch.object(support.sys, 'version_info', Version(3, minor, 0, release, 0)), \
             patch.object(support.sys, 'platform', 'win32'), \
             patch.object(support.platform, 'python_implementation', return_value='CPython'), \
             patch.object(support.platform, 'machine', return_value=machine), \
             patch.object(support.struct, 'calcsize', return_value=bits), \
             patch.object(support.sysconfig, 'get_config_var', return_value=free):
            return support.compatibility()

    def test_supported_versions_have_locks_and_windows_ci(self):
        policy = support.load_policy()
        workflow = (ROOT/'.github/workflows/desktop-windows.yml').read_text(encoding='utf-8')
        for entry in policy['versions']:
            if entry['status'] == 'supported':
                tag = entry['version'].replace('.', '')
                self.assertTrue((ROOT/f'backend/requirements-win-cp{tag}-x64.lock').is_file())
                self.assertIn("'" + entry['version'] + "'", workflow)
                self.assertTrue(self.check(int(entry['version'].split('.')[1]))[0])

    def test_315_reports_dependency_block_and_future_versions_are_identified(self):
        ok, reason = self.check(15)
        self.assertFalse(ok)
        self.assertIn('Python 3.15', reason)
        self.assertIn('pymatgen-core', reason)
        for minor in (10, 16):
            ok, reason = self.check(minor)
            self.assertFalse(ok)
            self.assertIn(f'Python 3.{minor}', reason)

    def test_architecture_and_release_are_not_confused_with_version_support(self):
        for args in ({'machine': 'ARM64'}, {'bits': 4}, {'free': True}, {'release': 'candidate'}):
            with self.subTest(args=args):
                self.assertFalse(self.check(13, **args)[0])

    def test_missing_or_invalid_manifest_fails_closed(self):
        for error in (OSError(), ValueError(), KeyError(), AssertionError()):
            with patch.object(support, 'load_policy', side_effect=error):
                self.assertFalse(support.compatibility()[0])


if __name__ == '__main__':
    unittest.main()
