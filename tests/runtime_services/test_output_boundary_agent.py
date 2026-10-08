"""Full machine payloads and configured agent views at real transport boundaries."""
from copy import deepcopy
import io
import json
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from poise.common import PoiseError, file_digest
from poise.infrastructure.result_views import OutputParser, ResultViews
from poise.interfaces.work import write_result
from poise.interfaces.hook_transport import _write
from poise.interfaces import catalogue, goal_config, hook_transport
from poise.modules.result_views.domain import OutputPolicy


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name('fixtures') / 'output_boundary_payload.json'


class AgentBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = json.loads(FIXTURE.read_text())
        self.policy = json.loads((ROOT / 'config/runtime.example.json').read_text())['output']
        self.policy['known'][0]['selection_pattern'] = '^SIGNAL'
        self.policy['known'][0]['primary_chars'] = 96
        self.policy['unknown']['primary_chars'] = 96
        self.events = []
        self.views = ResultViews(self.policy, lambda name, data: self.events.append((name, data)))
        self.addCleanup(self.views.finish)
        self.runtime = SimpleNamespace(
            cfg={'limits': {'output_chars': 2000, 'preview_chars': 24},
                 'batch': {'file_mode': 0o600}, 'runtime_services': {'output': self.policy}},
            runtime=self.root, paths={'runs': 'runs', 'response': 'response.json'},
            report_task=lambda result: None, session='agent-boundary-test', result_views=self.views)

    def command_receipt(self, known=True):
        directory = self.root / ('known' if known else 'unknown')
        directory.mkdir()
        out = directory / 'stdout.txt'
        err = directory / 'stderr.txt'
        out.write_text(self.fixture['known_stdout' if known else 'unknown_stdout'])
        err.write_text(self.fixture['known_stderr' if known else 'unknown_stderr'])
        return {'id': directory.name, 'stdout': str(out), 'stderr': str(err),
                'stdout_digest': file_digest(out), 'stderr_digest': file_digest(err),
                'argv': ['python', '-m', 'unittest'] if known else ['git', 'status'],
                'actual_exit_code': 128, 'capture_complete': True, 'timed_out': False,
                'passed': False, 'method': 'COMMAND', 'duration_seconds': 0.1}

    def assert_full_response(self, output, payload, cap):
        self.assertLessEqual(len(output.getvalue()), cap)
        view = json.loads(output.getvalue())
        self.assertEqual(view['status'], payload['status'])
        self.assertEqual(json.loads(Path(view['response_path']).read_text()), payload)
        return view

    def test_work_delivery_uses_common_parser(self):
        receipt = self.command_receipt()
        payload = deepcopy(self.fixture['structured_result'])
        payload['checks'] = [receipt]
        before = deepcopy(payload)
        calls = []
        original = OutputParser.render
        def render(parser, mode, captured, directory):
            calls.append((parser.profile['id'], mode, captured['id']))
            return original(parser, mode, captured, directory)
        output = io.StringIO()
        with patch.object(OutputParser, 'render', render):
            write_result(self.runtime, payload, output)
            self.views.finish()
        self.assert_full_response(output, before, 2000)
        self.assertEqual(payload, before)
        self.assertEqual(file_digest(Path(receipt['stderr'])), receipt['stderr_digest'])
        self.assertIn(('unittest', 'primary', receipt['id']), calls,
                      'Agent delivery must use the existing configured parser for the real command receipt')
        self.assertIn('SIGNAL selected known command', output.getvalue())
        self.assertNotIn('BEGIN-' + 'L' * 100, output.getvalue())

    def test_unknown_command_view_uses_ordinary_profile_without_changing_raw(self):
        receipt = self.command_receipt(False)
        payload = deepcopy(self.fixture['structured_result'])
        payload['checks'] = [receipt]
        output = io.StringIO()
        write_result(self.runtime, payload, output)
        self.views.finish()
        self.assert_full_response(output, payload, 2000)
        self.assertIn('UNKNOWN terminal summary', output.getvalue())
        self.assertEqual(Path(receipt['stdout']).read_text(), self.fixture['unknown_stdout'])
        self.assertEqual(payload['checks'][0]['actual_exit_code'], 128)

    def test_work_full_nested_error_and_status_are_unchanged(self):
        payload = deepcopy(self.fixture['structured_result'])
        before = deepcopy(payload)
        output = io.StringIO()
        write_result(self.runtime, payload, output)
        self.assert_full_response(output, before, 2000)
        self.assertEqual(payload, before)

    def test_work_equal_below_above_character_budget_uses_valid_json(self):
        payload = {'status': 'read_only', 'value': 'Ж'}
        size = len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n')
        # Small replies at/equal/below need no pointer. A large configured cap
        # leaves room for a truthful pointer instead of forcing impossible JSON.
        for cap in (size, size + 1):
            with self.subTest(cap=cap):
                self.runtime.cfg['limits']['output_chars'] = cap
                output = io.StringIO()
                write_result(self.runtime, payload, output)
                self.assertEqual(json.loads(output.getvalue()), payload)
                self.assertLessEqual(len(output.getvalue()), cap)
        self.runtime.cfg['limits']['output_chars'] = 2000
        output = io.StringIO()
        write_result(self.runtime, deepcopy(self.fixture['structured_result']), output)
        self.assert_full_response(output, self.fixture['structured_result'], 2000)

    def test_hook_envelope_keeps_full_structured_response(self):
        payload = deepcopy(self.fixture['structured_result'])
        before = deepcopy(payload)
        settings = SimpleNamespace(observations=self.root / 'hook',
            raw={'output_chars': 2000, 'response_file': 'response.json', 'file_mode': 0o600})
        output = io.StringIO()
        _write(SimpleNamespace(settings=settings), payload, output)
        self.assert_full_response(output, before, 2000)
        self.assertEqual(payload, before)

    def test_native_hook_control_protocol_is_preserved(self):
        protocol = {'hookSpecificOutput': {'hookEventName': 'SessionStart',
                                          'additionalContext': 'Required task context'}}
        service = SimpleNamespace(settings=SimpleNamespace(raw={
            'max_input_bytes': 20000, 'output_chars': 2000,
            'exit_codes': {'success': 0}}), event=lambda definition, packet: protocol)
        output, error = io.StringIO(), io.StringIO()
        with patch.object(hook_transport, 'HookService', return_value=service):
            code = hook_transport.execute('hook', SimpleNamespace(settings='fixture', definition='native'),
                                          io.BytesIO(b'{}'), output, error)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), protocol)
        self.assertEqual(error.getvalue(), '')

    def test_failed_full_response_persistence_never_emits_success(self):
        output = io.StringIO()
        with patch('poise.interfaces.work.atomic_write', side_effect=OSError('storage unavailable')):
            with self.assertRaises(OSError):
                write_result(self.runtime, deepcopy(self.fixture['structured_result']), output)
        self.assertEqual(output.getvalue(), '')

    def editor_settings(self):
        return SimpleNamespace(root=self.root, path=self.root / 'settings.json', raw={
            'output_chars': 2000, 'responses': 'editor-responses', 'file_mode': 0o600,
            'json_indent': 2, 'max_input_bytes': 20000,
            'exit_codes': {'success': 0, 'rejected': 2, 'pending': 3}})

    def test_goal_config_envelope_keeps_full_result_and_business_status(self):
        settings = self.editor_settings()
        payload = deepcopy(self.fixture['structured_result'])
        payload['status'] = 'read_only'
        tools = SimpleNamespace(status=lambda request: payload)
        output = io.StringIO()
        with patch.object(goal_config, 'EditorSettings', return_value=settings), \
                patch.object(goal_config, 'goal_config_tools', return_value=tools):
            code = goal_config.execute(settings.path, io.BytesIO(b'{"schema":"goal-config-status-1"}'), output)
        self.assertEqual(code, 0)
        self.assert_full_response(output, payload, 2000)
        self.assertEqual(payload['nested']['actual_exit_code'], 128)

    def test_catalogue_envelope_keeps_full_result_without_fake_success(self):
        settings = self.editor_settings()
        payload = deepcopy(self.fixture['structured_result'])
        repository = SimpleNamespace(editor=settings, root=self.root, raw={'max_items': 10})
        commands = SimpleNamespace(install=lambda items: payload)
        output = io.StringIO()
        with patch.object(catalogue, 'FileCatalogue', return_value=repository), \
                patch.object(catalogue, 'goal_config_tools', return_value=object()), \
                patch.object(catalogue, 'CatalogueCommands', return_value=commands):
            code = catalogue.execute(self.root / 'catalogue.json',
                io.BytesIO(b'{"action":"install","items":[],"project_config":null}'), output)
        self.assertEqual(code, 0)
        self.assert_full_response(output, payload, 2000)
        self.assertFalse(payload['nested']['capture_complete'])

    def test_structured_routes_use_one_presentation_owner(self):
        # Observe real executed project frames, without importing a future helper
        # or prescribing its name, class, signature or command-parser profile.
        # An owner must see the complete result AND form bounded valid JSON with
        # its truthful receipt pointer. Persistence-only functions do not qualify.
        source = (ROOT / 'src' / 'poise').resolve()
        marker = self.fixture['structured_result']['diagnostic']
        routes = {
            'work': self.test_work_full_nested_error_and_status_are_unchanged,
            'hook': self.test_hook_envelope_keeps_full_structured_response,
            'goal-config': self.test_goal_config_envelope_keeps_full_result_and_business_status,
            'catalogue': self.test_catalogue_envelope_keeps_full_result_without_fake_success,
        }
        owners = {}
        previous = sys.getprofile()
        try:
            for name, invoke in routes.items():
                seen = set()
                def observe(frame, event, returned):
                    if event != 'return' or not Path(frame.f_code.co_filename).resolve().is_relative_to(source):
                        return
                    values = tuple(frame.f_locals.values())
                    complete = [value for value in values if isinstance(value, dict)
                                and value.get('diagnostic') == marker]
                    if not complete:
                        return
                    for candidate in (returned, *values):
                        if isinstance(candidate, str):
                            if len(candidate) > 2000:
                                continue
                            try:
                                candidate = json.loads(candidate)
                            except (ValueError, TypeError):
                                continue
                        if (isinstance(candidate, dict)
                                and isinstance(candidate.get('response_path'), str)
                                and candidate.get('status') in {value['status'] for value in complete}
                                and len(json.dumps(candidate, ensure_ascii=False) + '\n') <= 2000):
                            seen.add((str(Path(frame.f_code.co_filename).resolve()), frame.f_code.co_qualname))
                            break
                sys.setprofile(observe)
                invoke()  # Existing independent full-file/status/budget assertions.
                sys.setprofile(previous)
                owners[name] = seen
        finally:
            sys.setprofile(previous)
        common = set.intersection(*owners.values())
        self.assertTrue(common, 'All four agent routes must execute one shared presentation owner; '
                        f'observed owners by route: {owners}')

    def test_known_and_unknown_selection_are_explicit(self):
        policy = OutputPolicy.parse(self.policy)
        self.assertEqual(policy.select(['python', '-m', 'unittest'])['id'], 'unittest')
        self.assertEqual(policy.select(['git', 'status'])['id'], 'unknown')
        duplicate = deepcopy(self.policy)
        duplicate['known'].append({**duplicate['known'][0], 'id': 'second-known'})
        with self.assertRaises(PoiseError):
            OutputPolicy.parse(duplicate).select(['python', '-m', 'unittest'])

    def test_raw_tamper_does_not_become_a_successful_command(self):
        receipt = self.command_receipt()
        Path(receipt['stdout']).write_text('tampered')
        result = self.views.capture(receipt, self.root / 'capture')
        self.assertEqual(result['status'], 'error')
        self.assertFalse(receipt['passed'])
        self.assertEqual(receipt['actual_exit_code'], 128)
        self.assertTrue(self.views.finish())
