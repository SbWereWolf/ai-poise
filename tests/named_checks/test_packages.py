"""Package ownership/order and actual configured documentation/suite subjects."""
from copy import deepcopy
import json
import threading
import time

from .support import Scene, beta_project, digest, git, literal, snapshot, task_projection


def diamond(s):
    base = deepcopy(s.packages['packages'][0])
    s.packages['packages'] = []
    s.profiles['commands'] = [v for v in s.profiles['commands'] if v['command_id'] != 'test-package']
    for name, dependencies in [('base', []), ('left', ['base']), ('right', ['base']),
                               ('consumer', ['left', 'right'])]:
        package = deepcopy(base)
        package['package_id'] = name
        package['dependencies'] = dependencies
        package['steps'][0]['test_paths'] = ['tests/' + name + '.py']
        package['steps'][0]['arguments'] = ['--marker', name + ':1']
        s.packages['packages'].append(package)
        path = s.checkout / 'tests' / (name + '.py')
        path.write_text('# independent ' + name + ' test input\n')
        profile = deepcopy(s.profile())
        profile['command_id'] = 'test-package'
        profile['workload_class'] = name
        profile['argv'] = deepcopy(s.policy['drivers'][0]['argv_prefix']) + ['--marker', name + ':1']
        profile['cache']['input_paths'] = ['src/core', 'tests/' + name + '.py']
        s.profiles['commands'].append(profile)
    git(s.checkout, 'add', 'tests')
    git(s.checkout, 'commit', '-m', 'Declare diamond package input membership')
    s.save()


def doc_candidate(s):
    docs = s.checkout / 'docs'
    docs.mkdir(exist_ok=True)
    path = docs / 'required.md'
    path.write_text('# required\n')
    git(s.checkout, 'add', 'docs')
    git(s.checkout, 'commit', '-m', 'Declare independent documentation candidate')
    return s.declaration(['docs/required.md'])


