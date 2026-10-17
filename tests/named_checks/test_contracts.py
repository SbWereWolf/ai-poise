"""Public requests, selection, roots and consumers; exact independent effects."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest

from .support import ROOT, Scene, beta_project, canonical, digest, git, literal, schema, snapshot


def test_list_describe_preview(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    before = (snapshot(s.state), s.profiles_path.read_bytes())
    for kind in ['profiles', 'packages', 'suites']:
        code, value, _ = s.invoke(s.request('list', items=[{'kind': kind, 'platform': None}]))
        assert code == 0 and value['status'] == 'ready' and value['items']
    code, described, _ = s.invoke(s.request('describe'))
    assert code == 0 and described['items'][0]['kind'] == 'profile'
    row = described['items'][0]['value']
    assert row['selection'] == s.selection() and row['profile']['argv'] == s.profile()['argv']
    code, preview, _ = s.invoke(s.request('preview'))
    assert code == 0 and preview['status'] == 'planned'
    step = preview['items'][0]['steps'][0]
    assert step['argv'] == s.profile()['argv'] and step['cwd'] == str(s.checkout)
    assert not s.markers() and snapshot(s.state / 'cache') == before[0].get('cache', {})
    assert s.profiles_path.read_bytes() == before[1]
    assert not (s.state / 'named-runs.sqlite').exists()


def test_selection_ambiguity_and_optional_warnings(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    small = s.profile('unit', marker='small', workload_class='small')
    large = deepcopy(small)
    large['workload_class'] = 'large'
    large['argv'] = [*large['argv'][:-1], 'large']
    s.profiles['commands'].append(large)
    s.save()
    request = s.request(selection=s.selection('unit', workload=None, runtime=None))
    error = s.error(request, 'selection_ambiguous')
    assert error['field'].endswith('.workload_class')
    assert 'small' in error['reason'] + error['cause'] and 'large' in error['recovery']
    selected = s.run(selection=s.selection('unit', workload='small', runtime=None))
    assert s.record(selected)['selection']['workload_class'] == 'small'
    other = s.run(selection=s.selection('unit', workload='large', runtime=None))
    assert other['kind'] == 'execution' and other['cache']['key'] != selected['cache']['key']
    assert [v['marker'] for v in s.markers()] == ['small', 'large']
    warning = s.request('describe', s.selection('sample', workload='unknown-optional', runtime=None))
    code, value, _ = s.invoke(warning)
    assert code == 0 and value['warnings']
    assert value['items'][0]['value']['selection']['workload_class'] == 'unit'
    s.error(s.request(selection=s.selection('absent')), 'selection_missing')


def test_configuration_root_and_checkout_are_independent(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    other = tmp_path / 'registered-other'
    git(s.checkout, 'worktree', 'add', '-b', 'fixture-other', str(other))
    try:
        first = s.run(fresh=True)
        code, value, _ = s.invoke(s.request(fresh=True), checkout=other)
        assert code == 0
        second = value['items'][0]
        assert s.record(first)['cwd'] == str(s.checkout)
        assert s.record(second)['cwd'] == str(other)
        assert [v['cwd'] for v in s.markers()] == [str(s.checkout), str(other)]
        assert s.record(first)['run_id'] != s.record(second)['run_id']
        assert value['policy_sha256'] == digest(s.policy_path.read_bytes())
    finally:
        git(s.checkout, 'worktree', 'remove', str(other))


def test_foreign_project_and_checkout_rejected(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    git(foreign, 'init', '-b', 'main')
    sentinel = foreign / 'foreign.txt'
    sentinel.write_bytes(b'foreign material\n')
    code, result, _ = s.invoke(checkout=foreign)
    assert code == 2 and result['error']['code'] == 'project_binding'
    assert sentinel.read_bytes() == b'foreign material\n' and not s.markers()
    code, result, _ = s.invoke(config=s.root / 'missing-project.json')
    assert code == 2 and result['error']['code'] == 'invalid_project' and not s.markers()


def test_policy_known_fields_and_unknown_raw_values(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    sentinel = literal('contract.json')['independent_oracles']['unknown_configuration']['sentinel']
    s.policy['future'] = deepcopy(sentinel)
    s.profile()['future'] = deepcopy(sentinel)
    s.save()
    before = s.profiles_path.read_bytes()
    code, _, _ = s.invoke(s.request('describe'))
    assert code == 0 and s.profiles_path.read_bytes() == before
    cases = literal('contextual-error-cases.json')[:3]
    original = deepcopy(s.policy)
    for case in cases:
        s.policy = deepcopy(original)
        if case['case'] == 'missing-profile-path':
            del s.policy['profiles_path']
        else:
            s.policy['timing']['poll_seconds'] = case['input']['policy.timing.poll_seconds']
        s.save()
        actual = s.error(s.request(), 'invalid_policy')
        assert {k: actual[k] for k in case['expected_error_without_references']} == case['expected_error_without_references']
    assert json.loads(s.policy_path.read_text())['future'] == sentinel


def test_strict_request_and_atomic_batch_preflight(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    baseline = s.run()  # Existing real records/success history must survive every rejection.
    responses = []
    actual_invoke = s.invoke

    def observed_invoke(*args, **kwargs):
        result = actual_invoke(*args, **kwargs)
        responses.append(result[1])
        return result

    monkeypatch.setattr(s, 'invoke', observed_invoke)
    valid = s.request()
    broken = [dict(valid, unknown=True), dict(valid, operation='unknown'),
              dict(valid, items=[valid['items'][0], {'selection': s.selection('missing'),
                                                 'options': valid['items'][0]['options']}])]
    for value in broken:
        s.error(value, 'selection_missing' if value is broken[-1] else 'invalid_request')
        assert responses[-1]['operation'] == (None if value['operation'] == 'unknown' else 'run')
    for case in literal('admission-late-errors.json'):
        s.error(case['request'], case['error_code'], field=case['field'])
    s.error(valid, 'invalid_request', raw=b'{"schema":"one","schema":"two"}')
    assert responses[-1]['operation'] is None
    missing = {key: value for key, value in valid.items() if key != 'operation'}
    s.error(missing, 'invalid_request')
    assert responses[-1]['operation'] is None
    rejection = responses[-1]
    assert rejection['status'] == 'invalid' and rejection['error'] is not None and rejection['items'] == []
    response_schema = s.contract['schemas']['raw_response']
    for mutation in [{'status': 'passed'}, {'status': 'planned'}, {'status': 'ready'},
                     {'error': None}, {'items': [baseline]}]:
        with pytest.raises(AssertionError):
            schema({**rejection, **mutation}, response_schema)
    s.error(s.request(items=[valid['items'][0], deepcopy(valid['items'][0])]), 'invalid_request')
    oversized = s.request(items=[valid['items'][0]] * (project['cfg']['batch']['max_items'] + 1))
    s.error(oversized, 'invalid_request')
    s.policy['packages_path'] = None
    s.policy['suites_path'] = None
    s.save()
    s.error(s.request(selection=s.selection('base', kind='package')), 'packages_unavailable')
    s.error(s.request(selection={'kind': 'suite_set', 'profile': 'fast', 'changed_paths': None}), 'suites_unavailable')
    assert s.run(fresh=True)['status'] == 'passed'


def test_conditional_runtime_selection_and_preview(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    expected = literal('contract.json')['independent_oracles']['windows_preview']
    s.profile('windows', marker='windows', platforms=['windows'], runtime_class='windows-native',
              invocation='windows_cmd', argv=expected['profile_argv'])
    s.policy['runtime_adapters'].append({'id': 'windows-native', 'platforms': ['windows'],
                                       'invocation': 'windows_cmd', 'command_processor': expected['command_processor']})
    s.save()
    selected = s.selection('windows', runtime='windows-native')
    code, value, _ = s.invoke(s.request('preview', selected))
    assert code == 0 and value['items'][0]['steps'][0]['argv'] == expected['composed_argv']
    assert value['items'][0]['steps'][0]['available'] is False
    s.error(s.request(selection=selected), 'runtime_unavailable')
    s.profile('darwin', marker='darwin', platforms=['darwin'], runtime_class='darwin-native')
    s.policy['runtime_adapters'].append({'id': 'darwin-native', 'platforms': ['darwin'],
                                       'invocation': 'exec', 'command_processor': None})
    s.save()
    s.error(s.request(selection=s.selection('darwin', runtime='darwin-native')), 'runtime_unavailable')
    assert not s.markers()


def test_taskless_native_and_generated_callers(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    from .support import task_projection
    before = task_projection(project)
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    monkeypatch.delenv('CODEX_SESSION_ID', raising=False)
    monkeypatch.delenv('POISE_CALLER_BINDING', raising=False)
    binding = tmp_path / 'named-generated-caller.json'
    monkeypatch.setenv('POISE_CALLER_BINDING', str(binding))
    generated = s.run(fresh=True)
    assert binding.is_file()
    monkeypatch.delenv('POISE_CALLER_BINDING')
    monkeypatch.setenv('CODEX_THREAD_ID', 'f07-isolated-protocol-fixture')
    monkeypatch.setenv('CODEX_SESSION_ID', 'f07-isolated-protocol-fixture')
    native = s.run(fresh=True)
    assert generated['status'] == native['status'] == 'passed'
    assert task_projection(project) == before and len(s.markers()) == 2


def test_explicit_roots_and_source_binding(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    selected = s.run()
    assert s.record(selected)['cwd'] == str(s.checkout)
    assert not s.state.is_relative_to(s.checkout)
    before = s.policy_path.read_bytes()
    code, value, _ = s.invoke(policy=s.root / 'missing-policy.json')
    assert code == 3 and value['error']['code'] == 'unavailable_policy'
    assert s.policy_path.read_bytes() == before and len(s.markers()) == 1
    import poise.interfaces.project_checks as actual
    assert Path(actual.__file__).is_relative_to(ROOT / 'src')


def test_common_views_preserve_full_machine_result(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    s.child('--stdout', 'long output\n' * 1000, '--stderr', 'complete error stream\n')
    s.save()
    bodies = []
    for view in ['brief', 'expanded', 'full']:
        result = subprocess.run([sys.executable, '-B', '-m', 'poise', 'checks',
                                 '--config', str(s.config), '--checkout', str(s.checkout),
                                 '--policy', str(s.policy_path), '--view', view],
                                input=canonical(s.request(fresh=True)).encode(), cwd=ROOT,
                                env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')}, capture_output=True)
        assert result.returncode == 0 and not result.stderr
        displayed = json.loads(result.stdout)
        saved = Path(displayed['response_path'])
        full = json.loads(saved.read_text())
        schema(full, s.contract['schemas']['raw_response'])
        record = s.record(full['items'][0])
        assert s.outputs(record) == [b'long output\n' * 1000, b'complete error stream\n']
        assert len(result.stdout.decode()) <= project['cfg']['limits']['output_chars']
        if view == 'full':
            assert 'items' not in displayed
        bodies.append(result.stdout)
    assert len(set(bodies)) == 3


def test_functional_store_identity_and_corruption(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    s.run()
    ledger = s.state / 'named-runs.sqlite'
    assert ledger.exists()
    original = ledger.read_bytes()
    other_project, other_root = beta_project(project, tmp_path)
    beta = Scene(other_project, other_root, monkeypatch)
    beta.run()
    foreign = (beta.state / 'named-runs.sqlite').read_bytes()
    assert beta.state != s.state and foreign != original
    ledger.write_bytes(foreign)
    s.error(s.request(fresh=True), 'registry_invalid')
    assert ledger.read_bytes() == foreign and len(beta.markers()) == 1
    assert (beta.state / 'named-runs.sqlite').read_bytes() == foreign
    ledger.write_bytes(original)
    sentinel = b'not a database, preserve this original\n'
    ledger.write_bytes(sentinel)
    s.error(s.request(fresh=True), 'registry_invalid')
    assert ledger.read_bytes() == sentinel and len(s.markers()) == 1

    # Corrupt only test-owned rows originally produced by real successful children.
    # An authoritative row is different from an optional malformed cache payload.
    def corruptions():
        yield 'runs', 'json', None
        yield 'runs', 'selection', []
        yield 'runs', 'verified_success', 'true'
        yield 'runs', 'duration_seconds', '2'
        yield 'runs', 'clock', True
        yield 'results', 'json', None
        yield 'results', 'object', []
        yield 'results', 'step', '0'
        yield 'results', 'identity', 'different-result'
    from .support import task_projection
    for index, (table, field, replacement) in enumerate(corruptions()):
        arrangement = tmp_path / ('row-' + str(index))
        arrangement.mkdir()
        with monkeypatch.context() as patch:
            row_scene = Scene(project, arrangement, patch)
            row_scene.state = s.state.parent / ('corrupt-row-' + str(index))
            row_scene.policy['storage']['directory'] = str(row_scene.state)
            item = row_scene.run(fresh=True)
            record = row_scene.record(item)
            row_path = row_scene.state / 'named-runs.sqlite'
            original_value = record if table == 'runs' else item
            changed = deepcopy(original_value)
            if field == 'json':
                body = '{'
            else:
                if field == 'object':
                    changed = replacement
                elif field == 'clock':
                    changed['started']['monotonic_ns'] = replacement
                elif field == 'step':
                    changed['steps'][0]['record']['actual_exit_code'] = replacement
                elif field == 'identity':
                    changed['result_id'] = replacement
                else:
                    changed[field] = replacement
                body = canonical(changed)
            identifier = record['run_id'] if table == 'runs' else item['result_id']
            statement = ('UPDATE runs SET record_json=? WHERE run_id=?' if table == 'runs'
                         else 'UPDATE results SET result_json=? WHERE result_id=?')
            with sqlite3.connect(row_path) as connection:
                cursor = connection.execute(statement, (body, identifier))
                assert cursor.rowcount == 1
            bad_bytes = row_path.read_bytes()
            functional_before = row_scene.functional_state()
            retained = (Path(item['record_path']).read_bytes(),
                        Path(record['record_path']).read_bytes(), row_scene.outputs(record))
            tasks_before = task_projection(project)
            requests = [row_scene.request(fresh=True)] if table == 'runs' else [
                row_scene.request('result', items=[{'result_id': item['result_id']}]),
                row_scene.request()]  # A cache source must not hide authoritative corruption.
            for request in requests:
                code, response, diagnostic = row_scene.invoke(request)
                assert code == 2 and response['status'] == 'invalid', (table, field, response)
                error = response['error']
                assert error['code'] == 'registry_invalid' and response['items'] == []
                assert error['field'] == 'policy.storage.ledger_file'
                assert table in error['cause'] and identifier in error['cause']
                if field not in ('json', 'object'):
                    expected_field = ('started.monotonic_ns' if field == 'clock' else
                                      'steps[0].record.actual_exit_code' if field == 'step' else
                                      'result_id' if field == 'identity' else field)
                    assert expected_field in error['cause'] + error['reason']
                assert error['recovery'] and not diagnostic
                saved = json.loads(Path(response['response_path']).read_text())
                assert saved == response
                assert row_path.read_bytes() == bad_bytes
                assert row_scene.functional_state() == functional_before
                assert len(row_scene.markers()) == 1 and task_projection(project) == tasks_before
                assert (Path(item['record_path']).read_bytes(),
                        Path(record['record_path']).read_bytes(), row_scene.outputs(record)) == retained


def test_prerequisites_are_checked_truthfully(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    for kind, value in [('executables', 'missing-f07-tool'), ('files', 'missing-f07-file'),
                        ('environment', 'MISSING_F07_VALUE')]:
        s.profile()['prerequisites'] = {'executables': [], 'files': [], 'environment': []}
        s.profile()['prerequisites'][kind] = [value]
        s.save()
        error = s.error(s.request(), 'prerequisites_unavailable')
        assert error['field'].endswith('.' + kind + '[0]')
        assert value in error['cause'] + error['reason'] + error['recovery']
    assert not s.markers()

    fixture_child = Path(__file__).with_name('fixture_child.py')
    root_command = s.checkout / 'local-check'
    subdirectory = s.checkout / 'selected-cwd'
    subdirectory.mkdir()
    nested_command = subdirectory / 'local-check'
    script = ('#!' + sys.executable + '\nimport runpy\nrunpy.run_path('
              + repr(str(fixture_child)) + ", run_name='__main__')\n")
    for command in (root_command, nested_command):
        command.write_text(script)
        command.chmod(0o700)
    original_path = os.environ['PATH']
    cases = [('.', './local-check', original_path, root_command),
             ('selected-cwd', './local-check', original_path, nested_command),
             ('selected-cwd', str(nested_command), original_path, nested_command),
             ('selected-cwd', 'local-check', str(subdirectory) + os.pathsep + original_path, nested_command),
             ('selected-cwd', 'local-check', '.' + os.pathsep + original_path, nested_command),
             ('selected-cwd', 'local-check', os.pathsep + original_path, nested_command)]
    for index, (cwd, executable, path, expected_executable) in enumerate(cases):
        with monkeypatch.context() as patch:
            patch.setenv('PATH', path)
            profile = s.profile(cwd=cwd, argv=[executable, '--marker', 'cwd-' + str(index)])
            profile['prerequisites'] = {'executables': ['./local-check'], 'files': [], 'environment': []}
            s.save()
            code, preview, diagnostic = s.invoke(s.request('preview', fresh=True))
            assert code == 0 and not diagnostic
            step = preview['items'][0]['steps'][0]
            assert step['argv'] == profile['argv'] and step['cwd'] == str(expected_executable.parent)
            assert step['status'] == 'planned' and step['available'] is True
            assert Path.cwd() != expected_executable.parent
            control_root = s.events / ('control-' + str(index))
            control_root.mkdir()
            control = subprocess.run(profile['argv'], cwd=expected_executable.parent,
                                     env={**os.environ, 'F07_FIXTURE_EVENTS': str(control_root)},
                                     capture_output=True, timeout=5)
            assert control.returncode == 0 and control.stdout == b'work done\n' and not control.stderr
            control_marker = json.loads((control_root / 'markers.jsonl').read_text())
            assert control_marker['cwd'] == str(expected_executable.parent)
            assert len(s.markers()) == index
            item = s.run(fresh=True)
            record = s.record(item)
            assert record['actual_exit_code'] == 0 and record['verified_success']
            assert record['argv'] == profile['argv'] and record['cwd'] == str(expected_executable.parent)
            assert record['runtime']['executable'] == str(expected_executable)
            assert s.outputs(record) == [b'work done\n', b'']
            assert s.markers()[-1]['cwd'] == record['cwd']
            assert s.markers()[-1]['argv'] == ['--marker', 'cwd-' + str(index)]
            assert len(s.markers()) == index + 1
    with monkeypatch.context() as patch:
        patch.setenv('PATH', str(subdirectory) + os.pathsep + original_path)
        s.profile(cwd='selected-cwd', argv=['local-check', '--marker', 'must-not-start'])
        s.profile()['prerequisites']['executables'] = []
        s.policy['environment']['clear_keys'].append('PATH')
        s.save()
        code, preview, _ = s.invoke(s.request('preview'))
        step = preview['items'][0]['steps'][0]
        assert code == 0 and step['argv'] == ['local-check', '--marker', 'must-not-start']
        assert step['cwd'] == str(subdirectory) and step['status'] == 'planned'
        s.error(s.request(fresh=True), 'prerequisites_unavailable', field='request.items[0].argv[0]')
    s.policy['environment']['clear_keys'].remove('PATH')
    for executable in ['./absent-local-check', './nonexecutable-local-check']:
        (subdirectory / 'nonexecutable-local-check').write_text(script)
        (subdirectory / 'nonexecutable-local-check').chmod(0o600)
        s.profile(cwd='selected-cwd', argv=[executable, '--marker', 'must-not-start'])
        s.profile()['prerequisites']['executables'] = []
        s.save()
        s.error(s.request(fresh=True), 'prerequisites_unavailable', field='request.items[0].argv[0]')
    assert len(s.markers()) == len(cases)


def test_saved_result_readback_has_no_execution(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    original = s.run()
    code, response, _ = s.invoke(s.request('result', items=[{'result_id': original['result_id']}]))
    assert code == 0 and response['items'] == [original] and len(s.markers()) == 1
    assert json.loads(Path(original['record_path']).read_text()) == original
    expected = next(v for v in literal('contextual-error-cases.json') if v['case'] == 'missing-result')
    actual = s.error(s.request('result', items=[{'result_id': 'missing-result-1'}]), 'result_missing')
    assert {k: actual[k] for k in expected['expected_error_without_references']} == expected['expected_error_without_references']
    assert len(s.markers()) == 1


def test_python_port_and_cli_consumer_agree(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    request = s.request('preview')
    code, raw, _ = s.invoke(request)
    assert code == 0
    result = subprocess.run([sys.executable, '-B', '-m', 'poise', 'checks', '--config', str(s.config),
                             '--checkout', str(s.checkout), '--policy', str(s.policy_path), '--view', 'full'],
                            cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                            input=canonical(request).encode(), capture_output=True)
    assert result.returncode == 0 and not result.stderr
    saved = json.loads(Path(json.loads(result.stdout)['response_path']).read_text())
    schema(saved, s.contract['schemas']['raw_response'])
    assert {k: v for k, v in saved.items() if k != 'response_path'} == {
        k: v for k, v in raw.items() if k != 'response_path'}
    assert not s.markers()
