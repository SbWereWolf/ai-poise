"""Independent raw-stream oracles at the existing Git adapter boundaries."""
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from poise.common import PoiseError
from poise.infrastructure.projects import FileProjectSetup
from poise.infrastructure.repository_tree import GitRepositoryTree
from poise.infrastructure.result_integration import RuntimeResultIntegration
from poise.infrastructure.task_cleanup import RuntimeTaskResourceCleanup
from poise.runtime import Poise


FIXTURE = Path(__file__).with_name('fixtures') / 'output_boundary_payload.json'


class RawBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name)
        self.cfg = {'limits': {'git_seconds': 10, 'preview_chars': 24}}
        self.runtime = SimpleNamespace(cfg=self.cfg)
        self.fixture = json.loads(FIXTURE.read_text())
        subprocess.run(['git', 'init', '-q', str(self.repository)], check=True)

    def run_git(self, *args):
        return subprocess.run(['git', '--no-optional-locks', '-C', str(self.repository), *args],
                              capture_output=True, check=False)

    def test_cleanup_keeps_complete_nul_stdout(self):
        names = self.fixture['inventory_paths']
        for name in names:
            (self.repository / name).write_bytes(b'fixture content\n')
        self.assertEqual(self.run_git('add', '--', *names).returncode, 0)
        expected = b''.join(name.encode() + b'\0' for name in sorted(names))
        oracle = self.run_git('ls-files', '-z')
        self.assertEqual(oracle.stdout, expected)
        result = RuntimeTaskResourceCleanup(self.runtime)._run(self.repository, 'ls-files', '-z')
        self.assertEqual(result['actual_exit_code'], 0)
        self.assertEqual(result['stdout'].encode(), expected,
                         'Cleanup must retain every independently specified NUL path, including the first record')
        self.assertEqual(result['stderr'], '')

    def test_cleanup_and_integration_preserve_failure_stderr(self):
        revision = self.fixture['missing_revision']
        oracle = self.run_git('show', revision)
        self.assertEqual(oracle.returncode, 128)
        self.assertIn(revision.encode(), oracle.stderr)
        for adapter in (RuntimeTaskResourceCleanup(self.runtime), RuntimeResultIntegration(self.runtime)):
            with self.subTest(adapter=type(adapter).__name__):
                result = adapter._run(self.repository, 'show', revision)
                self.assertEqual(result['actual_exit_code'], 128)
                self.assertEqual(result['stdout'], '')
                self.assertEqual(result['stderr'].encode(), oracle.stderr)

    def test_empty_equal_below_and_above_budget_are_full(self):
        for size in (0, 23, 24, 25, 240):
            out, err = 'A' * size, 'B' * size
            for adapter in (RuntimeTaskResourceCleanup(self.runtime), RuntimeResultIntegration(self.runtime)):
                with self.subTest(size=size, adapter=type(adapter).__name__):
                    def completed(*args, **kwargs):
                        return subprocess.CompletedProcess(args[0], 7,
                            out if kwargs.get('text') else out.encode(),
                            err if kwargs.get('text') else err.encode())
                    with patch('subprocess.run', side_effect=completed):
                        result = adapter._run(self.repository, 'status')
                    self.assertEqual((result['actual_exit_code'], result['stdout'], result['stderr']), (7, out, err))

    def test_actual_crlf_unicode_paths_are_not_translated(self):
        names = self.fixture['control_paths']
        for name in names:
            (self.repository / name).write_bytes(b'content')
        self.assertEqual(self.run_git('add', '--', *names).returncode, 0)
        expected = b''.join(name.encode() + b'\0' for name in sorted(names))
        self.assertEqual(self.run_git('ls-files', '-z').stdout, expected)
        for adapter in (RuntimeTaskResourceCleanup(self.runtime), RuntimeResultIntegration(self.runtime)):
            with self.subTest(adapter=type(adapter).__name__):
                result = adapter._run(self.repository, 'ls-files', '-z')
                self.assertEqual(result['stdout'].encode(), expected)

    def test_runtime_git_error_keeps_original_diagnostic(self):
        detail = self.fixture['full_error']
        with patch('poise.runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 128, '', detail)):
            with self.assertRaises(PoiseError) as caught:
                Poise._git(self.runtime, self.repository, 'status')
        self.assertTrue(str(caught.exception).endswith(detail))

    def test_all_repository_tree_error_branches_keep_original_diagnostic(self):
        detail = self.fixture['full_error']
        adapter = GitRepositoryTree(self.repository, 10, 24)
        calls = (lambda: adapter._read_path_facts(['status'], None, (0,)),
                 lambda: adapter.existing_paths('missing', ('file',)),
                 lambda: adapter.contains_commit('missing', 'missing'))
        for call in calls:
            with self.subTest(call=call):
                with patch('poise.infrastructure.repository_tree.subprocess.run',
                           return_value=subprocess.CompletedProcess([], 128, b'', detail.encode())):
                    with self.assertRaises(PoiseError) as caught:
                        call()
                self.assertTrue(str(caught.exception).endswith(detail))

    def test_project_probe_error_keeps_original_diagnostic(self):
        detail = self.fixture['full_error']
        setup = object.__new__(FileProjectSetup)
        setup.settings = SimpleNamespace(raw={'git_seconds': 10})
        cfg = {'git': {'push_required': False, 'repository': str(self.repository)},
               'limits': {'preview_chars': 24}}
        with patch('poise.infrastructure.projects.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 128, '', detail)):
            with self.assertRaises(PoiseError) as caught:
                setup._probe(cfg, True)
        self.assertTrue(str(caught.exception).endswith(detail))

    def test_unfinished_or_unspawned_git_never_returns_success(self):
        for failure in (OSError('spawn unavailable'), subprocess.TimeoutExpired(['git'], 10)):
            for adapter in (RuntimeTaskResourceCleanup(self.runtime), RuntimeResultIntegration(self.runtime)):
                with self.subTest(failure=type(failure).__name__, adapter=type(adapter).__name__):
                    with patch('subprocess.run', side_effect=failure), self.assertRaises(PoiseError):
                        adapter._run(self.repository, 'status')

    def test_invalid_text_decode_does_not_silently_replace_bytes(self):
        for adapter in (RuntimeTaskResourceCleanup(self.runtime), RuntimeResultIntegration(self.runtime)):
            with self.subTest(adapter=type(adapter).__name__):
                def invalid(*args, **kwargs):
                    if kwargs.get('text'):
                        raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid')
                    return subprocess.CompletedProcess(args[0], 0, b'\xff', b'')
                with patch('subprocess.run', side_effect=invalid), self.assertRaises((UnicodeError, PoiseError)):
                    adapter._run(self.repository, 'status')