def test_package_dependency_order_and_deduplication(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    diamond(s)
    selection = s.selection('consumer', kind='package')
    code, preview, _ = s.invoke(s.request('preview', selection))
    assert code == 0 and not s.markers()
    planned = preview['items'][0]
    assert planned['resolved_dependencies'] == ['base', 'left', 'right']
    assert [v['argv'][v['argv'].index('--marker') + 1] for v in planned['steps']] == [
        'base:1', 'left:1', 'right:1', 'consumer:1']
    actual = s.run(selection=selection, fresh=True)
    assert [v['marker'] for v in s.markers()] == ['base:1', 'left:1', 'right:1', 'consumer:1']
    assert [v['package_id'] for v in actual['steps']] == ['base', 'left', 'right', 'consumer']
    assert len({v['record']['run_id'] for v in actual['steps']}) == 4
    assert all(v['record']['actual_exit_code'] == 0 for v in actual['steps'])


def test_package_inventory_and_shared_test_contract(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    diamond(s)
    original = deepcopy(s.packages)
    request = s.request('preview', s.selection('consumer', kind='package'))
    assert s.invoke(request)[0] == 0
    (s.checkout / 'tests/orphan.py').write_text('# orphan must be diagnosed\n')
    s.error(request, 'invalid_packages')
    (s.checkout / 'tests/orphan.py').unlink()
    for fault in ['duplicate', 'cycle', 'missing_dependency', 'missing_driver']:
        s.packages = deepcopy(original)
        if fault == 'duplicate':
            s.packages['packages'][1]['steps'][0]['test_paths'].append('tests/base.py')
        elif fault == 'cycle':
            s.packages['packages'][0]['dependencies'] = ['consumer']
        elif fault == 'missing_dependency':
            s.packages['packages'][1]['dependencies'] = ['missing']
        else:
            s.packages['packages'][0]['steps'][0]['driver'] = 'missing'
        s.save()
        s.error(request, 'invalid_packages')
    s.packages = deepcopy(original)
    s.packages['packages'][1]['steps'][0]['test_paths'].append('tests/base.py')
    s.packages['shared_tests'] = [{'path': 'tests/base.py', 'packages': ['base', 'left'],
                                 'reason': 'Both explicit owners exercise this contract.'}]
    s.save()
    assert s.invoke(request)[0] == 0 and not s.markers()
    s.packages['shared_tests'][0]['packages'] = ['base']
    s.save()
    s.error(request, 'invalid_packages')


def test_generic_native_driver_arguments(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    diamond(s)
    package = s.packages['packages'][0]
    for family in ['composer-program', 'phpunit-program', 'npm-program', 'optimizer-program']:
        s.policy['drivers'] = [{**deepcopy(s.policy['drivers'][0]), 'id': family}]
        package['steps'][0]['driver'] = family
        package['steps'][0]['arguments'] = ['--marker', family, '--stdout', 'native fixture\n']
        result = s.run(selection=s.selection('base', kind='package'), fresh=True)
        assert result['steps'][0]['driver'] == family
        assert s.markers()[-1]['remaining'] == ['tests/base.py']
        assert s.markers()[-1]['marker'] == family
        assert s.record(result)['argv'] == s.policy['drivers'][0]['argv_prefix'] + package['steps'][0]['arguments'] + ['tests/base.py']
    assert len(s.markers()) == 4  # Real fixture programs, not PHP/npm/Composer execution proof.


def test_change_scope_and_integration_boundaries(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    s.packages = literal('package-impact-catalogue.json')
    expected = {v['package_id']: v for v in literal('package-impact-expected-declarations.json')}
    s.save()
    from poise.execution import RegisteredCheckRunner

    def forbidden_execution(*args, **kwargs):
        raise AssertionError('Read-only package impact must never invoke a process owner')
    monkeypatch.setattr(RegisteredCheckRunner, 'run', forbidden_execution)
    before = (task_projection(project), s.profiles_path.read_bytes(), snapshot(s.state / 'cache'))
    for case in literal('package-impact-cases.json'):
        if case.get('arrangement'):
            deleted = s.checkout / 'src/core/deleted.py'
            deleted.write_text('# real previously present input\n')
            git(s.checkout, 'add', 'src/core/deleted.py')
            git(s.checkout, 'commit', '-m', 'Arrange deleted package impact input')
            deleted.unlink()
        request = s.request('package_impact', items=[{'changed_paths': case['changed_paths']}])
        code, response, _ = s.invoke(request)
        assert code == 0 and response['status'] == 'ready'
        item = response['items'][0]
        assert item['kind'] == 'package_impact' and item['changed_paths'] == case['changed_paths']
        assert [v['package']['package_id'] for v in item['packages']] == case['ids']
        assert item['unmapped_paths'] == case.get('unmapped_paths', [])
        for selected in item['packages']:
            package_id = selected['package']['package_id']
            assert selected['package'] == expected[package_id]
            assert selected['matches'] == case['matches'][package_id]
        assert not s.markers() and not (s.state / 'named-runs.sqlite').exists()
        assert (task_projection(project), s.profiles_path.read_bytes(), snapshot(s.state / 'cache')) == before
        if case.get('arrangement'):
            assert not deleted.exists()
    original = deepcopy(s.packages)
    for case in literal('package-impact-errors.json'):
        s.packages = deepcopy(original)
        if 'configuration_mutation' in case:
            del s.packages['packages'][0]['source_paths']
            changed = []
        else:
            changed = case['request_changed_paths']
        s.save()
        error = s.error(s.request('package_impact', items=[{'changed_paths': changed}]),
                        case['code'], field=case['field'])
        assert error['cause'] == case['cause'] and error['recovery'] == case['recovery']
        assert case['field'] in error['reason'] or changed and changed[0] in error['reason']
    assert not s.markers()


def test_suite_profile_and_order_no_full_fallback(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    for case in literal('suite-selection-cases.json'):
        (s.events / 'markers.jsonl').unlink(missing_ok=True)
        if case.get('core-1_exit'):
            s.profile('core-1', marker='core-1:1')['argv'] += ['--exit', str(case['core-1_exit'])]
        s.save()
        selection = case['selection']
        for operation in ['describe', 'preview']:
            code, value, _ = s.invoke(s.request(operation, selection, fresh=True))
            assert code == 0 and not s.markers()
            if selection['kind'] == 'suite_set':
                wrapper = value['items'][0]
                assert wrapper['suite_ids'] == case['suite_ids'] and wrapper['reason'] == case['reason']
                if 'source_matches' in case:
                    assert wrapper['source_matches'] == case['source_matches']
                if 'unmatched_paths' in case:
                    assert wrapper['unmatched_paths'] == case['unmatched_paths']
        code, value, _ = s.invoke(s.request(selection=selection, fresh=True))
        assert code == (1 if case.get('status') == 'failed' else 0)
        assert [v['marker'] for v in s.markers()] == case['markers']
        if selection['kind'] == 'suite_set':
            wrapper = value['items'][0]
            assert wrapper['suite_ids'] == case['suite_ids']
            if not case['suite_ids']:
                assert wrapper['status'] == 'ready' and wrapper['items'] == []
            if 'not_executed_suites' in case:
                assert [v['selection']['id'] for v in wrapper['items'] if v['kind'] == 'not_executed'] == case['not_executed_suites']
    for case_name in ['unknown-suite-profile', 'bad-scope-path']:
        case = next(v for v in literal('contextual-error-cases.json') if v['case'] == case_name)
        if case_name == 'unknown-suite-profile':
            selection = case['input']['request.items[0].selection']
        else:
            selection = {'kind': 'suite_set', 'profile': 'fast', 'changed_paths': ['../outside.py']}
        expected = case['expected_error_without_references']
        actual = s.error(s.request(selection=selection), expected['code'])
        assert {k: actual[k] for k in expected} == expected


def test_working_documentation_scope(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    declaration = doc_candidate(s)
    s.documentation('working')
    (s.checkout / 'docs/required.md').write_text('# current working edit\n')
    result = s.run(scope_paths=['docs/required.md'], fresh=True)
    subject = s.record(result)['subject']
    assert subject['mode'] == 'working' and subject['declaration'] is None
    assert subject['paths'] == ['docs/required.md']
    s.save()
    s.error(s.request(documentation_scope=declaration), 'scope_mode_conflict')
    s.error(s.request(documentation_scope=None), 'scope_shape_invalid')


def test_committed_documentation_scope_exact_identity(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    declaration = doc_candidate(s)
    s.documentation()
    result = s.run(documentation_scope=declaration, fresh=True)
    subject = s.record(result)['subject']
    assert subject['mode'] == 'committed' and subject['declaration'] == declaration
    assert subject['state'] == 'unchanged'
    assert json.loads(s.markers()[0]['scope']) == declaration
    assert declaration['candidate_commit'] == git(s.checkout, 'rev-parse', 'HEAD')
    assert not result['cache']['eligible']


def test_invalid_subject_schema_and_admission_effects(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    declaration = doc_candidate(s)
    s.documentation()
    s.save()
    s.error(s.request(), 'scope_required')
    for changes, code in [({'version': 2}, 'scope_version_unsupported'),
                          ({'repository_root': str(tmp_path)}, 'scope_root_mismatch'),
                          ({'candidate_commit': 'no-commit'}, 'scope_git_identity_invalid'),
                          ({'candidate_tree': '0' * 40}, 'scope_candidate_mismatch'),
                          ({'paths': ['../outside.md']}, 'scope_path_invalid'),
                          ({'paths': []}, 'scope_targets_invalid'),
                          ({'extra': True}, 'scope_shape_invalid')]:
        bad = {**declaration, **changes}
        s.error(s.request(documentation_scope=bad), code)
    s.error(s.request(documentation_scope=declaration, scope_paths=['docs/required.md']), 'scope_mode_conflict')
    (s.checkout / 'docs/required.md').write_text('# dirty candidate\n')
    s.error(s.request(documentation_scope=declaration), 'scope_subject_dirty')
    assert not s.markers()


def test_subject_change_after_admission_is_unconfirmed(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    declaration = doc_candidate(s)
    profile = s.documentation()
    profile['argv'].append('--barrier')
    changed = []

    def mutate():
        deadline = time.monotonic() + 5
        while not (s.events / 'started').exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        (s.checkout / 'docs/required.md').write_text('# changed after admission\n')
        changed.append(True)
        (s.events / 'continue').write_text('continue\n')
    worker = threading.Thread(target=mutate)
    worker.start()
    try:
        item = s.run(documentation_scope=declaration, fresh=True, status='unconfirmed')
    finally:
        worker.join(6)
    assert not worker.is_alive() and changed == [True]
    record = s.record(item)
    assert record['actual_exit_code'] == 0 and record['error']['code'] == 'scope_subject_changed'
    assert not record['verified_success'] and not item['cache']['stored']


def test_deleted_and_renamed_subject_targets(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    docs = s.checkout / 'docs'
    docs.mkdir()
    (docs / 'old.md').write_text('# old\n')
    (docs / 'gone.md').write_text('# gone\n')
    (docs / 'required.md').write_text('# required\n')
    git(s.checkout, 'add', 'docs')
    git(s.checkout, 'commit', '-m', 'Initial documentation scope')
    git(s.checkout, 'mv', 'docs/old.md', 'docs/new.md')
    git(s.checkout, 'rm', 'docs/gone.md')
    (docs / 'required.md').write_text('# required updated\n')
    git(s.checkout, 'add', 'docs')
    git(s.checkout, 'commit', '-m', 'Rename delete and change documentation')
    paths = ['docs/gone.md', 'docs/new.md', 'docs/old.md', 'docs/required.md']
    declaration = s.declaration(paths, ['docs/required.md'])
    s.documentation()
    record = s.record(s.run(documentation_scope=declaration, fresh=True))
    assert record['subject']['paths'] == paths and record['subject']['declaration'] == declaration
    bad = {**declaration, 'paths': ['docs/new.md', 'docs/required.md']}
    s.save()
    s.error(s.request(documentation_scope=bad), 'scope_targets_invalid')


def test_both_documentation_modes_disable_cache(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    declaration = doc_candidate(s)
    for mode in ['working', 'committed']:
        s.documentation(mode)
        options = {} if mode == 'working' else {'documentation_scope': declaration}
        first = s.run(**options)
        second = s.run(**options)
        assert first['kind'] == second['kind'] == 'execution'
        assert first['result_id'] != second['result_id']
        s.profile()['cache']['enabled'] = True
        s.save()
        s.error(s.request(**options), 'invalid_profiles')
    assert len(s.markers()) == 4


def test_subject_capability_is_independent_of_erp_command_id(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    declaration = doc_candidate(s)
    for name in ['arbitrary-doc-one', 'arbitrary-doc-two']:
        s.documentation(name=name)
        record = s.record(s.run(selection=s.selection(name), documentation_scope=declaration))
        assert record['subject']['declaration'] == declaration
    s.profile('repository-documentation-contracts', marker='plain-command')
    s.save()
    s.error(s.request(selection=s.selection('repository-documentation-contracts'), documentation_scope=declaration),
            'scope_mode_conflict')
    assert len(s.markers()) == 2


def test_task_state_and_specialized_checks_are_preserved(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    before = task_projection(project)
    item = s.run()
    assert item['status'] == 'passed' and task_projection(project) == before
    from poise.infrastructure.test_package_cache import AiPoiseTestPackageCache
    from poise.infrastructure.test_packages import AiPoiseTestPackages
    from poise.modules.verification.domain import CheckRegistry
    assert AiPoiseTestPackageCache and AiPoiseTestPackages and CheckRegistry
    # Actual behavior of these owners is checked separately by the accepted GREEN guard.


def test_run_history_and_calibration_project_isolation(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    original = s.run()
    base, beta_root = beta_project(project, tmp_path)
    beta = Scene(base, beta_root, monkeypatch)
    beta.run()
    before = snapshot(s.state)
    beta.error(beta.request('result', items=[{'result_id': original['result_id']}]), 'result_missing')
    record = s.record(original)
    beta.error(beta.request('calibrate', items=[{'profile': beta.selection(), 'evidence_run_ids': [record['run_id']],
                                               'expected_profile_sha256': digest(beta.profiles_path.read_bytes())}]),
               'invalid_history')
    assert snapshot(s.state) == before
