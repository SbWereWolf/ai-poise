"""The transport consumes existing independent selection and scope contracts."""
from copy import deepcopy

import pytest

from tests.named_checks.support import git


def test_explicit_registered_copy_is_used(case, tmp_path):
    other = tmp_path / 'other-copy'
    git(case.scene.checkout, 'worktree', 'add', '-b', 'mcp-other-copy', str(other))
    try:
        peer = case.peer(); peer.initialize()
        binding = {'config': str(case.scene.config), 'checkout': str(other), 'policy': str(case.scene.policy_path)}
        peer.call('other', 'run', case.arguments(binding=binding))
        _, body = peer.outcome('other')
        assert body['result']['checkout'] == str(other)
        assert case.record(body)['cwd'] == case.started()['cwd'] == str(other)
    finally:
        git(case.scene.checkout, 'worktree', 'remove', str(other))


def test_selected_workload_runtime_and_warnings(case):
    profile = deepcopy(case.scene.profile())
    profile['workload_class'] = 'large'
    case.scene.profiles['commands'].append(profile)
    case.scene.save()
    peer = case.peer(); peer.initialize()
    args = case.arguments()
    args['selection']['workload_class'] = 'large'
    _, body = case.full_run(peer, arguments=args)
    assert case.record(body)['selection']['workload_class'] == 'large'
    assert case.record(body)['selection']['runtime_class'] == 'python'
    case.child(marker='warning', name='warning-only')
    args = case.arguments(name='warning-only')
    args['selection']['workload_class'] = 'unused-workload'
    _, warned = case.full_run(peer, 'warned', args)
    assert any('workload' in text for text in warned['result']['warnings'])
    assert case.record(warned)['argv'] == case.scene.profile('warning-only')['argv']


@pytest.mark.parametrize('mode', ['working', 'committed'])
def test_documentation_scope_reaches_real_child(case, mode):
    from tests.named_checks.test_packages import doc_candidate
    declaration = doc_candidate(case.scene)
    subject_argv = list(case.scene.profile()['argv'])
    case.scene.documentation(mode=mode)
    case.scene.profile()['argv'] = [*subject_argv, '--subject-mode', mode]
    case.scene.save()
    peer = case.peer(); peer.initialize()
    args = (case.arguments(documentation_scope=declaration) if mode == 'committed'
            else case.arguments(scope_paths=['docs/required.md']))
    _, body = case.full_run(peer, arguments=args)
    import json
    if mode == 'committed':
        assert json.loads(case.started()['scope']) == declaration
        assert case.record(body)['subject']['declaration'] == declaration
    else:
        assert case.started()['scope'] is None
        assert case.record(body)['subject']['paths'] == ['docs/required.md']
        assert case.record(body)['subject']['declaration'] is None


@pytest.mark.parametrize('scope', [None, 'not-an-object'])
def test_bad_required_documentation_scope_has_no_process(case, scope):
    subject_argv = list(case.scene.profile()['argv'])
    case.scene.documentation()
    case.scene.profile()['argv'] = [*subject_argv, '--subject-mode', 'committed']
    case.scene.save()
    peer = case.peer(); peer.initialize()
    peer.call('bad-scope', 'run', case.arguments(documentation_scope=scope))
    envelope, body = peer.outcome('bad-scope')
    assert envelope['isError'] is True
    assert body['result']['status'] == 'invalid'
    assert body['result']['error']['code'] == 'scope_shape_invalid'
    assert not case.rows()


def test_package_steps_use_existing_dependency_order(case):
    from tests.named_checks.test_packages import diamond
    diamond(case.scene)
    peer = case.peer(); peer.initialize()
    args = case.arguments()
    args['selection'] = {'kind': 'package', 'id': 'consumer'}
    peer.call('package', 'run', args)
    _, body = peer.outcome('package')
    steps = body['result']['items'][0]['steps']
    assert [step['package_id'] for step in steps] == ['base', 'left', 'right', 'consumer']
    assert [row['marker'] for row in case.scene.markers()][-4:] == ['base:1', 'left:1', 'right:1', 'consumer:1']


def test_cache_hit_and_explicit_fresh_keep_truth(case):
    peer = case.peer(); peer.initialize()
    _, first = case.full_run(peer, 'first', case.arguments(fresh=False))
    _, reused = case.full_run(peer, 'reused', case.arguments(fresh=False))
    assert reused['result']['items'][0]['kind'] == 'reuse'
    assert reused['result']['items'][0]['source']['result_id'] == first['result']['items'][0]['result_id']
    assert len([row for row in case.rows() if row['event'] == 'STARTED']) == 1
    _, fresh = case.full_run(peer, 'fresh', case.arguments(fresh=True))
    assert fresh['result']['items'][0]['kind'] == 'execution'
    assert len([row for row in case.rows() if row['event'] == 'STARTED']) == 2


def test_actual_timeout_evidence_crosses_transport_and_is_consumed_once(case):
    case.child(marker='timeout', mode='hold')
    case.scene.profile(execution_timeout_seconds=0.3)
    case.scene.save()
    peer = case.peer(); peer.initialize()
    _, timed_out = case.full_run(peer, 'timeout')
    original = case.record(timed_out)
    assert original['status'] == 'timed_out'
    case.child(marker='diagnostic')
    case.scene.profile(execution_timeout_seconds=0.3)
    case.scene.save()
    other = case.peer('diagnostic'); other.initialize()
    from .support import literal
    override = literal('override.json')
    override['evidence_run_id'] = original['run_id']
    _, completed = case.full_run(other, 'override', case.arguments(timeout_override=override))
    record = case.record(completed)
    assert record['timeout']['effective_seconds'] == 2
    assert record['timeout']['evidence_run_id'] == original['run_id']
    assert record['timeout']['basis'] == 'diagnostic_override'
    other.call('used', 'run', case.arguments(timeout_override=override))
    envelope, used = other.outcome('used')
    assert envelope['isError'] is True
    assert used['result']['error']['code'] == 'override_consumed'
    assert len([row for row in case.rows('diagnostic') if row['event'] == 'STARTED']) == 1


def test_unavailable_runtime_has_preview_and_truthful_run_continuation(case):
    missing = case.root / 'missing-executable'
    profile = case.scene.profile()
    profile['argv'][0] = str(missing)
    case.scene.save()
    peer = case.peer(); peer.initialize()
    _, preview = case.full_run(peer, 'preview', case.arguments(preview=True))
    assert preview['result']['status'] == 'planned'
    # Existing F07 availability describes the selected platform/adapter;
    # executable admission remains the actual run prerequisite owner.
    assert preview['result']['items'][0]['steps'][0]['available'] is True
    assert preview['result']['items'][0]['steps'][0]['argv'][0] == str(missing)
    envelope, result = case.full_run(peer, 'unavailable')
    assert envelope['isError'] is True and result['result']['status'] == 'unavailable'
    assert result['result']['error']['code'] == 'prerequisites_unavailable'
    assert result['result']['error']['recovery']
    assert not case.rows()


def test_invalid_selected_manifest_has_no_execution(case):
    peer = case.peer(); peer.initialize()
    binding = {'config': str(case.root / 'missing-profile.json'),
               'checkout': str(case.scene.checkout), 'policy': str(case.scene.policy_path)}
    peer.call('bad-binding', 'run', case.arguments(binding=binding))
    envelope, result = peer.outcome('bad-binding')
    assert envelope['isError'] is True
    assert result['result']['error']['code'] == 'invalid_project'
    assert result['result']['error']['recovery']
    assert not case.rows()
