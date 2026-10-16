"""Public taskless instruction contract: independent bodies, scopes and failures."""
from copy import deepcopy
import io
import json
import sys

import pytest

from tests.skills.instruction_delivery_helpers import (
    Scenario, body, changed, decoded, fixture, snapshot, unavailable,
)
from tests.skills.test_development_routing import configured, service


@pytest.fixture
def alpha(project, tmp_path):
    return Scenario(project, tmp_path, 'alpha')


def test_taskless_prepare_delivers_selected_instructions(alpha):
    before = snapshot(alpha.root)
    result = alpha.assert_response(alpha.invoke(), fixture('billing-expected.json'))
    assert len(result['materials'][0]['content']) > 5000
    assert '\r\n' in result['materials'][-1]['content']
    assert result['materials'][0]['content'].endswith('END-ROOT-UNCLIPPED\n')
    assert snapshot(alpha.root) == before


def test_two_projects_use_their_own_policy_and_roots(alpha, project, tmp_path):
    beta = Scenario(project, tmp_path, 'beta')
    before = snapshot(tmp_path)
    first = alpha.assert_response(alpha.invoke(), fixture('billing-expected.json'))
    second = beta.assert_response(beta.invoke(), fixture('beta-expected.json'))
    assert first['materials'][0]['content'] != second['materials'][0]['content']
    assert snapshot(tmp_path) == before


def test_multi_area_selection_preserves_scope_and_deduplicates(alpha):
    alpha.profiles()
    assert not (alpha.checkout / '.agents/profiles/archive.md').exists()
    paths = ['src/billing/invoice.py', 'docs/guide.md', 'src/billing/invoice.py']
    first = alpha.assert_response(alpha.invoke(alpha.request(paths)),
                                  fixture('multi-profile-expected.json'),
                                  paths=['docs/guide.md', 'src/billing/invoice.py'])
    second = alpha.invoke(alpha.request(list(reversed(paths))))
    assert second == (0, first)


def test_refresh_reports_working_path_changes(alpha):
    initial = alpha.assert_response(alpha.invoke(), fixture('billing-expected.json'))
    current = alpha.assert_response(
        alpha.invoke(alpha.request(['docs/guide.md'], initial)), fixture('docs-expected.json'),
        paths=['docs/guide.md'], delta=fixture('working-path-delta.json'))
    empty = {'added': [], 'removed': [], 'changed': []}
    alpha.assert_response(alpha.invoke(alpha.request(['docs/guide.md'], current)),
                          fixture('docs-expected.json'), paths=['docs/guide.md'], delta=empty)


def test_refresh_reports_instruction_content_changes(alpha):
    initial = alpha.assert_response(alpha.invoke(), fixture('billing-expected.json'))
    alpha.write('.agents/skills/billing/SKILL.md', body('changed-billing'))
    materials = changed(fixture('billing-expected.json'), 'skill', 'billing', body('changed-billing'))
    current = alpha.assert_response(alpha.invoke(alpha.request(previous=initial)), materials,
                                    delta=fixture('single-material-deltas.json')['billing-content'])
    alpha.write('src/billing/AGENTS.md', body('changed-billing-rules'))
    materials = changed(materials, 'rule', 'src/billing/AGENTS.md', body('changed-billing-rules'))
    alpha.assert_response(alpha.invoke(alpha.request(previous=current)), materials,
                          delta=fixture('single-material-deltas.json')['billing-rules-content'])


def test_missing_selected_required_instruction_blocks_readiness(alpha):
    initial = alpha.assert_response(alpha.invoke(), fixture('billing-expected.json'))
    (alpha.checkout / '.agents/skills/billing/SKILL.md').unlink()
    alpha.assert_response(alpha.invoke(alpha.request(previous=initial)),
                          unavailable(fixture('billing-expected.json'), 'skill', 'billing'),
                          status='needs_inputs', diagnostics=fixture('missing-diagnostics.json')['billing'],
                          delta=fixture('single-material-deltas.json')['billing-missing'])


