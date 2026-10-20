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
# These existing owners are unaffected by a family registration. Future family
# storage is deliberately outside this list; public views prove its relation.
UNCHANGED_REGISTRATION_TABLES = (
    'sprint_dependencies', 'task_claims', 'workspaces',
    'workspace_roots', 'workspace_bindings',
)

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

    @classmethod
    def from_prefamily_store(cls, root):
        """Populate supported format 2 independently, before any current CLI."""
        case = cls.__new__(cls)
        case.client = Client(root)
        case.root, case.db = root, case.client.database
        case.origin, case.serial = 'independent', 100
        case.revision = DATA['prefamily_store']['revision']
        case.db.with_suffix('.lock').touch()
        case.config = case.client.config.read_bytes()
        case.marker = root / 'foreign-wip.txt'
        case.marker.write_text('Retained unrelated work.\n')
        case.tokens = {}
        for kind in ('code', 'main', 'artifact'):
            path = root / ('source-' + kind)
            path.mkdir()
            (path / 'work.txt').write_text('Source-owned work remains in place.\n')
            case.tokens['$ROOT/source-' + kind] = str(path)
        with closing(sqlite3.connect(case.db)) as conn:
            conn.executescript(DATA['prefamily_store']['schema_sql'])
            conn.execute('PRAGMA foreign_keys=ON')
            for table, rows in DATA['prefamily_store']['rows'].items():
                if rows:
                    conn.executemany('INSERT INTO "' + table + '" VALUES(' +
                        ','.join('?' for _ in rows[0]) + ')', render(rows, case.tokens))
            assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
            conn.commit()
        return case

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
        for table in UNCHANGED_REGISTRATION_TABLES:
            assert old_rows[table] == new_rows[table], table
        if changed:
            fields = [row[1] for row in self.sql('PRAGMA table_info(tasks)')]
            positions = [fields.index(field) for field in DATA['prefamily_task_columns']]
            projected = lambda rows: [tuple(row[i] for i in positions) for row in rows]
            assert all(row in projected(new_rows['tasks']) for row in projected(old_rows['tasks']))
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
            expected_sprint = (DATA['destination_siblings'] if sprint_revision == 3
                               else DATA['destination_after'][self.origin])
            self.sprint_revision_delta(before, expected_sprint)
            assert all(row in new_rows['sequencing_history'] for row in old_rows['sequencing_history'])
            appended = [row for row in new_rows['sequencing_history'] if row not in old_rows['sequencing_history']]
            assert len(appended) == 1
            assert appended[0][1] == task and appended[0][4] == claimant
        else:
            # A business replay has no new family or other domain effect.
            for table in set(old_rows) - {'requests', 'meta'}:
                assert old_rows[table] == new_rows[table], table
        assert len(new_rows['requests']) == len(old_rows['requests']) + 1
        assert all(row in new_rows['requests'] for row in old_rows['requests'])
        assert self.sql('SELECT value FROM meta WHERE key=?', ('revision',)) == [(self.revision,)]

    def sprint_revision_delta(self, before, expected):
        old = before['database']['rows']['sprint_revisions']
        new = self.snapshot()['database']['rows']['sprint_revisions']
        assert all(row in new for row in old)
        appended = [row for row in new if row not in old]
        assert len(new) == len(old) + 1 and len(appended) == 1
        row = appended[0]
        assert row[:3] == (expected['sprint_id'], expected['sprint_revision'], 'draft')
        assert json.loads(row[3]) == expected

    def family(self, value, selectors=(P, D)):
        queries = [{'id': 'family-' + task, 'kind': 'duplicate_family', 'task_id': task} for task in selectors]
        expected = [{'id': 'family-' + task, 'value': value} for task in selectors]
        for member in value['members']:
            task = member['task']['task_id']
            queries += [{'id': 'card-' + task, 'kind': 'task', 'task_id': task},
                        {'id': 'facts-' + task, 'kind': 'execution_facts', 'task_id': task}]
            expected += [{'id': 'card-' + task, 'value': member['task']},
                         {'id': 'facts-' + task, 'value': member['execution_facts']}]
        self.query(queries, expected)

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
    case.query([{'id': 'destination', 'kind': 'sprint', 'sprint_id': 'S-DST'}],
               [{'id': 'destination', 'value': DATA['destination_after'][origin]}])
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
    corrected = dict(later, task_id=F)
    packet = case.packet([deepcopy(DATA['base_operation']), corrected])
    before = case.snapshot()
    actual = case.client.invoke(packet)
    claim_id = actual['results'][1]['claim_id']
    assert isinstance(claim_id, str) and claim_id
    duplicate_result = render(DATA['duplicate_result'],
        {'$OP': 'duplicate', '$CHANGED': True, '$TASK': D, '$SPRINT_REV': 2})
    claim_result = render(DATA['batch_claim_result'], {'$CLAIM': claim_id})
    expected = render(DATA['apply_response'],
        {'$REV': case.revision + 1, '$RESULTS': [duplicate_result, claim_result]})
    assert actual == expected
    case.revision += 1
    after = case.snapshot()
    assert before['files'] == after['files'] and before['directories'] == after['directories']
    assert before['database']['schema'] == after['database']['schema']
    assert before['database']['version'] == after['database']['version']
    old, new = before['database']['rows'], after['database']['rows']
    for table in UNCHANGED_REGISTRATION_TABLES:
        if table != 'task_claims':
            assert old[table] == new[table], table
    assert len(new['tasks']) == len(old['tasks']) + 1
    fields = [row[1] for row in case.sql('PRAGMA table_info(tasks)')]
    positions = [fields.index(field) for field in DATA['prefamily_task_columns']]
    projected = [tuple(row[i] for i in positions) for row in new['tasks']]
    for row in old['tasks']:
        wanted = dict(zip(fields, row))
        if wanted['task_id'] == F:
            wanted['state'] = 'claimed'
        assert tuple(wanted[field] for field in DATA['prefamily_task_columns']) in projected
    assert len(new['sprint_members']) == len(old['sprint_members']) + 1
    assert all(row in new['sprint_members'] for row in old['sprint_members'])
    case.sprint_revision_delta(before, DATA['destination_after']['independent'])
    for row in old['sprints']:
        assert (('S-DST', 2, row[2]) if row[0] == 'S-DST' else row) in new['sprints']
    assert len(new['sprints']) == len(old['sprints'])
    assert all(row in new['task_claims'] for row in old['task_claims'])
    assert len(new['task_claims']) == len(old['task_claims']) + 1
    assert len(new['requests']) == len(old['requests']) + 1
    assert all(row in new['requests'] for row in old['requests'])
    saved = case.sql('SELECT response_json FROM requests WHERE request_id=?', (packet['input']['request_id'],))
    assert len(saved) == 1 and json.loads(saved[0][0]) == expected
    assert all(row in new['sequencing_history'] for row in old['sequencing_history'])
    assert len(new['sequencing_history']) == len(old['sequencing_history']) + 2
    appended = sorted((row for row in new['sequencing_history'] if row not in old['sequencing_history']),
                      key=lambda row: row[0])
    assert [(row[1], row[4]) for row in appended] == [(D, 'CALLER-A'), (F, 'LATER')]
    assert case.sql('SELECT task_id,claimant,status FROM task_claims WHERE claim_id=?',
                    (claim_id,)) == [(F, 'LATER', 'active')]
    assert case.sql('SELECT state FROM tasks WHERE task_id=?', (F,)) == [('claimed',)]
    assert case.sql('SELECT revision FROM sprints WHERE sprint_id=?', ('S-DST',)) == [(2,)]
    assert case.sql('SELECT sprint_id FROM sprint_members WHERE task_id=?', (D,)) == [('S-DST',)]
    assert case.sql('SELECT value FROM meta WHERE key=?', ('revision',)) == [(case.revision,)]
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
    case.sql('UPDATE sprint_revisions SET data=? WHERE sprint_id=? AND revision=?',
             (json.dumps(DATA['destination_with_dependencies']), 'S-DST', 2))
    if outcome == 'cancelled':
        case.outcome(D, 'cancelled')
    elif outcome == 'no-result':
        case.outcome(D, 'positive')
    elif outcome == 'result':
        case.outcome(D, 'positive', DATA['commits'][1])
    before_completion = DATA['dependency_cases'][outcome]['before']
    query = DATA['templates']['eligibility_query']
    case.query([query], [{'id': 'eligible', 'value': before_completion}])
    case.outcome(B, 'positive')
    wanted = DATA['dependency_cases'][outcome]['after']
    case.query([query], [{'id': 'eligible', 'value': wanted}])


