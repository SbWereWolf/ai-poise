"""The persisted optional source obeys the same public reporting scope as work."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from conftest import DeterministicClock, write_json
from poise.runtime import Poise
from tests.batch.helpers import request
from tests.batch.test_cli import call
from .test_domain import policy


def observations():
    return json.loads((Path(__file__).parent / 'fixtures/telemetry_scope.json').read_text())


def seed(project, records):
    project['cfg']['schema'] = 'ddd-accounting-12'
    project['cfg']['accounting'] = policy()
    write_json(project['config_path'], project['cfg'])
    runtime = Poise(project['config_path'], 'scope-fixture', clock=DeterministicClock())
    runtime.telemetry.dispatcher.close()
    # Persist independent test data; do not ask the producer to generate the oracle.
    db = runtime.accounting.port.telemetry_repo.database
    with db.transaction() as connection:
        for record in records:
            connection.execute(
                'INSERT INTO accounting_cycles(id,task_id,project,session_id,started_at,ended_at,data) '
                'VALUES(?,?,?,?,?,?,?)',
                (record['id'], None, project['cfg']['project'], record['session'],
                 record['started']['audit_utc'], record['finished']['audit_utc'],
                 json.dumps({'kind': 'telemetry_envelope', 'envelope': record})))
    return runtime


def report(project, runtime, kind, scope_id, *, cli, start=None, end=None):
    query = {'scope': {'kind': kind, 'id': scope_id}, 'group_by': ['task'],
             'from': start, 'to': end}
    if not cli:
        return runtime.accounting.port.report(query)
    result = call(project, request('show', {'queries': [
        {'id': 'scoped-report', 'kind': 'accounting', **query}]}))
    assert result.returncode == 0, result.stdout + result.stderr
    view = json.loads(result.stdout)
    full = json.loads(Path(view['response_path']).read_text()) if 'response_path' in view else view
    return full['results'][0]['value']


@pytest.mark.parametrize('cli', [False, True], ids=['projection', 'public-cli'])
@pytest.mark.parametrize('kind,scope_id,seconds,groups', [
    ('task', 'task-A', 5, ['task-A']),
    ('sprint', 'sprint-A', 5, ['task-A']),
    ('task', 'task-B', 30, ['task-B']),
    ('project', 'fixture-project', 35, ['task-A', 'task-B']),
    ('project', 'other-project', None, []),
    ('all', None, 35, ['task-A', 'task-B']),
])
def test_scope_filters_optional_time_groups_and_costs(project, cli, kind, scope_id, seconds, groups):
    # Explicit project identity is part of this fixture, not derived expected output.
    project['cfg']['project'] = 'fixture-project'
    rows = observations()
    runtime = seed(project, rows)
    result = report(project, runtime, kind, scope_id, cli=cli)
    assert result['totals']['active_seconds'] == seconds
    assert [group['dimensions']['task'] for group in result['groups']] == groups
    if groups == ['task-A'] or not groups:
        assert result['totals']['remediation_groups'] == []
        assert 'delivered_rework' not in result['totals']['by_cause']
    else:
        assert result['totals']['remediation_groups'] == [
            {'task': 'task-B', 'finding_ids': ['foreign-finding'],
             'model_tokens': 0, 'active_seconds': 30}]
    with runtime.accounting.port.telemetry_repo.database.read_transaction() as db:
        persisted = db.execute(
            'SELECT id,data FROM accounting_cycles WHERE id IN (?,?) ORDER BY id',
            ('selected-operation', 'foreign-operation')).fetchall()
    assert [json.loads(row['data'])['envelope'] for row in persisted] == [rows[1], rows[0]]


@pytest.mark.parametrize('kind,scope_id,coverage', [
    ('task', 'task-A', 'unavailable'),
    ('sprint', 'sprint-A', 'unavailable'),
    ('project', 'other-project', 'unavailable'),
    ('task', 'task-B', 'partial'),
    ('sprint', 'sprint-B', 'partial'),
    ('project', 'fixture-project', 'partial'),
    ('all', None, 'partial'),
])
def test_foreign_incomparable_clock_does_not_poison_scope_coverage(project, kind, scope_id, coverage):
    project['cfg']['project'] = 'fixture-project'
    foreign = observations()[1]
    foreign['finished']['comparison_domain'] = 'different-boot'
    runtime = seed(project, [foreign])
    result = report(project, runtime, kind, scope_id, cli=True)
    assert result['totals']['active_seconds'] is None
    assert result['totals']['time_coverage'] == coverage
    assert result['groups'] == []


@pytest.mark.parametrize('empty_after', [False, True], ids=['read-only-acquisition', 'no-task'])
def test_taskless_read_only_telemetry_does_not_poison_coverage(project, empty_after):
    row = observations()[0]
    row['before_binding'] = deepcopy(observations()[1]['after_binding'])
    row['result_status'] = 'read_only'
    if empty_after:
        row['after_binding'] = deepcopy(row['before_binding'])
    row['finished']['comparison_domain'] = 'different-boot'
    runtime = seed(project, [row])
    result = report(project, runtime, 'all', None, cli=True)
    assert result['totals']['active_seconds'] is None
    assert result['totals']['time_coverage'] == 'unavailable'
    assert result['groups'] == []


def test_selected_telemetry_keeps_half_open_clipping(project):
    runtime = seed(project, observations())
    result = report(project, runtime, 'task', 'task-A', cli=True,
                    start='2026-09-11T10:00:02+00:00', end='2026-09-11T10:00:04+00:00')
    assert result['totals']['active_seconds'] == 2
    assert [group['dimensions']['task'] for group in result['groups']] == ['task-A']
    outside = report(project, runtime, 'task', 'task-A', cli=True,
                     start='2026-09-11T10:00:05+00:00', end='2026-09-11T10:00:06+00:00')
    assert outside['totals']['active_seconds'] is None
    assert outside['groups'] == []