def test_missing_recommended_and_unselected_materials_do_not_block_ready(alpha):
    initial = alpha.assert_response(alpha.invoke(), fixture('billing-expected.json'))
    for name in ['audit', 'unused']:
        (alpha.checkout / ('.agents/skills/' + name + '/SKILL.md')).unlink()
    alpha.assert_response(alpha.invoke(alpha.request(previous=initial)),
                          unavailable(fixture('billing-expected.json'), 'skill', 'audit'),
                          diagnostics=fixture('missing-diagnostics.json')['audit'],
                          delta=fixture('single-material-deltas.json')['audit-missing'])
    code, old = alpha.invoke({'operation': 'list'})
    assert code == 2
    assert 'instruction file is missing' in old['reason']


def test_missing_selection_inputs_have_actionable_diagnostics(alpha, project, tmp_path):
    initial = alpha.assert_response(alpha.invoke(), fixture('billing-expected.json'))
    beta = Scenario(project, tmp_path, 'beta')
    invalid_policy = alpha.root / 'invalid-policy.json'
    invalid_policy.write_text('{"schema":"invalid"}', encoding='utf-8')
    cases = []
    request = alpha.request(); del request['policy']; cases.append(('missing-policy', request, None))
    for name, field, value in [
        ('null-policy', 'policy', None), ('empty-paths', 'paths', []),
        ('unreadable-policy', 'policy', str(alpha.root / 'missing-policy.json')),
        ('invalid-policy', 'policy', str(invalid_policy)),
        ('unreadable-manifest', 'project_config', str(alpha.root / 'missing-manifest.json')),
        ('foreign-checkout', 'checkout', str(beta.checkout)),
        ('unknown-field', 'unexpected', True),
    ]:
        request = alpha.request(); request[field] = value; cases.append((name, request, None))
    previous = deepcopy(initial); previous['project'] = 'beta'
    cases.append(('foreign-previous', alpha.request(previous=previous), None))
    cases.append(('invalid-json', alpha.request(), '{'))
    before = snapshot(tmp_path)
    for name, request, raw in cases:
        assert alpha.invoke(request, raw) == (2, fixture('errors.json')[name])
    assert snapshot(tmp_path) == before


def test_common_preparation_and_poise_selection_use_one_dispatch(alpha, configured, monkeypatch, capsys):
    from poise import __main__
    from poise.interfaces import skills
    calls = []
    original = skills.execute

    def observe(*args):
        calls.append('general-entry')
        return original(*args)

    monkeypatch.setattr(skills, 'execute', observe)
    monkeypatch.setattr(sys, 'argv', ['poise'] + alpha.argv()[4:])
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(json.dumps(alpha.request()).encode())))
    code = __main__.main()
    output = capsys.readouterr()
    alpha.assert_response(decoded(code, output.out, output.err), fixture('billing-expected.json'))
    paths, request, _ = configured
    router = service(paths)
    route = router.route

    def specialization(value):
        calls.append('poise-selection')
        return route(value)

    monkeypatch.setattr(router, 'route', specialization)
    result = router.route(request)
    assert calls == ['general-entry', 'poise-selection']
    assert result['checks']['required_methods'] == ['M']
    assert result['checks']['executed'] is False


def test_missing_selected_profile_instruction_blocks_readiness(alpha):
    alpha.profiles()
    initial = alpha.assert_response(alpha.invoke(), fixture('profile-billing-expected.json'))
    (alpha.checkout / '.agents/profiles/billing.md').unlink()
    alpha.assert_response(alpha.invoke(alpha.request(previous=initial)),
                          unavailable(fixture('profile-billing-expected.json'), 'profile', 'billing'),
                          status='needs_inputs', diagnostics=fixture('missing-diagnostics.json')['profile'],
                          delta=fixture('single-material-deltas.json')['profile-missing'])