def test_retired_replacement_operation_and_populated_owned_store(tmp_path):
    case = Family(tmp_path)
    case.reject(case.packet([DATA['retired_operation']]), DATA['retired_reason'])
    case.duplicate()
    case.family(DATA['family_views']['requirements_statement_revision:independent'])


def test_populated_prefamily_store_opens_without_losing_owned_data(tmp_path):
    case = Family.from_prefamily_store(tmp_path)
    before = case.snapshot()
    columns = {table: [row[1] for row in case.sql('PRAGMA table_info("' + table + '")')]
               for table in before['database']['rows']}
    assert before['database']['version'] == 2
    assert {table: len(rows) for table, rows in before['database']['rows'].items()} == {
        table: len(rows) for table, rows in DATA['prefamily_store']['rows'].items()}
    # The first current CLI entry sees all prior data; no seed/warmup/factory was used.
    packet = {'schema': DATA['schema'], 'operation': 'query',
              'input': {'queries': DATA['prefamily_store']['open_queries']}}
    expected = render(DATA['query_response'], {'$REV': 5,
        '$RESULTS': render(DATA['prefamily_store']['open_results'], case.tokens)})
    assert case.client.invoke(packet) == expected
    after = case.snapshot()
    assert before['files'] == after['files'] and before['directories'] == after['directories']
    # Compare old columns only: a future schema/table/version extension is permitted.
    for table, fields in columns.items():
        selected = ','.join('"' + field + '"' for field in fields)
        actual = case.sql('SELECT ' + selected + ' FROM "' + table + '"')
        assert sorted(actual, key=repr) == sorted(before['database']['rows'][table], key=repr), table
    before_replay = case.snapshot()
    assert case.client.invoke(DATA['prefamily_store']['replay_packet']) == DATA['prefamily_store']['replay_response']
    assert case.snapshot() == before_replay
    case.query([{'id': 'ordinary-root', 'kind': 'duplicate_family', 'task_id': P},
                {'id': 'ordinary-foreign', 'kind': 'duplicate_family', 'task_id': F}],
               [{'id': 'ordinary-root', 'value': None}, {'id': 'ordinary-foreign', 'value': None}])
    case.reject(case.packet([DATA['retired_operation']]), DATA['retired_reason'])
    case.duplicate()
    case.family(render(DATA['prefamily_store']['family_after'], case.tokens))
    foreign_queries = DATA['prefamily_store']['open_queries'][2:4]
    foreign_values = DATA['prefamily_store']['open_results'][2:4]
    case.query(foreign_queries, foreign_values)


@pytest.mark.parametrize('binding', ['ROOT-OWNER', 'WORKSPACE-OWNER', None])
def test_family_projects_live_claim_workspace_and_opaque_execution_facts(tmp_path, binding):
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
    if binding is not None:
        case.sql('INSERT INTO workspace_bindings(workspace_id,session_id) VALUES(?,?)',
                 ('ROOT-WORKSPACE', binding))
    case.apply([DATA['root_label_operation']], [DATA['root_label_result']])
    case.duplicate()
    view = deepcopy(DATA['family_views']['requirements_statement_revision:independent'])
    view['members'][0]['task']['state'] = 'claimed'
    view['members'][0]['task']['claim'] = DATA['claim_value']
    workspace = deepcopy(DATA['root_workspace'])
    workspace['owner_session_id'] = binding
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
