"""Received facts through the real CLI and independent SQLite/state observations."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from apps.sequencing.tests.test_registration import Client, current_state

DATA = json.loads((Path(__file__).resolve().parents[3] /
                   'apps/sequencing/fixtures/execution-facts-contract.json').read_text())
A, B, C, S, F = DATA['task_ids']
OWNER, OTHER = DATA['owners']


def render(value, tokens):
    if isinstance(value, str):
        return deepcopy(tokens.get(value, value))
    if isinstance(value, list):
        return [render(item, tokens) for item in value]
    if isinstance(value, dict):
        return {tokens.get(key, key): render(item, tokens) for key, item in value.items()}
    return value


def assert_exact(actual, expected):
    assert actual == expected


class Facts:
    def __init__(self, root, reference='statement', form='independent', graph=False):
        self.client = Client(root / 'client')
        self.root, self.database = root, self.client.database
        self.reference = 'requirements_' + reference + '_revision'
        self.form, self.serial, self.claims = form, 0, {}
        self.files = root / 'contexts'
        self.files.mkdir()
        self.config_bytes = self.client.config.read_bytes()
        operations = [render(DATA['register'], {'$TASK': task, '$REFERENCE': self.reference})
                      for task in DATA['task_ids']]
        if form == 'sprint':
            operations.append(render(DATA['sprint'], {'$EDGES': DATA['graph'] if graph else []}))
        self.generic_apply(operations)
        self.workspace(F, 'ready', OWNER)

    def rows(self, sql, values=()):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute(sql, values).fetchall()

    def write(self, sql, values=()):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute(sql, values)
            db.commit()

    @property
    def revision(self):
        return self.rows('SELECT value FROM meta WHERE key=?', ('revision',))[0][0]

    def packet(self, operations, request=None, revision=None):
        self.serial += 1
        return render(DATA['apply'], {'$REQUEST': request or 'request-' + str(self.serial),
            '$REVISION': self.revision if revision is None else revision, '$OPERATIONS': operations})

    def generic_apply(self, operations):
        before = self.revision if self.database.exists() else 0
        response = self.client.invoke(self.packet(operations, revision=before))
        assert response['schema'] == DATA['schema'] and response['status'] == 'applied'
        assert response['revision'] == before + 1 and response['replayed'] is False
        return response['results']

    def claim(self, task=A, owner=OWNER):
        self.serial += 1
        result = self.generic_apply([render(DATA['claim'], {'$TASK': task, '$OWNER': owner,
            '$INTENT': 'claim-' + str(self.serial)})])[0]
        self.claims[task] = (result['claim_id'], owner)
        return result['claim_id']

    def release(self, task=A):
        claim, owner = self.claims.pop(task)
        return self.generic_apply([render(DATA['release'], {'$TASK': task, '$CLAIM': claim,
            '$OWNER': owner})])[0]['handoff_id']

    def resume(self, handoff, task=A, owner=OTHER):
        result = self.generic_apply([render(DATA['resume'], {'$TASK': task, '$OWNER': owner,
            '$HANDOFF': handoff})])[0]
        self.claims[task] = (result['claim_id'], owner)

    def workspace(self, task, status, binding=None, identifier=None):
        identifier = identifier or 'WS-' + task
        paths = {}
        for kind in ('code', 'main', 'artifact'):
            path = self.files / identifier / kind
            path.mkdir(parents=True)
            (path / 'work.txt').write_text(DATA['marker'])
            paths[kind] = str(path)
        self.write('INSERT INTO workspaces(workspace_id,task_id,repository_id,base_commit,branch,status,read_only) '
                   'VALUES(?,?,?,?,?,?,?)',
                   (identifier, task, 'fixture-repo', 'b' * 40, 'tasks/' + task, status, 0))
        for kind, path in paths.items():
            self.write('INSERT INTO workspace_roots(workspace_id,kind,position,path) VALUES(?,?,?,?)',
                       (identifier, kind, 0, path))
        if binding is not None:
            self.write('INSERT INTO workspace_bindings(workspace_id,session_id) VALUES(?,?)',
                       (identifier, binding))
        value = render(DATA['workspace_value'], {'$WORKSPACE': identifier, '$TASK': task,
            '$BRANCH': 'tasks/' + task, '$STATUS': status, '$CODE': paths['code'],
            '$MAIN': paths['main'], '$ARTIFACT': paths['artifact'], '$BINDING': binding})
        if status != 'released':
            assert self.query('task', task)['workspace'] == value

    def snapshot(self):
        return {'database': current_state(self.database),
                'files': {str(p.relative_to(self.root)): p.read_bytes()
                          for p in self.root.rglob('*') if p.is_file() and p != self.database},
                'directories': sorted(str(p.relative_to(self.root))
                                      for p in self.root.rglob('*') if p.is_dir()),
                'config': self.client.config.read_bytes()}

    def preserved(self, before, request, response, changed_tasks=(), history_tasks=()):
        after = self.snapshot()
        assert before['files'] == after['files'] and before['config'] == after['config'] == self.config_bytes
        assert before['directories'] == after['directories']
        old, new = before['database'], after['database']
        assert old['schema'] == new['schema'] and old['version'] == new['version'] == 2
        assert set(old['rows']) == set(new['rows'])
        for table in old['rows']:
            if table not in ('tasks', 'sequencing_history', 'requests', 'meta'):
                assert old['rows'][table] == new['rows'][table], table
        prior_tasks = {row[0]: row for row in old['rows']['tasks']}
        actual_tasks = {row[0]: row for row in new['rows']['tasks']}
        assert prior_tasks.keys() == actual_tasks.keys()
        for task, row in prior_tasks.items():
            if task in changed_tasks:
                assert row[:3] == actual_tasks[task][:3]
            else:
                assert row == actual_tasks[task], task
        prior_history = old['rows']['sequencing_history']
        assert all(row in new['rows']['sequencing_history'] for row in prior_history)
        appended = sorted((row for row in new['rows']['sequencing_history'] if row not in prior_history),
                          key=lambda row: row[0])
        assert [row[1] for row in appended] == list(history_tasks)
        prior_receipts = old['rows']['requests']
        assert all(row in new['rows']['requests'] for row in prior_receipts)
        receipts = [row for row in new['rows']['requests'] if row not in prior_receipts]
        assert len(receipts) == 1 and receipts[0][0] == request
        assert json.loads(receipts[0][2]) == response
        old_meta, new_meta = dict(old['rows']['meta']), dict(new['rows']['meta'])
        assert new_meta == {**old_meta, 'revision': old_meta['revision'] + 1}

    def query(self, kind, task=A):
        packet = render(DATA['query'], {'$QUERY_KIND': kind, '$TASK': task})
        before = self.snapshot()
        response = self.client.invoke(packet)
        assert self.snapshot() == before
        assert set(response) == {'schema', 'status', 'revision', 'results'}
        assert response['schema'] == DATA['schema'] and response['status'] == 'read_only'
        assert response['revision'] == self.revision
        assert len(response['results']) == 1
        assert set(response['results'][0]) == {'id', 'value'}
        assert response['results'][0]['id'] == 'read'
        return response['results'][0]['value']

    def facts(self, label=None, outcome=None, task=A):
        assert_exact(self.query('execution_facts', task), render(DATA['facts_value'], {
            '$TASK': task, '$LABEL_VALUE': label, '$OUTCOME_VALUE': outcome}))

    def task(self, state='available', terminal=None, task=A, workspace=None):
        claim = None
        if task in self.claims:
            claim_id, owner = self.claims[task]
            claim = render(DATA['claim_value'], {'$TASK': task, '$CLAIM': claim_id, '$OWNER': owner})
        expected = render(DATA['task_value'], {'$TASK': task, '$REFERENCE': self.reference,
            '$STATE': state, '$SPRINT': 'SPR-FACTS' if self.form == 'sprint' else None,
            '$CLAIM_VALUE': claim, '$WORKSPACE_VALUE': workspace, '$TERMINAL': terminal})
        assert_exact(self.query('task', task), expected)
        rows = self.rows('SELECT task_id,state,terminal_json FROM tasks WHERE task_id=?', (task,))
        assert len(rows) == 1 and rows[0][:2] == (task, state)
        assert (None if rows[0][2] is None else json.loads(rows[0][2])) == terminal

    def label(self, label_id='L1', label='opaque unknown stage', task=A):
        claim, owner = self.claims.get(task, ('absent-claim', OWNER))
        tokens = {'$TASK': task, '$CLAIM': claim, '$OWNER': owner,
                  '$LABEL_ID': label_id, '$LABEL': label, '$SOURCE': label_id + '-source'}
        return render(DATA['label'], tokens), render(DATA['label_value'], tokens)

    def outcome(self, kind='positive', commit=None, outcome_id='O1', task=A):
        tokens = {'$TASK': task, '$OUTCOME_ID': outcome_id, '$OUTCOME_KIND': kind,
                  '$COMMIT': commit, '$SOURCE': outcome_id + '-source'}
        return render(DATA['outcome'], tokens), render(DATA['outcome_value'], tokens)

    def label_item(self, fact, replay=False):
        return render(DATA['label_result'], {'$TASK': fact['task_id'], '$LABEL_ID': fact['label_id'],
            '$CHANGED': not replay, '$FACT_REPLAYED': replay})

    def outcome_item(self, fact, state, replay=False):
        return render(DATA['outcome_result'], {'$TASK': fact['task_id'], '$OUTCOME_ID': fact['outcome_id'],
            '$STATE': state, '$CHANGED': not replay, '$FACT_REPLAYED': replay})

    def terminal(self, fact, kind='positive'):
        return render(DATA[kind + '_terminal'], {'$TASK': fact['task_id'], '$OUTCOME_ID': fact['outcome_id'],
                      '$COMMIT': fact['result_commit'], '$SOURCE': fact['source_ref']['id'],
                      '$SOURCE_OWNER': fact['source_ref']['owner']})

    def accept(self, operations, items, changed_tasks=(), history_tasks=()):
        packet = self.packet(operations)
        before, revision = self.snapshot(), self.revision
        expected = render(DATA['apply_value'], {'$REVISION': revision + 1, '$RESULTS': items})
        response = self.client.invoke(packet)
        assert_exact(response, expected)
        self.preserved(before, packet['input']['request_id'], expected, changed_tasks, history_tasks)
        return packet, response

    def reject(self, packet, reason=None):
        before = self.snapshot()
        response = self.client.invoke(packet, 2)
        assert set(response) == {'schema', 'status', 'reason'}
        assert response['schema'] == DATA['schema'] and response['status'] == 'rejected'
        assert isinstance(response['reason'], str) and response['reason']
        if reason is not None:
            assert response['reason'] == reason
        assert self.snapshot() == before

    def replay_outer(self, packet, response):
        before = self.snapshot()
        assert_exact(self.client.invoke(packet), {**response, 'replayed': True})
        assert self.snapshot() == before

    def receive(self, kind='positive', commit=None, outcome_id='O1', task=A, state='completed'):
        op, fact = self.outcome(kind, commit, outcome_id, task)
        self.accept([op], [self.outcome_item(fact, state)], [task], [task])
        return op, fact

    def label_availability(self, variant):
        before = self.snapshot()
        response = self.client.invoke(render(DATA['eligibility'], {'$TASK': A}))
        expected = render(DATA['label_availability'][variant], {'$CLAIM': self.claims[A][0]})
        assert_exact(response, render(DATA['query_value'], {'$REVISION': self.revision,
                                                          '$VALUE': expected}))
        response = self.client.invoke(DATA['next_all'])
        assert_exact(response, render(DATA['query_value'], {'$REVISION': self.revision,
                     '$VALUE': {'task_ids': DATA['label_next_ids'][variant]}}))
        assert self.snapshot() == before

    def eligibility(self, blockers, provenance, next_ids, task=S):
        before = self.snapshot()
        response = self.client.invoke(render(DATA['eligibility'], {'$TASK': task}))
        value = render(DATA['eligibility_value'], {'$TASK': task, '$ELIGIBLE': not blockers,
                       '$BLOCKERS': blockers, '$PROVENANCE': provenance})
        assert_exact(response, render(DATA['query_value'], {'$REVISION': self.revision, '$VALUE': value}))
        next_response = self.client.invoke(DATA['next'])
        assert_exact(next_response, render(DATA['query_value'], {'$REVISION': self.revision,
                     '$VALUE': {'task_ids': next_ids}}))
        assert self.snapshot() == before


@pytest.mark.parametrize('reference,form', DATA['contexts'])
def test_label_metadata_and_replay(tmp_path, reference, form):
    work = Facts(tmp_path, reference, form)
    work.claim()
    work.label_availability('own')
    work.facts()
    before_task = work.query('task')
    first, old = work.label()
    packet, response = work.accept([first], [work.label_item(old)], history_tasks=[A])
    work.label_availability('own')
    second, latest = work.label('L2', 'stage without graph or artifact data')
    work.accept([second], [work.label_item(latest)], history_tasks=[A])
    work.facts(latest)
    work.label_availability('own')
    assert work.query('task') == before_task
    work.resume(work.release())
    current = work.query('task')
    work.label_availability('foreign')
    work.accept([first], [work.label_item(old, True)])
    work.label_availability('foreign')
    work.replay_outer(packet, response)
    work.facts(latest)
    work.label_availability('foreign')
    assert work.query('task') == current


@pytest.mark.parametrize('kind,commit,reference,form,state', DATA['mapping_cases'])
def test_received_outcome_mapping(tmp_path, kind, commit, reference, form, state):
    work = Facts(tmp_path, reference, form)
    op, fact = work.outcome(kind, commit)
    packet, response = work.accept([op], [work.outcome_item(fact, state)], [A], [A])
    work.facts(outcome=fact)
    terminal = work.terminal(fact, kind) if kind in ('positive', 'cancelled') else None
    work.task(state, terminal)
    work.replay_outer(packet, response)
    work.accept([op], [work.outcome_item(fact, state, True)])
    work.facts(outcome=fact)
    work.task(state, terminal)


def test_negative_then_success_keeps_historical_replay(tmp_path):
    work = Facts(tmp_path)
    negative, old = work.receive('negative', outcome_id='SHARED', state='available')
    work.task()
    work.claim()
    label, label_value = work.label('SHARED')
    work.accept([label], [work.label_item(label_value)], history_tasks=[A])
    work.release()
    positive, latest = work.receive(commit=DATA['commit'], outcome_id='POSITIVE')
    work.accept([negative], [work.outcome_item(old, 'available', True)])
    work.accept([label], [work.label_item(label_value, True)])
    work.facts(label_value, latest)
    work.task('completed', work.terminal(latest))


@pytest.mark.parametrize('commit', [None, DATA['commit']])
def test_all_predecessors_and_result_provenance(tmp_path, commit):
    work = Facts(tmp_path / 'success', form='sprint', graph=True)
    work.eligibility(DATA['blockers']['all'], [], DATA['next_ids']['all'])
    work.receive(outcome_id='A-success')
    work.receive('negative', outcome_id='B-negative', task=B, state='available')
    work.eligibility(DATA['blockers']['bc'], [], DATA['next_ids']['bc'])
    work.receive(outcome_id='B-success', task=B)
    work.eligibility(DATA['blockers']['c'], [], DATA['next_ids']['c'])
    work.receive(commit=commit, outcome_id='C-success', task=C)
    work.eligibility(DATA['blockers']['missing'] if commit is None else [],
                     [] if commit is None else DATA['result_provenance'],
                     DATA['next_ids']['missing' if commit is None else 'eligible'])
    cancelled = Facts(tmp_path / 'cancelled', form='sprint', graph=True)
    cancelled.receive(outcome_id='A-success')
    cancelled.receive('cancelled', outcome_id='B-cancelled', task=B, state='cancelled')
    cancelled.receive(commit=DATA['commit'], outcome_id='C-success', task=C)
    cancelled.eligibility(DATA['blockers']['cancelled_b'], DATA['result_provenance'],
                          DATA['next_ids']['cancelled_b'])


@pytest.mark.parametrize('kind', ['label', 'outcome'])
def test_fact_identity_is_global_within_kind(tmp_path, kind):
    work = Facts(tmp_path)
    if kind == 'label':
        work.claim()
        op, fact = work.label()
        work.accept([op], [work.label_item(fact)], history_tasks=[A])
        work.accept([op], [work.label_item(fact, True)])
        changed = deepcopy(op)
        changed['label'] = 'another label'
        work.reject(work.packet([changed]), 'label_conflict')
        changed = deepcopy(op)
        changed['source_ref']['id'] = 'other source'
        work.reject(work.packet([changed]), 'label_conflict')
        changed = deepcopy(op)
        changed['source_ref']['owner'] = 'other producer'
        work.reject(work.packet([changed]), 'label_conflict')
        changed = deepcopy(op)
        changed['task_id'] = B
        work.reject(work.packet([changed]), 'label_conflict')
        work.facts(fact)
    else:
        op, fact = work.receive('negative', state='available')
        work.accept([op], [work.outcome_item(fact, 'available', True)])
        for field, value in [('kind', 'cancelled'), ('task_id', B),
                             ('source_ref', {'owner': 'execution', 'id': 'other source'}),
                             ('source_ref', {'owner': 'other producer',
                                             'id': fact['source_ref']['id']})]:
            changed = deepcopy(op)
            changed['outcome'][field] = value
            work.reject(work.packet([changed]), 'outcome_conflict')
        work.facts(outcome=fact)


@pytest.mark.parametrize('kind', ['label', 'outcome', 'combined'])
def test_packet_rolls_back_all_prior_fact_effects(tmp_path, kind):
    work = Facts(tmp_path)
    work.claim()
    label, label_value = work.label()
    outcome, outcome_value = work.outcome(task=B)
    bad, _ = work.label('late-error', task=C)
    operations = [label] if kind == 'label' else [outcome]
    if kind == 'combined':
        operations = [label, outcome]
    work.reject(work.packet(operations + [bad]), 'claim_owner_mismatch')
    if kind == 'label':
        work.accept([label], [work.label_item(label_value)], history_tasks=[A])
    elif kind == 'outcome':
        work.accept([outcome], [work.outcome_item(outcome_value, 'completed')], [B], [B])
    else:
        work.accept([label, outcome], [work.label_item(label_value),
                    work.outcome_item(outcome_value, 'completed')], [B], [A, B])


def test_known_task_and_current_claim_admission(tmp_path):
    work = Facts(tmp_path)
    label, _ = work.label()
    work.reject(work.packet([label]), 'claim_owner_mismatch')
    work.reject(render(DATA['query'], {'$QUERY_KIND': 'execution_facts', '$TASK': '0099'}),
                'task_not_found')
    work.claim()
    label, fact = work.label()
    for key, value, reason in [('task_id', '0099', 'task_not_found'),
                               ('claim_id', 'other claim', 'claim_owner_mismatch'),
                               ('claimant', OTHER, 'claim_owner_mismatch')]:
        changed = deepcopy(label)
        changed[key] = value
        work.reject(work.packet([changed]), reason)
    for kind in ('positive', 'negative', 'cancelled'):
        outcome, _ = work.outcome(kind)
        work.reject(work.packet([outcome]), 'claim_conflict')
        outcome['outcome']['task_id'] = '0099'
        work.reject(work.packet([outcome]), 'task_not_found')
    work.accept([label], [work.label_item(fact)], history_tasks=[A])
    work.release()
    work.receive()


@pytest.mark.parametrize('kind,status', DATA['workspace_cases'])
def test_outcome_workspace_context_policy(tmp_path, kind, status):
    work = Facts(tmp_path)
    work.workspace(A, 'released', 'OLD-SESSION', 'WS-OLD')
    if status != 'released':
        work.workspace(A, status, OTHER)
    before_task = work.query('task')
    op, fact = work.outcome(kind)
    if status in ('reserved', 'release_pending'):
        work.reject(work.packet([op]), 'workspace_conflict')
    else:
        work.accept([op], [work.outcome_item(fact, 'completed')], [A], [A])
        work.task('completed', work.terminal(fact), workspace=before_task['workspace'])
        work.facts(outcome=fact)


def test_strict_fact_and_query_packets(tmp_path):
    work = Facts(tmp_path)
    work.claim()
    label, label_value = work.label()
    outcome, _ = work.outcome(task=B)
    query = render(DATA['query'], {'$QUERY_KIND': 'execution_facts', '$TASK': A})
    packets = [work.packet([label]), work.packet([outcome]), query]
    paths = [[('input', 'operations', 0), ('input', 'operations', 0, 'source_ref')],
             [('input', 'operations', 0), ('input', 'operations', 0, 'outcome'),
              ('input', 'operations', 0, 'outcome', 'source_ref')],
             [('input', 'queries', 0)]]
    for packet, levels in zip(packets, paths):
        for path in levels:
            original = packet
            for key in path:
                original = original[key]
            for invalid in (None, []):
                changed = deepcopy(packet)
                parent = changed
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = invalid
                work.reject(changed)
            for removed in list(original) + [None]:
                changed = deepcopy(packet)
                target = changed
                for key in path:
                    target = target[key]
                if removed is None:
                    target['extra'] = True
                else:
                    del target[removed]
                work.reject(changed)
    for value in DATA['bad_values']['label']:
        changed = deepcopy(label)
        changed['label'] = value
        work.reject(work.packet([changed]))
    for key in ('label_id', 'claim_id', 'claimant', 'task_id'):
        for value in ('', '\x00', 0):
            changed = deepcopy(label)
            changed[key] = value
            work.reject(work.packet([changed]))
    for key in ('outcome_id', 'task_id'):
        for value in ('', '\x00', False):
            changed = deepcopy(outcome)
            changed['outcome'][key] = value
            work.reject(work.packet([changed]))
    for value in DATA['bad_values']['kind']:
        changed = deepcopy(outcome)
        changed['outcome']['kind'] = value
        work.reject(work.packet([changed]))
    for value in DATA['bad_values']['commit']:
        changed = deepcopy(outcome)
        changed['outcome']['result_commit'] = value
        work.reject(work.packet([changed]))
    for kind in ('negative', 'cancelled'):
        changed = deepcopy(outcome)
        changed['outcome']['kind'] = kind
        changed['outcome']['result_commit'] = DATA['commit']
        work.reject(work.packet([changed]))
    for operation in (label, outcome):
        for value in (None, [], ''):
            changed = deepcopy(operation)
            holder = changed if changed['kind'] == 'record_stage_label' else changed['outcome']
            holder['source_ref'] = value
            work.reject(work.packet([changed]))
        for key in ('owner', 'id'):
            for value in ('', '\x00', False):
                changed = deepcopy(operation)
                holder = changed if changed['kind'] == 'record_stage_label' else changed['outcome']
                holder['source_ref'][key] = value
                work.reject(work.packet([changed]))
    bad_query = deepcopy(query)
    bad_query['input']['queries'][0]['task_id'] = False
    work.reject(bad_query)
    # Opaque identifiers accept whitespace; the literal label still remains opaque metadata.
    valid, expected = work.label(' opaque label id ', ' opaque stage label ')
    valid['source_ref']['id'] = ' provenance id '
    expected['source_ref']['id'] = ' provenance id '
    valid['source_ref']['owner'] = ' local caller '
    expected['source_ref']['owner'] = ' local caller '
    work.accept([valid], [work.label_item(expected)], history_tasks=[A])
    work.facts(expected)
    work.release()
    valid_outcome, expected_outcome = work.outcome(outcome_id=' opaque outcome id ', task=B)
    valid_outcome['outcome']['source_ref']['owner'] = 'orchestrator'
    expected_outcome['source_ref']['owner'] = 'orchestrator'
    work.accept([valid_outcome], [work.outcome_item(expected_outcome, 'completed')], [B], [B])
    work.facts(outcome=expected_outcome, task=B)
    work.task('completed', work.terminal(expected_outcome), task=B)


@pytest.mark.parametrize('kind,state', [('positive', 'completed'), ('cancelled', 'cancelled')])
def test_terminal_accepts_only_exact_outcome_replay(tmp_path, kind, state):
    work = Facts(tmp_path)
    work.claim()
    label, label_value = work.label()
    work.accept([label], [work.label_item(label_value)], history_tasks=[A])
    work.release()
    op, fact = work.outcome(kind)
    op['outcome']['source_ref']['owner'] = DATA['nondefault_terminal_owner']
    fact['source_ref']['owner'] = DATA['nondefault_terminal_owner']
    work.accept([op], [work.outcome_item(fact, state)], [A], [A])
    work.task(state, work.terminal(fact, kind))
    work.accept([op], [work.outcome_item(fact, state, True)])
    changed = deepcopy(op)
    changed['outcome']['outcome_id'] = 'another outcome'
    work.reject(work.packet([changed]), 'outcome_conflict')
    changed = deepcopy(op)
    changed['outcome']['source_ref']['id'] = 'different provenance'
    work.reject(work.packet([changed]), 'outcome_conflict')
    if kind == 'positive':
        changed = deepcopy(op)
        changed['outcome']['result_commit'] = DATA['commit']
        work.reject(work.packet([changed]), 'outcome_conflict')
    work.accept([label], [work.label_item(label_value, True)])
    work.facts(label_value, fact)
    work.task(state, work.terminal(fact, kind))
