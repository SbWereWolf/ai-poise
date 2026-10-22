"""File-proof contract through real WorkTools, SQLite, Git and current files.

The fixture participants exercise role gates; they are not engineering reviewers.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from poise.application.work import WorkTools
from conftest import WorkPoise
from tests.batch.helpers import request
from tests.evidence.file_proof_support import Scenario, fixture, require


def current(s):
    return s.show()['arguments']


def correction(kind='author_unreliable'):
    return {'kind': kind, 'reason': 'Исходное обоснование недостоверно.'}


def test_registers_file_metadata_and_requirement_links(project):
    s = Scenario(project)
    s.register('A')
    proof, = current(s)
    assert set(proof) == set(fixture('metadata-fields.json'))
    for field, value in s.argument().items():
        assert proof[field] == value
    assert (proof['state'], proof['stage'], proof['iteration']) == ('presented', 'measure', 1)
    assert proof['tree'] and s.read()['content'] == 'A\r\n'
    assert s.show()['decisions'] == []


def test_several_and_additional_proofs_preserve_paths(project):
    s = Scenario(project, first='B')
    s.refine('accepted')
    before = deepcopy(current(s))
    s.write('C', b'C')
    response = s.submit([s.argument('C', claim='Отдельное доказательство C.')], required_contract=True)
    assert response['status'] == 'verified'
    after = current(s)
    assert after[:2] == before
    assert [p['id'] for p in after] == ['B', 'A', 'C']
    assert len({p['path'] for p in after}) == 3
    assert len({p['claim'] for p in after}) == 3
    assert s.read('B')['content'] == 'B\r\n'
    assert s.read('C')['content'] == 'C'


def test_inline_argument_payload_is_rejected(project):
    s = Scenario(project)
    s.refused(lambda: s.submit([fixture('inline-argument.json')]), 'proof_argument_invalid')


def test_file_proof_bytes_are_not_snapshotted_in_task_book(project):
    s = Scenario(project)
    s.register('A')
    with s.runtime.store.transaction() as db:
        stored = json.loads(db.execute(
            'SELECT data FROM task_proofs WHERE task_id=?', ('T1',)).fetchone()['data'])
    raw = json.dumps(stored, ensure_ascii=False)
    assert 'A\\r\\n' not in raw
    proof, = current(s)
    assert not {'body', 'content', 'digest', 'revision', 'snapshot', 'content_hash'} & set(proof)
    assert s.read()['content'] == 'A\r\n'


def test_proof_cannot_share_an_existing_ordinary_artifact_path(project):
    s = Scenario(project)
    made = s.invoke('artifacts', {'items': [
        {'scope': 'task', 'path': 'ordinary.json', 'source': {'kind': 'text', 'text': 'ordinary'}}]})
    path = Path(made['artifact_paths'][0])
    before = path.read_bytes()
    s.refused(lambda: s.submit([s.argument(path=str(path))]), 'proof_file_ordinary_artifact')
    assert path.read_bytes() == before


def test_proof_cannot_reclassify_machine_material(project):
    s = Scenario(project, first=None, machine=True)
    observed = s.submit()
    require(observed['status'] == 'verified' and len(observed['checks']) == 1,
            'Real machine receipt did not complete')
    receipt = observed['checks'][0]
    require(receipt['passed'] is True, 'Actual machine receipt failed')
    path = Path(receipt['stdout'])
    before = path.read_bytes()
    s.transfer()
    require(s.submit()['stage_outcome'] == 'clear', 'Empty real audit failed')
    s.transfer()
    s.refused(lambda: s.submit([s.argument(path=str(path))]), 'proof_file_managed_material')
    assert path.read_bytes() == before


def test_reviewer_rejection_is_corrected_at_same_path(project):
    s = Scenario(project)
    s.register('A')
    old_path = current(s)[0]['path']
    s.transfer()
    rejected = s.submit(decisions=[s.decide(decision='rejected')])
    assert rejected['stage_outcome'] == 'changes_requested'
    s.transfer()
    assert s.context['stage'] == 'measure' and s.context['iteration'] == 2
    s.write(data=b'Corrected A')
    response = s.submit([s.argument(claim='Исправленное A.', correction=correction('reviewer_rejected'))], required_contract=True)
    assert response['status'] == 'verified'
    s.transfer()
    assert s.read()['content'] == 'Corrected A'
    assert s.submit(decisions=[s.decide()])['stage_outcome'] == 'clear'
    proof, = current(s)
    assert (proof['path'], proof['state']) == (old_path, 'accepted')
    assert [d['decision'] for d in s.show()['decisions']] == ['rejected', 'accepted']


def test_author_unreliability_reopens_accepted_proof_at_same_path(project):
    s = Scenario(project, first='B')
    s.refine('accepted')
    prior = deepcopy(current(s))
    s.write(data=b'Author corrected A')
    response = s.submit([s.argument(claim='Уточнённое A.', correction=correction())], required_contract=True)
    assert response['status'] == 'verified'
    after = current(s)
    assert after[0] == prior[0]
    assert after[1]['path'] == prior[1]['path'] and after[1]['state'] == 'presented'
    s.transfer()
    assert s.read()['content'] == 'Author corrected A'
    assert s.submit(decisions=[s.decide()])['stage_outcome'] == 'clear'
    assert current(s)[1]['state'] == 'accepted'
    assert len([d for d in s.show()['decisions'] if d['argument_id'] == 'A']) == 2


def test_repeated_submission_and_decision_are_idempotent(project):
    s = Scenario(project)
    s.register('A')
    before = s.business()
    repeated = s.submit([s.argument()], required_contract=True)
    assert repeated['replayed'] is True and s.business() == before
    s.transfer()
    s.submit(decisions=[s.decide()])
    before = s.business()
    repeated = s.submit(decisions=[s.decide()])
    assert repeated['replayed'] is True and s.business() == before
    assert len(s.show()['decisions']) == 1


def test_changed_existing_proof_metadata_requires_correction_reason(project):
    s = Scenario(project, first='B')
    s.refine('accepted')
    s.refused(lambda: s.submit([s.argument(claim='Иное утверждение.')]), 'proof_correction_required')


def test_reviewer_checks_current_file_before_acceptance(project):
    s = Scenario(project)
    s.register('A')
    s.transfer()
    s.path().unlink()
    s.refused(lambda: s.submit(decisions=[s.decide()]), 'proof_file_missing')
    assert current(s)[0]['state'] == 'presented'


@pytest.mark.parametrize('variant', [
    'first-presentation', 'unrejected', 'accepted', 'empty-reason', 'malformed-reason'])
def test_invalid_correction_is_rejected_without_effects(project, variant):
    cases = fixture('reviewed-denial-cases.json')
    case = next(c for c in cases if c['nodeid'].endswith('[' + variant + ']'))
    s = Scenario(project, first='B')
    s.refine(case['prior_A']['state'])
    s.write(data=b'Caller-authored replacement')
    argument = deepcopy(case['literal_argument'])
    argument['path'] = str(s.path())
    before = deepcopy(current(s))
    s.refused(lambda: s.submit([argument]), 'proof_correction_invalid')
    assert current(s) == before
    assert s.path('B').read_bytes() == b'B\r\n'


def test_reader_uses_current_text_bytes(project):
    s = Scenario(project, first='B')
    s.refine('accepted')
    assert s.read()['content'] == 'A\r\n'
    data = 'Новые данные\r\n\x00'.encode('utf-8')
    s.write(data=data)
    s.submit([s.argument(correction=correction())], required_contract=True)
    reply = s.read()
    assert reply['content'] == 'Новые данные\r\n\x00' and reply['size_bytes'] == len(data)


def test_reader_returns_lossless_binary(project):
    s = Scenario(project)
    s.write(data=bytes.fromhex('00ff0d0a41'))
    s.submit([s.argument()], required_contract=True)
    response = s.read(representation='base64')
    assert response['content'] == 'AP8NCkE=' and response['size_bytes'] == 5


def test_reader_preserves_empty_file(project):
    s = Scenario(project)
    s.write(data=b'')
    s.submit([s.argument()], required_contract=True)
    for mode in ('text', 'base64'):
        response = s.read(representation=mode)
        assert response['content'] == '' and response['size_bytes'] == 0


def test_text_representation_reports_invalid_utf8(project):
    s = Scenario(project)
    s.write(data=b'\xff')
    s.submit([s.argument()], required_contract=True)
    s.refused(lambda: s.read(required_contract=False), 'proof_file_decode_error')
    assert s.read(representation='base64')['content'] == '/w=='


@pytest.mark.parametrize('budget', [4096, 8192])
def test_cli_retains_full_read_response_beyond_preview(project, budget, tmp_path):
    s = Scenario(project)
    # Valid feature registration stays in CALL, before the actual CLI boundary.
    body = 'Ж' * 12000 + '\r\nEND'
    s.write(data=body.encode('utf-8'))
    s.submit([s.argument()], required_contract=True)
    cfg = json.loads(project['config_path'].read_text())
    cfg['limits']['output_chars'] = budget
    project['config_path'].write_text(json.dumps(cfg, ensure_ascii=False))
    env = {k: v for k, v in os.environ.items() if k not in (
        'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING', 'POISE_SESSION')}
    source = Path(__file__).resolve().parents[2] / 'src'
    env.update(PYTHONPATH=str(source), POISE_CONFIG=str(project['config_path']),
               POISE_CALLER_BINDING=str(tmp_path / 'read-caller.json'), LANG='C.UTF-8')
    packet = request('show', fixture('test_file_proofs-literal-1.json'))
    # The public CLI selects T1 with its own generated caller; it never reuses
    # the executor native binding. A read-only selection must not take a claim.
    bootstrap = request('bootstrap', {'task': {'id': 'T1'}, 'decision': None,
                                     'feedback': None, 'rework_stage': None})
    # Release the fixture author first, then let the real CLI acquire normally.
    task = s.show('task')
    s.invoke('release', {'task_id': 'T1', 'request_id': 'cli-reader-release',
        'expected_version': task['version'], 'reason': 'Public CLI fixture reader.'})
    command = [sys.executable, '-B', '-m', 'poise', 'work']
    admitted = subprocess.run(command, input=json.dumps(bootstrap), env=env,
        capture_output=True, text=True, timeout=60)
    require(admitted.returncode == 0, admitted.stdout + admitted.stderr)
    response = subprocess.run(command, input=json.dumps(packet), env=env,
        capture_output=True, text=True, timeout=60)
    assert response.returncode == 0, response.stderr
    short = json.loads(response.stdout)
    saved = json.loads(Path(short['response_path']).read_text())
    value = saved['results'][1]['value']
    assert value['content'] == body and value['size_bytes'] == len(body.encode('utf-8'))
    assert len(response.stdout) <= budget and body not in response.stdout
    assert json.loads((tmp_path / 'read-caller.json').read_text())['caller_id']


def test_read_batch_preserves_query_order_and_independent_files(project):
    s = Scenario(project)
    s.register('A', 'B')
    response = s.invoke('show', fixture('test_file_proofs-literal-2.json'))
    assert [r['id'] for r in response['results']] == ['B-first', 'A-second']
    assert [r['value']['content'] for r in response['results']] == ['Qg0K', 'A\r\n']


@pytest.mark.parametrize('variant', ['missing', 'null'])
def test_proof_read_requires_explicit_representation(project, variant):
    cases = fixture('reviewed-denial-cases.json')
    case = next(c for c in cases if c['nodeid'].endswith('[' + variant + ']'))
    s = Scenario(project)
    s.register('A')
    s.read_controls()
    s.refused(lambda: s.read(query=case['literal_query'], required_contract=False),
              'proof_file_representation_invalid')


@pytest.mark.parametrize('variant', ['missing', 'foreign-task', 'directory'])
def test_invalid_proof_file_is_rejected_before_effects(project, tmp_path, variant):
    s = Scenario(project)
    path = s.path()
    prefix = 'proof_file_missing'
    if variant == 'foreign-task':
        path = tmp_path / 'foreign-task' / 'proof.json'
        path.parent.mkdir()
        path.write_bytes(b'FOREIGN SENTINEL')
        prefix = 'proof_file_foreign_task'
    elif variant == 'directory':
        path.mkdir(parents=True)
        prefix = 'proof_file_not_regular'
    s.refused(lambda: s.submit([s.argument(path=str(path))]), prefix)
    if variant == 'foreign-task':
        assert path.read_bytes() == b'FOREIGN SENTINEL'
    if variant == 'directory':
        assert path.is_dir()


def test_unknown_requirement_ref_is_rejected(project):
    s = Scenario(project)
    s.write()
    s.refused(lambda: s.submit([s.argument(covers=['requirements[99]'])]), 'proof_requirement_unknown')


def test_unknown_observation_id_is_rejected(project):
    s = Scenario(project, first='B')
    s.refine('accepted')
    s.refused(lambda: s.submit([s.argument(correction=correction(), observation_ids=['UNKNOWN'])]),
              'proof_observation_invalid')


def test_argument_id_cannot_change_its_path(project):
    s = Scenario(project, first='B')
    s.refine('accepted')
    changed = s.root / 'proofs' / 'changed.json'
    changed.write_bytes(b'NEW PATH')
    s.refused(lambda: s.submit([s.argument(path=str(changed), correction=correction())]),
              'proof_file_path_changed')
    assert changed.read_bytes() == b'NEW PATH'


def test_distinct_arguments_cannot_share_one_path(project):
    s = Scenario(project)
    s.write()
    s.refused(lambda: s.submit([s.argument(), s.argument('B', path=str(s.path()))]),
              'proof_file_path_conflict')


@pytest.mark.parametrize('variant', fixture('test_file_proofs-literal-3.json'))
def test_strict_request_contract(project, variant):
    s = Scenario(project)
    if variant == 'unknown-representation':
        s.register('A')
        s.read_controls()
        s.refused(lambda: s.read(query={
            'id': 'unknown', 'kind': 'proof_file', 'argument_id': 'A',
            'representation': 'unknown'}, required_contract=False), 'proof_file_representation_invalid')
        return
    s.write()
    argument = s.argument()
    prefix = 'proof_argument_invalid'
    arguments = [argument]
    if variant == 'missing-path':
        del argument['path']
    elif variant == 'null-path':
        argument['path'] = None
    elif variant == 'inline-body':
        argument['body'] = 'Inline payload is not a file reference.'
    elif variant == 'duplicate-id':
        arguments.append(deepcopy(argument))
        prefix = 'proof_argument_duplicate'
    elif variant == 'missing-correction':
        del argument['correction']
    s.refused(lambda: s.submit(arguments), prefix)


def test_proof_query_requires_task_without_changing_taskless_tools(project):
    s = Scenario(project)
    tools = WorkTools(WorkPoise(project['config_path'], 'taskless-proof-reader'))
    selected = tools.invoke(request('show', {'queries': [{'id': 'selected', 'kind': 'task'}]}))
    require(selected['results'][0]['value']['status'] == 'read_only'
            and 'task' not in selected['results'][0]['value'], 'Expected genuine taskless view')
    s.refused(lambda: tools.invoke(request('show', {'queries': [
        {'id': 'proof', 'kind': 'proof_file', 'argument_id': 'A', 'representation': 'text'}]})),
        'proof_task_required')
    selected = tools.invoke(request('show', {'queries': [{'id': 'selected', 'kind': 'task'}]}))
    assert selected['results'][0]['value']['status'] == 'read_only'
    assert 'task' not in selected['results'][0]['value']


def test_legacy_inline_history_is_preserved_as_history(project):
    s = Scenario(project)
    legacy = fixture('legacy-book.json')
    # Isolated test data only: no Task lifecycle/claim/native receipt is forged.
    with s.runtime.store.transaction() as db:
        envelope = json.loads(db.execute(
            'SELECT data FROM task_proofs WHERE task_id=?', ('T1',)).fetchone()['data'])
        require('book' in envelope and 'input' in envelope and 'assessment' in envelope,
                'Historical fixture requires the actual unchanged storage envelope')
        envelope['book'] = legacy
        db.execute('UPDATE task_proofs SET data=? WHERE task_id=?',
                   (json.dumps(envelope, ensure_ascii=False), 'T1'))
    view = s.show()
    assert view['arguments'] == [] and view['decisions'] == []
    assert view['historical_arguments'] == legacy['arguments']
    assert view['historical_decisions'] == legacy['decisions']
    result = s.submit()
    assert result['status'] == 'evidence_requirements_failed'
    assert s.show()['historical_arguments'] == legacy['arguments']


def test_saved_read_response_is_not_used_as_current_proof(project, tmp_path):
    s = Scenario(project)
    s.register('A')
    previous = s.read()
    saved = tmp_path / 'previous-response.json'
    saved.write_text(json.dumps(previous, ensure_ascii=False))
    s.path().unlink()
    s.refused(lambda: s.read(required_contract=False), 'proof_file_missing')
    assert json.loads(saved.read_text())['content'] == 'A\r\n'
