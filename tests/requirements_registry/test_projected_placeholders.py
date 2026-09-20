"""Explicit future placeholders preserve three-level traceability but prove nothing."""
import pytest

from poise.modules.foundation.errors import DomainError
from poise.modules.requirements_registry.domain import RequirementsRegistry


def registry(system_text='', application_text=''):
    return RequirementsRegistry.restore([
        {'id': 'S', 'level': 'system', 'status': 'future', 'text': system_text},
        {'id': 'A', 'level': 'application', 'status': 'future', 'text': application_text},
    ], [{'system': 'S', 'application': 'A'}])


def test_projected_chain_may_have_empty_placeholders_at_all_three_levels():
    r = registry()
    planned = r.plan_task([{'text': '', 'applications': ['A'], 'status': 'future'}])
    assert planned['status'] == 'gaps'
    assert planned['chains'] == [{'task_text': '',
        'applications': [{'id': 'A', 'level': 'application', 'status': 'future', 'text': ''}],
        'systems': [{'id': 'S', 'level': 'system', 'status': 'future', 'text': ''}]}]
    assert {gap['level'] for gap in planned['gaps']} == {'system', 'application', 'task'}
    assert planned['snapshot']['links'] == [{'system': 'S', 'application': 'A'}]


@pytest.mark.parametrize('level', ['system', 'application'])
def test_an_empty_current_requirement_is_not_accepted(level):
    with pytest.raises(DomainError, match='text'):
        RequirementsRegistry.restore([
            {'id': 'EMPTY', 'level': level, 'status': 'current', 'text': ''}], [])


def test_a_named_task_with_an_empty_ancestor_remains_a_gap():
    r = registry(system_text='The system supports verification.')
    plan = r.plan_task([{'text': 'Repair the test fixture.', 'applications': ['A']}])
    assert plan['status'] == 'gaps'
    assert plan['gaps'] == [{'kind': 'projected_placeholder', 'level': 'application',
                             'requirement_id': 'A', 'task_text': 'Repair the test fixture.'}]


def test_resolving_placeholders_keeps_ids_and_makes_the_chain_ready():
    r = registry()
    r = r.apply([
        {'kind': 'put_requirement', 'requirement': {
            'id': 'S', 'level': 'system', 'status': 'current', 'text': 'Verification is executable.'}},
        {'kind': 'put_requirement', 'requirement': {
            'id': 'A', 'level': 'application', 'status': 'current', 'text': 'Fixtures meet prerequisites.'}},
    ], 2)
    plan = r.plan_task([{'text': 'Repair the fixture.', 'applications': ['A']}])
    assert plan['status'] == 'ready'
    assert plan['gaps'] == []
    assert plan['snapshot']['links'] == [{'system': 'S', 'application': 'A'}]


def test_placeholder_is_visible_in_registry_gaps_even_when_linked():
    gaps = registry().gaps()
    assert {(gap['level'], gap['requirement_id']) for gap in gaps
            if gap['kind'] == 'projected_placeholder'} == {('system', 'S'), ('application', 'A')}


def test_empty_task_requires_explicit_future_status():
    with pytest.raises(DomainError, match='text'):
        registry('System', 'Application').plan_task([{'text': '', 'applications': ['A']}])
