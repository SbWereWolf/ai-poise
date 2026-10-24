"""Public preparation and independent observations for advancement checks."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from lifecycle_checks import Scenario, bind
from process_checks import create, edit, installation, invoke, write_json
from store_checks import subject

APP = Path(__file__).resolve().parents[1]
DATA = json.loads((APP / 'fixtures/advance-contract.json').read_text())
LOG = []


class MissingPublicAdvance(Exception):
    pass


def assert_traversal(actual, origin, current):
    """Compare semantic provenance with independently prepared public inputs."""
    expected = {key: origin[key] for key in [
        'stage_id', 'visit_id', 'source_generation', 'source_actor_ids',
        'gate_id', 'evidence_id', 'outcome']}
    expected.update(iteration=current['iteration'], current_evidence_id=current['evidence_id'])
    assert actual == expected, (actual, expected)


class AdvanceSubject(Scenario):
    def __init__(self, root, *, extra_guard=False):
        # Reuse actual Requirements/Sequencing/Workspace preparation, not its
        # unrelated execution history. Our subject has a separate public store.
        super().__init__(root / 'prerequisites')
        self.task_id = '0042'
        self.counter = 0
        self.definition = deepcopy(DATA['definition'])
        self.config = installation(root / 'advance')
        cfg = json.loads(self.config.read_text())
        old = json.loads((root / 'prerequisites/execution/execution.json').read_text())
        for key in ['lifecycle', 'gates']:
            cfg[key] = deepcopy(old[key])
        cfg['lifecycle']['actors']['reviewer-D'] = 'reviewer'
        write_json(self.config, cfg)
        self.version_file = self.git_root / 'source-version.txt'
        self.version_file.write_text(DATA['source_initial'])
        self.git('add', 'source-version.txt')
        self.git('commit', '--only', 'source-version.txt', '-m', 'Prepare source version')
        self.baseline = self.source()['tree']
        self.payload = self.git_root / 'outputs/subject.bin'
        self.payload.parent.mkdir(parents=True, exist_ok=True)
        self.payload.write_bytes(b'OK\n')
        code, process = invoke(self.config, create(definition=self.definition))
        assert code == 0 and process['status'] == 'published', process
        method = bind(DATA['method'], {'@PY': sys.executable, '@WORK': str(self.git_root)})
        self.contract = deepcopy(self.contract)
        self.contract.update(process_ref={'goal_type': 'writing', 'revision': process['process_revision']},
            stage_expectations=[{'stage_id': stage, 'values': [], 'files': [],
                'methods': ['guard'] if stage != 'finish' else []} for stage in DATA['actors']],
            methods=[method], evidence_plan=deepcopy(DATA['evidence_plan']))
        if extra_guard:
            second = deepcopy(method)
            second['id'] = 'extra'
            second['verification_plan']['green_stages'] = ['write']
            self.contract['methods'].append(second)
            self.contract['stage_expectations'][0]['methods'].append('extra')
        code, setup = invoke(self.config, {'schema': 'stride-execution/v1', 'operation': 'apply', 'input': {
            'request_id': 'advance-setup', 'expected_revision': process['revision'],
            'operations': [{'id': 'setup', 'kind': 'setup_execution', 'contract': self.contract}]}})
        assert code == 0 and setup['status'] == 'applied', setup
        self.revision, self.generation = setup['revision'], 1
        self.contract_revision = setup['results'][0]['contract_revision']
        self.database = self.config.parent / 'state/execution.sqlite'
        module = subject()
        self.store = module.Store(module.load_config(self.config))
        self.origin = {}
        self.first_evidence = None
        self.origins_by_visit = {}
        self.observed_frames = {}
        self.presentations = {}
        self.current_proofs = {}

    def act(self, action, **fields):
        result = super().act(action, **fields)
        if action == 'present':
            key = (self.generation, result['current_stage'], result['iteration'],
                   fields['context_id'], fields['candidate_id'])
            self.presentations[key] = fields.get('actor_id', 'executor-A')
        return result

    def identity(self, prefix):
        self.counter += 1
        return f'{prefix}-{self.task_id}-{self.counter}'

    def source(self):
        return {'commit': self.git('rev-parse', 'HEAD').strip(),
                'tree': self.git('rev-parse', 'HEAD^{tree}').strip()}

    def physical(self):
        result = super().physical()
        if hasattr(self, 'payload'):
            result['subject_bytes'] = self.payload.read_bytes()
            result['source_version'] = self.version_file.read_bytes()
        return result

    def sync(self):
        response = self.read('execution')
        state = response['results'][0]['value']
        self.revision, self.generation = response['revision'], state['generation']
        self.contract_revision = state['contract_revision']
        return state

    def read(self, kind):
        packet = {'schema': 'stride-execution/v1', 'operation': 'query', 'input': {
            'queries': [{'id': 'read', 'kind': kind, 'task_id': self.task_id}]}}
        code, result = invoke(self.config, packet)
        LOG.append({'packet': packet, 'exit_code': code, 'response': result})
        assert code == 0, result
        return result

    def progression(self):
        return self.read('progression')['results'][0]['result']

    def gate_ref(self, stage, *, tag='shared', iteration=None, files=None):
        state = self.sync()
        iteration = state['iteration'] if iteration is None else iteration
        context, candidate = f'context-{stage}-{iteration}-{tag}', f'candidate-{stage}-{iteration}-{tag}'
        declaration = {'action': 'declare', 'request_id': self.identity('declare'),
            'expected_revision': self.revision, 'task_id': self.task_id, 'generation': self.generation,
            'context_id': context, 'contract_revision': self.contract_revision,
            'requirement_ids': ['report'], 'task_content': {'sections': [], 'routes': [], 'requirements': []},
            'stage_contracts': [{'stage_id': s['id'], 'allowed_paths': s['allowed_paths'],
                'entry_requirements': [], 'exit_requirements': ['filled'],
                'criteria': {'requirements': [], 'definition_of_ready': [], 'definition_of_done': []}}
                for s in self.definition['stages']]}
        code, declared = invoke(self.config, {'schema': 'stride-execution/v1', 'operation': 'gate', 'input': declaration})
        assert code == 0, declared
        evaluation = {'action': 'evaluate', 'request_id': self.identity('evaluate'),
            'expected_revision': declared['revision'], 'task_id': self.task_id, 'generation': self.generation,
            'context_id': context, 'candidate_id': candidate, 'stage_id': stage, 'iteration': iteration,
            'sections': {'report': DATA['reports'][stage]}, 'trace': {}, 'files': [] if files is None else files}
        code, evaluated = invoke(self.config, {'schema': 'stride-execution/v1', 'operation': 'gate', 'input': evaluation})
        LOG.append({'gate': evaluation, 'exit_code': code, 'response': evaluated})
        assert code == 0 and evaluated['accepted'], evaluated
        self.revision = evaluated['revision']
        return {'stage_id': stage, 'context_id': context, 'candidate_id': candidate,
                'iteration': iteration, 'gate_id': evaluated['result_id']}

    def latest_argument(self):
        args = self.progression()['accepted_arguments']
        value = next(a for a in reversed(args) if a['id'] == 'witness')
        assert isinstance(value['revision'], str) and value['revision']
        return value['revision']

    def observe(self, frame, *, replay=True, omit=(), capture=None, contradiction=False,
                decision='accepted', review_revision=None, missing_review=False):
        self.sync()
        before = self.source()
        stage = frame['stage_id']
        schedule = next(s['methods'] for s in self.contract['stage_expectations'] if s['stage_id'] == stage)
        methods = [m for m in self.contract['methods'] if m['id'] in schedule]
        receipts = []
        for method in methods:
            if method['id'] in omit:
                continue
            env = dict(os.environ, **method['environment'])
            actual = subprocess.run(method['argv'], cwd=method['cwd'], env=env, capture_output=True)
            raw_out, raw_err = actual.stdout, actual.stderr
            try:
                out, err = raw_out.decode('utf-8'), raw_err.decode('utf-8')
                interpretable = True
            except UnicodeDecodeError:
                out, err = raw_out.decode('utf-8', errors='replace'), raw_err.decode('utf-8', errors='replace')
                interpretable = False
            complete = capture is None or len(raw_out) <= capture
            published = out if capture is None else raw_out[:capture].decode('utf-8', errors='replace')
            after = self.source()
            passed = (complete and interpretable and actual.returncode == method['expected_exit_code']
                and all(v in published for v in method['stdout_contains'])
                and all(v in err for v in method['stderr_contains']) and before == after)
            receipt = {'id': self.identity('observation'), 'method': method['id'],
                'obligations': [method['id']], 'argv': method['argv'], 'cwd': method['cwd'],
                'environment': method['environment'], 'actual_exit_code': actual.returncode,
                'stdout': published, 'stderr': err, 'stdout_bytes_hex': raw_out.hex(),
                'stderr_bytes_hex': raw_err.hex(), 'capture_complete': complete,
                'interpretable': interpretable, 'timed_out': False, 'guard': True,
                'passed': True if contradiction else passed, 'commit': before['commit'],
                'tree': before['tree'], 'source_before': before, 'source_after': after,
                'source_unchanged': before == after}
            receipts.append(receipt)
            LOG.append({'actual_child': deepcopy(receipt),
                        'semantic_negative_input_only': contradiction})
        work = {'phase': 'continue' if methods else 'prepare', 'arguments': [], 'decisions': []}
        if not replay and stage == 'write':
            work['arguments'] = [{'id': 'witness', 'kind': 'logical',
                'facts': ['Реальная команда вернула ожидаемый результат.'], 'assumptions': [],
                'inference': 'Наблюдение соответствует объявленной проверке.',
                'conclusion': 'Результат проверки подтверждён.', 'verdict': 'proved',
                'observation_ids': [r['id'] for r in receipts if r['method'] == 'guard']}]
        if not replay and stage == 'review' and not missing_review:
            work['decisions'] = [{'argument_id': 'witness',
                'revision': self.latest_argument() if review_revision is None else review_revision,
                'decision': decision, 'reason': 'Проверено фактическое обоснование автора.'}]
        attempt_id, evidence_id = self.identity('attempt'), self.identity('evidence')
        records = [
            {'kind': 'attempt', 'id': attempt_id, 'task_id': self.task_id,
             'generation': self.generation, 'revision': 1, 'data': {
                'status': 'completed', 'stage_id': stage, 'iteration': frame['iteration'],
                'context_id': frame['context_id'], 'candidate_id': frame['candidate_id'],
                'source': before, 'methods': methods, 'execution_key': attempt_id, 'receipts': receipts}},
            {'kind': 'evidence', 'id': evidence_id, 'task_id': self.task_id,
             'generation': self.generation, 'revision': 1,
             'data': {'attempt_id': attempt_id, 'evidence_work': work}},
        ]
        result = self.store.write_records(request_id=self.identity('facts'),
            expected_revision=self.revision, records=records)
        self.revision = result['revision']
        LOG.append({'public_records': deepcopy(records), 'result': result})
        observed = {**frame, 'evidence_id': evidence_id}
        key = (self.task_id, self.generation, stage, frame['context_id'],
               frame['candidate_id'], evidence_id)
        self.observed_frames[key] = deepcopy(observed)
        return observed

    def packet_advance(self, action, *, actor='executor-A', **fields):
        self.sync()
        return {'schema': 'stride-execution/v1', 'operation': 'advance', 'input': {
            'action': action, 'request_id': self.identity('advance'),
            'expected_revision': self.revision, 'task_id': self.task_id,
            'expected_generation': self.generation, 'expected_contract_revision': self.contract_revision,
            'actor_id': actor, 'source': self.source(), 'entry_tree': self.baseline, **fields}}

    def advance(self, packet):
        value = packet['input']
        # Save intended inputs before the subject can return any semantic facts.
        expected = None
        if value['action'] == 'next':
            state = self.sync()
            frame_key = (self.task_id, value['expected_generation'], state['current_stage'],
                value['proof']['context_id'], value['proof']['candidate_id'], value['proof']['evidence_id'])
            frame = self.observed_frames.get(frame_key)
            if frame is not None:
                performer = self.presentations[(value['expected_generation'], frame['stage_id'],
                    frame['iteration'], frame['context_id'], frame['candidate_id'])]
                expected = {'stage_id': frame['stage_id'], 'iteration': frame['iteration'],
                    'source_generation': value['expected_generation'],
                    'source_actor_ids': list(dict.fromkeys([performer, value['actor_id']])),
                    'gate_id': frame['gate_id'], 'evidence_id': frame['evidence_id'],
                    'outcome': value['proof']['outcome']}
        code, result = invoke(self.config, packet)
        LOG.append({'packet': packet, 'exit_code': code, 'response': result})
        if code == 2 and result.get('reason') == DATA['unknown_operation']:
            raise MissingPublicAdvance()
        if code == 0:
            assert set(result) == set(DATA['response_fields']), result
            assert (result['task_id'], result['generation'], result['contract_revision']) == (
                value['task_id'], value['expected_generation'], value['expected_contract_revision']), result
            assert result['revision'] == packet['input']['expected_revision'] + 1 or result['replayed']
            for item in result['traversed']:
                assert set(item) == set(DATA['traversal_fields']), item
            self.revision = result['revision']
            if not result['replayed']:
                if value['action'] == 'next' and result['traversed']:
                    assert expected is not None and len(result['traversed']) == 1
                    item = result['traversed'][0]
                    assert isinstance(item['visit_id'], str) and item['visit_id']
                    # Only the opaque identity comes from the owner. All of its
                    # associations come from input preparation above.
                    origin = dict(expected, visit_id=item['visit_id'])
                    assert item['visit_id'] not in self.origins_by_visit
                    assert_traversal(item, origin, frame)
                    self.origins_by_visit[item['visit_id']] = origin
                    self.origin[origin['stage_id']] = origin
                    self.current_proofs = {frame['stage_id']: frame}
                elif value['action'] == 'fast_forward':
                    self.current_proofs = {}
                    for proof in value['proofs']:
                        key = (self.task_id, value['expected_generation'], proof['stage_id'],
                            proof['context_id'], proof['candidate_id'], proof['evidence_id'])
                        if key in self.observed_frames:
                            self.current_proofs[proof['stage_id']] = self.observed_frames[key]
                self.assert_traversals(result)
                self.assert_public_provenance(result)
        return code, result

    def assert_traversals(self, result):
        for item in result['traversed']:
            origin = self.origins_by_visit[item['visit_id']]
            assert_traversal(item, origin, self.current_proofs[item['stage_id']])

    def assert_public_provenance(self, result):
        if not result['traversed']:
            return
        view = self.progression()
        # The query may preserve its ordinary record envelope; its semantic
        # associations must still be the actual prepared ones.
        def facts(items):
            return [item['data'] if 'data' in item else item for item in items]
        visits, traversals = facts(view['accepted_visits']), facts(view['traversals'])
        for item in result['traversed']:
            expected = self.origins_by_visit[item['visit_id']]
            visit = next(v for v in visits if v['visit_id'] == expected['visit_id'])
            assert {key: visit[key] for key in expected} == expected
            assert any(all(row.get(key) == value for key, value in item.items())
                       for row in traversals), (item, traversals)

    def next(self, frame, *, outcome=None, actor=None):
        stage = frame['stage_id']
        proof = {k: frame[k] for k in ['context_id', 'candidate_id', 'evidence_id']}
        proof['outcome'] = DATA['outcomes'][stage] if outcome is None else outcome
        packet = self.packet_advance('next', actor=DATA['actors'][stage] if actor is None else actor, proof=proof)
        code, result = self.advance(packet)
        assert code == 0, result
        return result

    def perform(self, stage, *, outcome=None, decision='accepted', tag='shared'):
        frame = self.gate_ref(stage, tag=tag)
        self.act('present', actor_id=DATA['actors'][stage], context_id=frame['context_id'],
                 candidate_id=frame['candidate_id'])
        frame = self.observe(frame, replay=False, decision=decision)
        if self.first_evidence is None:
            self.first_evidence = deepcopy(frame)
        result = self.next(frame, outcome=outcome)
        return result

    def restart(self, *, contract=None, definition=None):
        desired = deepcopy(self.contract if contract is None else contract)
        if definition is not None:
            changes = [{'op': 'put_requirement', 'value': requirement}
                       for requirement in definition['content_contract']['requirements']]
            code, published = invoke(self.config, edit({
                'goal_type': definition['goal_type'],
                'process_revision': desired['process_ref']['revision']}, changes,
                request=self.identity('process'), revision=self.sync_revision()))
            assert code == 0, published
            desired['process_ref'] = {'goal_type': definition['goal_type'], 'revision': published['process_revision']}
            self.definition = deepcopy(definition)
            self.revision = published['revision']
        self.act('propose_restart', actor_id='executor-C', proposal_id='proposal-1',
            contract=desired, requirements_snapshot=self.initial_snapshot, reason='Согласовать текущие условия.')
        approval = self.approve(actor='reviewer-D')
        self.act('restart', proposal_id='proposal-1', approval_id=approval)
        self.contract = desired
        self.freeze()
        self.enter()

    def sync_revision(self):
        self.sync()
        return self.revision

    def historical(self, *, review=True, finish=False):
        self.prepared()
        self.perform('write')
        if review:
            self.perform('review')
        if finish:
            self.perform('finish')
        self.restart()
        return self

    def current(self, stages=('write', 'review', 'finish')):
        return [self.observe(self.gate_ref(s, iteration=1)) for s in stages]

    def ff_packet(self, proofs, *, target=None, actor='executor-A'):
        return self.packet_advance('fast_forward', actor=actor, target_stage=target,
            proofs=[{k: p[k] for k in ['stage_id', 'context_id', 'candidate_id', 'evidence_id']} for p in proofs])

    def ff(self, proofs, *, target=None, actor='executor-A'):
        code, result = self.advance(self.ff_packet(proofs, target=target, actor=actor))
        assert code == 0, result
        return result

    def denied(self, packet, *tokens):
        before = self.state()
        code, result = self.advance(packet)
        assert code == 2 and result['status'] == 'rejected', result
        for token in tokens:
            assert token.lower() in result['reason'].lower(), result
        assert self.state() == before
        return result

    def preserved_after(self, before_records, before_physical, before_neighbors):
        assert all(row in self.records() for row in before_records)
        assert self.physical() == before_physical
        assert self.neighbors() == before_neighbors


def install_fault(database):
    with sqlite3.connect(database) as db:
        for sql in DATA['fault_triggers']:
            db.execute(sql)


def clear_fault(database):
    with sqlite3.connect(database) as db:
        db.execute('DROP TRIGGER fail_progress_record')
        db.execute('DROP TRIGGER fail_progress_cursor')


class CorrectionSubject(AdvanceSubject):
    """Actual public producers for the four independently confirmed regressions."""
    def __init__(self, root, *, subject_check=False, entry=False):
        global DATA
        original = DATA
        variant = deepcopy(original)
        if subject_check:
            variant['definition']['stages'][0].update(
                handler='check', transitions=deepcopy(original['corrections']['subject_transitions']))
            variant['evidence_plan']['write'] = deepcopy(original['corrections']['subject_plan'])
            variant['evidence_plan']['review']['review_arguments'] = []
        if entry:
            variant['definition']['content_contract']['requirements'].append(
                deepcopy(original['corrections']['entry_requirement']))
        try:
            DATA = variant
            super().__init__(root)
        finally:
            DATA = original
        self.entry_required = entry
        self.handoff_file = self.store.gates.files.roots['task'] / 'handoff.txt'

    def gate_ref(self, stage, *, tag='correction', iteration=None, files=None):
        if not self.entry_required:
            return super().gate_ref(stage, tag=tag, iteration=iteration, files=files)
        state = self.sync()
        iteration = state['iteration'] if iteration is None else iteration
        context, candidate = self.identity('entry-context'), self.identity('entry-candidate')
        declaration = {'action': 'declare', 'request_id': self.identity('entry-declare'),
            'expected_revision': self.revision, 'task_id': self.task_id, 'generation': self.generation,
            'context_id': context, 'contract_revision': self.contract_revision,
            'requirement_ids': ['report'], 'task_content': {'sections': [], 'routes': [], 'requirements': []},
            'stage_contracts': [{'stage_id': node['id'], 'allowed_paths': node['allowed_paths'],
                'entry_requirements': ['entry-arrival'] if node['id'] == 'review' else [],
                'exit_requirements': ['filled'],
                'criteria': {'requirements': [], 'definition_of_ready': [], 'definition_of_done': []}}
                for node in self.definition['stages']]}
        code, declared = invoke(self.config, {'schema': 'stride-execution/v1', 'operation': 'gate',
                                             'input': declaration})
        assert code == 0, declared
        paths = [str(self.handoff_file)] if self.handoff_file.is_file() else []
        evaluation = {'action': 'evaluate', 'request_id': self.identity('entry-evaluate'),
            'expected_revision': declared['revision'], 'task_id': self.task_id, 'generation': self.generation,
            'context_id': context, 'candidate_id': candidate, 'stage_id': stage, 'iteration': iteration,
            'sections': {'report': DATA['reports'][stage]}, 'trace': {},
            'files': paths if files is None else files}
        code, evaluated = invoke(self.config, {'schema': 'stride-execution/v1', 'operation': 'gate',
                                             'input': evaluation})
        LOG.append({'entry_gate': evaluation, 'exit_code': code, 'response': evaluated})
        assert code == 0 and evaluated['accepted'], evaluated
        self.revision = evaluated['revision']
        return {'stage_id': stage, 'context_id': context, 'candidate_id': candidate,
                'iteration': iteration, 'gate_id': evaluated['result_id']}

    def handoff(self):
        self.handoff_file.parent.mkdir(parents=True, exist_ok=True)
        self.handoff_file.write_text('Проверяемый результат автора.\n')

    def subject_observation(self, frame, *, guard=False, capture=None):
        """Execute the declared real child, without a production assessment oracle."""
        self.sync()
        before = self.source()
        method = self.contract['methods'][0]
        actual = subprocess.run(method['argv'], cwd=method['cwd'],
            env=dict(os.environ, **method['environment']), capture_output=True)
        after = self.source()
        raw = actual.stdout
        complete = capture is None or len(raw) <= capture
        text = raw.decode() if capture is None else raw[:capture].decode()
        passed = (complete and actual.returncode == method['expected_exit_code']
            and all(x in text for x in method['stdout_contains'])
            and all(x in actual.stderr.decode() for x in method['stderr_contains']) and before == after)
        receipt = {'id': self.identity('actual-subject'), 'method': method['id'],
            'obligations': [method['id']], 'argv': method['argv'], 'cwd': method['cwd'],
            'environment': method['environment'], 'actual_exit_code': actual.returncode,
            'stdout': text, 'stderr': actual.stderr.decode(), 'stdout_bytes_hex': raw.hex(),
            'stderr_bytes_hex': actual.stderr.hex(), 'capture_complete': complete,
            'interpretable': True, 'timed_out': False, 'guard': guard, 'passed': passed,
            'commit': before['commit'], 'tree': before['tree'], 'source_before': before,
            'source_after': after, 'source_unchanged': before == after}
        attempt_id = self.identity('subject-attempt')
        records = [
            {'kind': 'attempt', 'id': attempt_id, 'task_id': self.task_id, 'generation': self.generation,
             'revision': 1, 'data': {'status': 'completed', 'stage_id': frame['stage_id'],
                'iteration': frame['iteration'], 'context_id': frame['context_id'],
                'candidate_id': frame['candidate_id'], 'source': before, 'methods': [method],
                'execution_key': attempt_id, 'receipts': [receipt]}},
            {'kind': 'evidence', 'id': self.identity('subject-evidence'), 'task_id': self.task_id,
             'generation': self.generation, 'revision': 1, 'data': {'attempt_id': attempt_id,
                'evidence_work': {'phase': 'continue', 'arguments': [], 'decisions': []}}}]
        LOG.append({'actual_child': deepcopy(receipt)})
        return self.write_proof(frame, records)

    def write_proof(self, frame, records):
        self.sync()
        written = self.store.write_records(request_id=self.identity('correction-facts'),
            expected_revision=self.revision, records=records)
        self.revision = written['revision']
        LOG.append({'public_records': deepcopy(records), 'result': written})
        evidence = next(item for item in records if item['kind'] == 'evidence')
        observed = dict(frame, evidence_id=evidence['id'])
        key = (self.task_id, self.generation, frame['stage_id'], frame['context_id'],
               frame['candidate_id'], evidence['id'])
        self.observed_frames[key] = deepcopy(observed)
        return observed

    def raw_advance(self, packet):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env['PATH']
        actual = subprocess.run([str(APP / 'bin/execution'), '--config', str(self.config)],
            input=json.dumps(packet), cwd=self.config.parent, text=True, capture_output=True, env=env)
        try:
            response = json.loads(actual.stdout)
        except json.JSONDecodeError:
            response = None
        LOG.append({'raw_advance': packet, 'exit_code': actual.returncode,
            'stdout': actual.stdout, 'stderr': actual.stderr, 'response': response})
        return actual.returncode, response, actual.stderr
