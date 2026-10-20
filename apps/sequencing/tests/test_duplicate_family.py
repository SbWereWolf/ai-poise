"""Owned family registration through the external CLI and independent literal views."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from apps.sequencing.tests.test_registration import Client, current_state


DATA = json.loads((Path(__file__).resolve().parents[3] /
                   'apps/sequencing/fixtures/duplicate-family-contract.json').read_text())
P, D, E, U, F, A, B, Z, T = [DATA['ids'][name] for name in ('P', 'D', 'E', 'U', 'F', 'A', 'B', 'Z', 'T')]


def render(value, tokens):
    """Substitute explicitly supplied fixture values, without computing business answers."""
    if isinstance(value, str):
        return deepcopy(tokens.get(value, value))
    if isinstance(value, list):
        return [render(item, tokens) for item in value]
    if isinstance(value, dict):
        return {tokens.get(key, key): render(item, tokens) for key, item in value.items()}
    return value


class Family:
    def __init__(self, root, origin='independent', patterns=None):
        self.client = Client(root).seed('anchor')
        self.root = root
        self.db = self.client.database
        self.revision = 0
        self.serial = 0
        self.origin = origin
        if patterns:
            config = json.loads(self.client.config.read_text())
            config['identifiers'] = patterns
            self.client.config.write_text(json.dumps(config, ensure_ascii=False))
        self.config = self.client.config.read_bytes()
        self.marker = root / 'foreign-wip.txt'
        self.marker.write_text('Retained unrelated work.\n')
        operations = [render(DATA['templates']['register_operation'],
                      {'$TASK': task, '$REGISTER_ID': 'register-' + task}) for task in (P, U, F, A, B, Z, T)]
        expected = [render(DATA['templates']['register_result'],
                    {'$TASK': task, '$REGISTER_ID': 'register-' + task}) for task in (P, U, F, A, B, Z, T)]
        members = [A, U, B] + ([P] if origin == 'same' else [])
        definitions = [('destination', 'S-DST', members), ('alternative', 'S-ALT', [T])]
        if origin == 'other':
            definitions.append(('source', 'S-SRC', [P, Z]))
        for op, sprint, members in definitions:
            tokens = {'$OP': op, '$SPRINT': sprint, '$MEMBERS': members}
            operations.append(render(DATA['templates']['sprint_operation'], tokens))
            expected.append(render(DATA['templates']['sprint_result'], tokens))
        self.apply(operations, expected)

    def snapshot(self):
        return {'database': current_state(self.db),
                'files': {str(p.relative_to(self.root)): p.read_bytes()
                          for p in self.root.rglob('*') if p.is_file() and p != self.db},
                'directories': sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*') if p.is_dir())}

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.db)) as conn:
            result = conn.execute(statement, parameters).fetchall()
            conn.commit()
            return result

    def packet(self, operations):
        self.serial += 1
        return {'schema': DATA['schema'], 'operation': 'apply',
                'input': {'request_id': 'family-request-' + str(self.serial),
                          'expected_revision': self.revision, 'operations': operations}}

    def apply(self, operations, expected):
        packet = self.packet(operations)
        response = render(DATA['apply_response'], {'$REV': self.revision + 1, '$RESULTS': expected})
        assert self.client.invoke(packet) == response
        self.revision += 1
        return packet, response

    def query(self, queries, expected):
        before = self.snapshot()
        packet = {'schema': DATA['schema'], 'operation': 'query', 'input': {'queries': queries}}
        response = render(DATA['query_response'], {'$REV': self.revision, '$RESULTS': expected})
        assert self.client.invoke(packet) == response
        assert self.snapshot() == before

    def reject(self, packet, reason):
        before = self.snapshot()
        assert self.client.invoke(packet, 2) == render(DATA['error_response'], {'$REASON': reason})
        assert self.snapshot() == before

    def duplicate(self, task=D, parent=P, field='requirements_statement_revision', reference=7,
                  sprint_revision=2, claimant='CALLER-A', changed=True, op_id='duplicate'):
        operation = deepcopy(DATA['base_operation'])
        operation.pop('requirements_statement_revision')
        operation.update(id=op_id, task_id=task, parent_id=parent, claimant=claimant)
        operation[field] = reference
        result = render(DATA['duplicate_result'], {'$OP': operation['id'], '$CHANGED': changed,
                        '$TASK': task, '$SPRINT_REV': sprint_revision})
        before = self.snapshot()
        packet, response = self.apply([operation], [result])
        self.registration_delta(before, task, field, reference, sprint_revision, claimant, changed)
        return packet, response

    def registration_delta(self, before, task, field, reference, sprint_revision, claimant, changed):
        after = self.snapshot()
        assert before['files'] == after['files'] and before['directories'] == after['directories']
        assert self.client.config.read_bytes() == self.config
        old, new = before['database'], after['database']
        assert old['schema'] == new['schema'] and old['version'] == new['version']
        old_rows, new_rows = old['rows'], new['rows']
        mutable = {'tasks', 'sprints', 'sprint_revisions', 'sprint_members', 'sequencing_history', 'requests', 'meta'}
        for table in old_rows:
            if table not in mutable:
                assert old_rows[table] == new_rows[table], table
        if changed:
            assert all(row in new_rows['tasks'] for row in old_rows['tasks'])
            assert len(new_rows['tasks']) == len(old_rows['tasks']) + 1
            assert all(row in new_rows['sprint_members'] for row in old_rows['sprint_members'])
            assert len(new_rows['sprint_members']) == len(old_rows['sprint_members']) + 1
            assert self.sql('SELECT sprint_id,task_id FROM sprint_members WHERE task_id=?', (task,)) == [('S-DST', task)]
            assert self.sql('SELECT task_id,state,terminal_json,requirements_statement_revision,requirements_snapshot_revision '
                            'FROM tasks WHERE task_id=?', (task,)) == [(task, 'available', None,
                reference if field == 'requirements_statement_revision' else None,
                reference if field == 'requirements_snapshot_revision' else None)]
            for row in old_rows['sprints']:
                assert (('S-DST', sprint_revision, row[2]) if row[0] == 'S-DST' else row) in new_rows['sprints']
            assert len(old_rows['sprints']) == len(new_rows['sprints'])
            assert all(row in new_rows['sprint_revisions'] for row in old_rows['sprint_revisions'])
            assert all(row in new_rows['sequencing_history'] for row in old_rows['sequencing_history'])
            appended = [row for row in new_rows['sequencing_history'] if row not in old_rows['sequencing_history']]
            assert len(appended) == 1
            assert appended[0][1] == task and appended[0][4] == claimant
        else:
            for table in mutable - {'requests', 'meta'}:
                assert old_rows[table] == new_rows[table], table
        assert len(new_rows['requests']) == len(old_rows['requests']) + 1
        assert all(row in new_rows['requests'] for row in old_rows['requests'])
        assert self.sql('SELECT value FROM meta WHERE key=?', ('revision',)) == [(self.revision,)]

    def family(self, value, selectors=(P, D)):
        self.query([{'id': 'family-' + task, 'kind': 'duplicate_family', 'task_id': task} for task in selectors],
                   [{'id': 'family-' + task, 'value': value} for task in selectors])

    def outcome(self, task, kind, commit=None, identity=None):
        identity = identity or 'outcome-' + task
        tokens = {'$ID': identity, '$TASK': task, '$KIND': kind, '$COMMIT': commit,
                  '$SOURCE_ID': 'source-' + identity}
        fact = render(DATA['templates']['outcome'], tokens)
        state = {'positive': 'completed', 'negative': 'available', 'cancelled': 'cancelled'}[kind]
        self.apply([render(DATA['templates']['outcome_operation'], {'$FACT': fact})],
                   [render(DATA['templates']['outcome_result'], dict(tokens, **{'$STATE': state}))])
        return fact


@pytest.mark.parametrize('origin', ['independent', 'same', 'other'])
@pytest.mark.parametrize('field', ['requirements_statement_revision', 'requirements_snapshot_revision'])
def test_explicit_new_duplicate_registration_preserves_source_and_graph(tmp_path, origin, field):
    case = Family(tmp_path, origin)
    case.duplicate(field=field)
    expected = DATA['family_views'][field + ':' + origin]
    case.family(expected)
    case.query([{'id': 'ordinary', 'kind': 'task', 'task_id': F},
                {'id': 'ordinary-family', 'kind': 'duplicate_family', 'task_id': F}],
               [{'id': 'ordinary', 'value': DATA['ordinary_card']}, {'id': 'ordinary-family', 'value': None}])


def test_child_copy_resolves_original_root_and_all_siblings(tmp_path):
    case = Family(tmp_path)
    case.duplicate()
    case.duplicate(E, D, reference=8, sprint_revision=3)
    case.family(DATA['family_views']['siblings'], (P, D, E))


def test_historical_outer_and_business_replay_keep_current_family_and_result(tmp_path):
    case = Family(tmp_path)
    packet, response = case.duplicate()
    case.duplicate(E, D, reference=8, sprint_revision=3)
    fact = case.outcome(P, 'positive', DATA['commits'][0])
    view = deepcopy(DATA['family_views']['siblings'])
    root = view['members'][0]
    root['task']['state'] = 'completed'
    root['task']['terminal'] = render(DATA['templates']['positive_terminal'],
        {'$ID': 'outcome-' + P, '$TASK': P, '$COMMIT': DATA['commits'][0], '$SOURCE_ID': 'source-outcome-' + P})
    root['execution_facts']['outcome'] = fact
    before = case.snapshot()
    expected = dict(response, replayed=True)
    assert case.client.invoke(packet) == expected
    assert case.snapshot() == before
    case.duplicate(parent=E, claimant='CALLER-B', changed=False, op_id='fresh-duplicate')
    case.family(view, (P, D, E))


@pytest.mark.parametrize('change', ['root', 'kind', 'revision', 'destination'])
def test_each_business_identity_component_conflicts_independently(tmp_path, change):
    case = Family(tmp_path)
    case.duplicate()
    operation = deepcopy(DATA['base_operation'])
    if change == 'root':
        operation['parent_id'] = Z
    elif change == 'kind':
        del operation['requirements_statement_revision']
        operation['requirements_snapshot_revision'] = 7
    elif change == 'revision':
        operation['requirements_statement_revision'] = 8
    else:
        operation['sprint_id'] = 'S-ALT'
    case.reject(case.packet([operation]), 'duplicate_conflict')


@pytest.mark.parametrize('problem', ['source', 'destination', 'published', 'ordinary'])
def test_domain_refusals_preserve_the_complete_preimage(tmp_path, problem):
    case = Family(tmp_path)
    operation = deepcopy(DATA['base_operation'])
    if problem == 'source':
        operation['parent_id'] = '999998'
        reason = 'task_not_found'
    elif problem == 'destination':
        operation['sprint_id'] = 'MISSING'
        reason = 'sprint_not_found'
    elif problem == 'published':
        case.sql('UPDATE sprints SET state=? WHERE sprint_id=?', ('published', 'S-DST'))
        reason = 'sprint_not_draft'
    else:
        operation['task_id'] = F
        operation['requirements_statement_revision'] = 1
        reason = 'identity_conflict'
    case.reject(case.packet([operation]), reason)


def test_later_semantic_failure_rolls_back_duplicate_and_sprint_revision(tmp_path):
    case = Family(tmp_path)
    later = DATA['templates']['later_failure']
    case.reject(case.packet([deepcopy(DATA['base_operation']), later]), 'task_not_found')
    case.duplicate()
    case.family(DATA['family_views']['requirements_statement_revision:independent'])


@pytest.mark.parametrize('row', DATA['invalid'], ids=lambda row: row['id'])
def test_finite_new_wire_grammar_matrix_has_no_effects(tmp_path, row):
    case = Family(tmp_path)
    if 'operation' in row:
        packet = case.packet([row['operation']])
    else:
        packet = {'schema': DATA['schema'], 'operation': 'query', 'input': {'queries': [row['query']]}}
    case.reject(packet, row['reason'])


def test_opaque_caller_and_unknown_family_lookup(tmp_path):
    case = Family(tmp_path)
    case.duplicate(claimant=DATA['opaque_claimant'])
    case.family(DATA['family_views']['requirements_statement_revision:independent'])
    case.reject({'schema': DATA['schema'], 'operation': 'query',
                 'input': {'queries': [{'id': 'missing', 'kind': 'duplicate_family', 'task_id': '999998'}]}},
                'task_not_found')


@pytest.mark.parametrize('outcome', ['nonterminal', 'cancelled', 'no-result', 'result'])
def test_dependency_consumes_addressed_child_result_after_every_success(tmp_path, outcome):
    case = Family(tmp_path)
    case.outcome(P, 'positive', DATA['commits'][0])
    case.duplicate()
    # Test-owned graph arrangement; current public graph-editing is outside this Task.
    case.sql('INSERT INTO sprint_dependencies(sprint_id,predecessor,successor,kind) VALUES(?,?,?,?)',
             ('S-DST', D, U, 'result'))
    case.sql('INSERT INTO sprint_dependencies(sprint_id,predecessor,successor,kind) VALUES(?,?,?,?)',
             ('S-DST', B, U, 'completion'))
    if outcome == 'cancelled':
        case.outcome(D, 'cancelled')
    elif outcome == 'no-result':
        case.outcome(D, 'positive')
    elif outcome == 'result':
        case.outcome(D, 'positive', DATA['commits'][1])
    before_completion = render(DATA['eligibility'], {'$ELIGIBLE': False,
        '$BLOCKERS': ([{'predecessor': B, 'kind': 'completion', 'reason': 'predecessor_not_completed'}] +
                     ([] if outcome == 'result' else DATA['blocked_missing'] if outcome == 'no-result'
                      else DATA['blocked_nonterminal'])),
        '$PROVENANCE': DATA['producer'] if outcome == 'result' else []})
    # The public contract sorts predecessors by ID: child D precedes completion B.
    before_completion['blockers'].sort(key=lambda item: item['predecessor'])
    query = DATA['templates']['eligibility_query']
    case.query([query], [{'id': 'eligible', 'value': before_completion}])
    case.outcome(B, 'positive')
    wanted = render(DATA['eligibility'], {'$ELIGIBLE': outcome == 'result',
        '$BLOCKERS': [] if outcome == 'result' else DATA['blocked_missing'] if outcome == 'no-result'
        else DATA['blocked_nonterminal'], '$PROVENANCE': DATA['producer'] if outcome == 'result' else []})
    case.query([query], [{'id': 'eligible', 'value': wanted}])


def test_retired_replacement_operation_and_populated_owned_store(tmp_path):
    case = Family(tmp_path)
    case.reject(case.packet([DATA['retired_operation']]), DATA['retired_reason'])
    case.duplicate()
    case.family(DATA['family_views']['requirements_statement_revision:independent'])


def test_family_projects_live_claim_workspace_and_opaque_execution_facts(tmp_path):
    case = Family(tmp_path)
    paths = {kind: str(tmp_path / ('source-' + kind)) for kind in ('code', 'main', 'artifact')}
    for path in paths.values():
        Path(path).mkdir()
        (Path(path) / 'work.txt').write_text('Source-owned work remains in place.\n')
    case.sql('INSERT INTO task_claims(claim_id,task_id,claimant,status) VALUES(?,?,?,?)',
             ('ROOT-CLAIM', P, 'ROOT-OWNER', 'active'))
    case.sql('UPDATE tasks SET state=? WHERE task_id=?', ('claimed', P))
    case.sql('INSERT INTO workspaces(workspace_id,task_id,repository_id,base_commit,branch,status,read_only) '
             'VALUES(?,?,?,?,?,?,?)', ('ROOT-WORKSPACE', P, 'fixture-repo', DATA['commits'][1],
                                      'tasks/' + P, 'ready', 0))
    for kind, path in paths.items():
        case.sql('INSERT INTO workspace_roots(workspace_id,kind,position,path) VALUES(?,?,?,?)',
                 ('ROOT-WORKSPACE', kind, 0, path))
    case.sql('INSERT INTO workspace_bindings(workspace_id,session_id) VALUES(?,?)',
             ('ROOT-WORKSPACE', 'ROOT-OWNER'))
    case.apply([DATA['root_label_operation']], [DATA['root_label_result']])
    case.duplicate()
    view = deepcopy(DATA['family_views']['requirements_statement_revision:independent'])
    view['members'][0]['task']['state'] = 'claimed'
    view['members'][0]['task']['claim'] = DATA['claim_value']
    workspace = deepcopy(DATA['root_workspace'])
    for root in workspace['roots']:
        root['path'] = root['path'].replace('$ROOT', str(tmp_path))
    workspace['path'] = workspace['path'].replace('$ROOT', str(tmp_path))
    view['members'][0]['task']['workspace'] = workspace
    view['members'][0]['execution_facts']['stage_label'] = DATA['root_label_fact']
    case.family(view)
    negative = case.outcome(D, 'negative')
    view['members'][1]['execution_facts']['outcome'] = negative
    case.family(view)


def test_explicit_identifier_configuration_accepts_nonnumeric_family_ids(tmp_path):
    case = Family(tmp_path, patterns=DATA['custom_patterns'])
    case.apply(DATA['custom_register'], DATA['custom_register_results'])
    case.apply([DATA['custom_duplicate']], [DATA['custom_duplicate_result']])
    case.family(DATA['custom_family'], ('ROOT', 'CHILD'))
