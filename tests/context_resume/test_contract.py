"""F06 native behavior: independent effects, current facts and denial witnesses."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from poise.common import PoiseError
from tests.content_reader.helpers import Scenario, files, other_project
from tests.conftest import write_json
from tests.context_resume.helpers import Recovery, card, fixture, git_identity, readonly_rows


CASES = fixture('cases.json')


def cycle(r):
    r.save(); r.compact()
    value = r.restore()
    assert value['checkpoint']['state'] == 'restored'
    assert value['generation'] == {'before': 1, 'current': 2}
    return value


def failure_card(value, state):
    assert value['checkpoint']['state'] == state
    assert value['status'] == 'partial'
    assert value['actions'], 'An unavailable source needs an executable continuation'
    assert value['git']['head']


def capture_all(r):
    r.s.read(start=1, end=2)
    observed = r.instructions_now()
    stored = r.save()
    head, status = git_identity(r.s.checkout)
    assert stored['captured']['git']['head'] == head
    assert stored['captured']['git']['status_sha256'] == status
    assert stored['captured']['coverage']['generation'] == 1
    assert stored['captured']['coverage']['ref'] == str(r.s.db)
    records = stored['captured']['instructions']['materials']
    expected = [{k: m[k] for k in ('kind', 'id', 'root', 'path', 'scopes', 'required',
                                  'reasons', 'available', 'sha256')}
                for m in observed['materials']]
    # These are current-owner identities, not an oracle for the comparison algorithm.
    assert records == expected
    assert [(m['kind'], m['id']) for m in records] == [
        ('rule', 'AGENTS.md'), ('skill', 'core'), ('skill', 'domain')]
    assert all('content' not in m for m in records)
    assert stored['captured']['check']['status'] == 'not_configured'
    assert stored['event']['kind'] == 'PreCompact'
    assert stored['consumption']['state'] == 'pending'


def taskless_capture(r, project):
    h = r.work_runtime()
    before = deepcopy(h.current_task())
    assert before is None
    r.save(); r.compact()
    result = r.restore()
    assert result['work']['status'] == 'not_configured'
    assert h.current_task() is None
    assert result['coverage']['ref'] == str(r.s.db)
    # The enabled healthy source is a real current Task, not a fabricated DTO.
    h.bootstrap(project['task'])
    before = deepcopy(h.current_task())
    options = deepcopy(r.options); options['work_owner']['enabled'] = True
    r.configure(options)
    current = r.restore('resume')
    assert current['work']['status'] == 'available'
    assert current['work']['task'] == 'T1' and current['work']['stage'] == 'tests'
    assert set(current['work']) == {'status', 'task', 'stage', 'ref'}
    for requirement in project['task']['requirements']:
        assert requirement not in r.outputs[-1]['hookSpecificOutput']['additionalContext']
    assert h.current_task() == before


def replace_checkpoint(r):
    r.save(); before = r.checkpoint_bytes(); sentinel = r.sentinel()
    r.s.write('docs/new.txt', b'new current work\n')
    r.save()
    assert r.checkpoint_bytes() != before
    assert sentinel.read_bytes() == b'FOREIGN-F06-SENTINEL\n'
    assert list(r.checkpoint.parent.iterdir()) == [r.checkpoint]
    assert strict_checkpoint(r)['consumption']['state'] == 'pending'


def strict_checkpoint(r):
    return json.loads(r.checkpoint.read_bytes())


def capture_write_failure(r, monkeypatch):
    r.save(); baseline = r.checkpoint_bytes(); reached = []
    real = os.replace
    with monkeypatch.context() as scoped:
        def failing(source, target, *args, **kwargs):
            if Path(target) == r.checkpoint:
                reached.append(str(target))
                raise OSError('F06_ATOMIC_CHECKPOINT_FAILURE')
            return real(source, target, *args, **kwargs)
        scoped.setattr(os, 'replace', failing)
        r.cycle += 1
        output = r.event('PreCompact')
        assert 'checkpoint_write_failed' in json.dumps(output)
        assert reached, 'The failed storage boundary must actually be used'
        assert r.checkpoint_bytes() == baseline
        saved_delivery = r.delivery()
    r.retry(saved_delivery)
    assert strict_checkpoint(r)['event']['id'] == r.events[-1]['id']
    assert r.checkpoint_bytes() != baseline


def post_then_start(r):
    assert cycle(r)['generation']['current'] == r.generation() == 2
    assert len(r.transitions()) == 2  # One explicit fixture seed and one native transition.


def exact_post_replay(r):
    r.save(); r.compact(); before = r.transitions(); delivery = r.delivery()
    r.retry(delivery)
    assert r.generation() == 2 and r.transitions() == before
    packet = json.loads(delivery.read_text())
    original = delivery.read_bytes()
    packet['packet']['native_event']['trigger'] = 'different-meaning'
    delivery.write_text(json.dumps(packet))
    try:
        with pytest.raises(PoiseError) as caught:
            r.retry(delivery)
        assert str(caught.value) == fixture()['event_conflict_error']
        assert r.generation() == 2 and r.transitions() == before
    finally:
        delivery.write_bytes(original)


def compact_without_post(r):
    r.save()
    value = r.restore()
    assert value['generation'] == {'before': 1, 'current': 2}
    assert r.generation() == 2
    assert not any(e['kind'] == 'PostCompact' for e in r.events)
    assert len(r.transitions()) == 2


def exact_start_replay(r):
    expected = cycle(r); before = r.transitions(); cp = r.checkpoint_bytes()
    assert card(r.retry()) == expected
    assert r.transitions() == before and r.checkpoint_bytes() == cp


def used_next_resume(r):
    cycle(r)
    r.s.write('docs/after.txt', b'next real observation\n')
    expected_head, expected_status = git_identity(r.s.checkout)
    value = r.restore('resume')
    assert value['checkpoint']['state'] == 'used'
    assert value['git']['head'] == expected_head
    assert value['git']['status_sha256'] == expected_status
    assert value['generation']['current'] == r.generation() == 2


def clear_own(r):
    r.save(); sentinel = r.sentinel()
    value = r.restore('clear')
    assert not r.checkpoint.exists()
    assert value['checkpoint']['state'] == 'missing'
    assert value['generation']['current'] == r.generation() == 2
    assert sentinel.read_bytes() == b'FOREIGN-F06-SENTINEL\n'


def worktree_change(r):
    subprocess.run(['git', '-C', str(r.s.checkout), 'add', '--', 'src/billing/invoice.py'],
                   check=True, capture_output=True)
    subprocess.run(['git', '-C', str(r.s.checkout), 'commit', '-m', 'Track context fixture input'],
                   check=True, capture_output=True)
    r.save(); initial = git_identity(r.s.checkout)
    r.s.write('src/billing/invoice.py', b'VALUE = 7\n')
    r.compact(); expected = git_identity(r.s.checkout); value = r.restore()
    assert expected[0] == initial[0] and expected[1] != initial[1]
    assert value['git']['head'] == expected[0]
    assert value['git']['status_sha256'] == expected[1]
    assert 'src/billing/invoice.py' in value['git']['changed_paths']


def instruction_change(r):
    r.save()
    r.s.write('.agents/skills/core/SKILL.md', b'# Changed core\n')
    (r.s.checkout / '.agents/skills/domain/SKILL.md').unlink()
    r.compact(); value = r.restore()
    assert value['instructions']['status'] == 'needs_inputs'
    assert set(value['instructions']['changed']) == {
        '.agents/skills/core/SKILL.md', '.agents/skills/domain/SKILL.md'}
    assert any('restore' in json.dumps(a) for a in value['actions'])
    assert fixture()['body_marker'] not in json.dumps(value)


def shared_coverage(r):
    r.s.read(start=1, end=1); before = r.s.db
    r.save(); r.compact(); value = r.restore()
    assert value['coverage']['generation'] == 2
    assert value['coverage']['ref'] == str(before) and r.s.db == before
    actual = r.s.read(start=1, end=1)
    assert actual['context']['generation'] == 2
    assert actual['items'][0]['status'] == 'content'
    assert actual['items'][0]['classification'] == 'new_context_generation'
    assert actual['items'][0]['content_bytes'] == 4


def current_check(r, monkeypatch, project):
    from poise.runtime import Poise
    h = r.work_runtime()
    task_context = h.bootstrap(project['task'])
    actual_task = deepcopy(h.current_task())
    actual_tree = subprocess.check_output(['git', '-C', actual_task['worktree'],
                                          'rev-parse', 'HEAD^{tree}'], text=True).strip()
    original = Poise.context_metadata
    state = {'pending': deepcopy(fixture()['work_pending'])}
    reached = []
    # A controlled consumer input in the CURRENT pending schema. It is not a
    # fabricated Task receipt, DB mutation, live process or producer acceptance.
    def observed(owner, facts, *args, **kwargs):
        result = original(owner, facts, *args, **kwargs)
        reached.append(deepcopy(state['pending']))
        pending = deepcopy(state['pending'])
        pending.update(actor=owner.session, task_version=result['task']['version'],
                       verified_tree=actual_tree)
        if pending['runs'][0]['termination'] is not None:
            pending['runs'][0]['termination'].update(actor=owner.session, tree=actual_tree)
        result['task']['pending'] = pending
        return result
    monkeypatch.setattr(Poise, 'context_metadata', observed)
    options = deepcopy(r.options)
    options['work_owner']['enabled'] = options['check_owner']['enabled'] = True
    r.configure(options)
    saved = r.save(); assert reached
    old = fixture()['work_pending']
    old_run = old['runs'][0]['run_id']
    run_root = Path(task_context['task_root']) / h.paths['runs']
    assert saved['captured']['check']['run_id'] == old_run
    assert saved['captured']['check']['attempt_id'] == old['attempt_id']
    assert saved['captured']['check']['ref'] == str(run_root / old_run)
    state['pending'] = deepcopy(fixture()['work_completed'])
    current = state['pending']
    current_run = current['runs'][0]['run_id']
    assert current_run != old_run and current['attempt_id'] != old['attempt_id']
    r.compact(); value = r.restore()
    assert reached[-1] == state['pending']
    assert_current_check(value['check'], current, old, run_root)
    assert 'PASS' not in json.dumps(value)
    assert h.current_task() == actual_task


def assert_current_check(actual, current, old, run_root):
    current_run = current['runs'][0]['run_id']
    assert current_run != old['runs'][0]['run_id']
    assert current['attempt_id'] != old['attempt_id']
    assert actual['run_id'] == current_run
    assert actual['attempt_id'] == current['attempt_id']
    assert actual['ref'] == str(run_root / current_run)
    assert actual['run_id'] != old['runs'][0]['run_id']
    assert actual['attempt_id'] != old['attempt_id']
    assert actual['ref'] != str(run_root / old['runs'][0]['run_id'])
    assert actual['status'] == 'completed'
    assert 'stdout' not in actual


def same_anchor(r):
    original = deepcopy(r.s.episode); r.s.read(start=1, end=2)
    cycle(r)
    value = r.restore('resume')
    assert value['scope']['episode'] == original == r.s.context()['context']['native_episode']
    assert r.s.db.exists()


def seed_scope_read(r, vector='scope_read'):
    expected = fixture()[vector]
    r.s.write(expected['path'], expected['text'].encode())
    actual = r.s.read(path=expected['path'], key=expected['key'], start=1, end=1)
    assert actual['items'][0]['status'] == 'content'
    assert actual['items'][0]['classification'] == 'first_read'
    observed = r.i.coverage()
    selected = [d for d in observed['details']['documents'] if d['path'] == expected['path']]
    assert len(selected) == 1 and selected[0]['ranges'] == [[1, 1]]
    return expected['path'], observed['details']


def coverage_rows(database, path, namespace=None):
    if namespace is None:
        found = readonly_rows(database,
            'SELECT DISTINCT session_id FROM content_read_cache WHERE path_label=?', (path,))
        assert len(found) == 1, 'The seeded read must have one actual source namespace'
        namespace = found[0][0]
    cache = readonly_rows(database,
        'SELECT epoch,path_label,content_sha256,normalized_range,reader_kind,'
        'content_bytes,content_lines,first_read_at FROM content_read_cache '
        'WHERE session_id=? AND path_label=? ORDER BY rowid', (namespace, path))
    events = readonly_rows(database,
        'SELECT epoch,occurred_at,path_label,content_sha256,normalized_range,reader_kind,'
        'status,classification,content_bytes,content_lines,model_content_bytes '
        'FROM content_read_events WHERE session_id=? AND path_label=? ORDER BY rowid',
        (namespace, path))
    assert cache and events
    return namespace, cache, events


def assert_scope_coverage(coverage, *, present=None, absent=None):
    text = json.dumps(coverage)
    if present is not None:
        assert present in text, 'Positive control must expose its actual own read metadata'
    if absent is not None:
        assert absent not in text, 'A different logical scope must not inherit the read'


def assert_empty_generation_coverage(value, generation):
    assert value['context']['generation'] == generation
    assert value['details']['documents'] == []
    assert value['details']['counts']['current_documents'] == 0


def project_session_isolation(r, project, tmp_path, monkeypatch):
    path, own_details = seed_scope_read(r)
    assert_scope_coverage(r.restore('resume')['coverage'], present=path)
    r.save(); own = r.checkpoint_bytes()
    own_rows = coverage_rows(r.s.db, path)
    foreign = other_project(project, tmp_path)
    # Hold the external session identity fixed to exercise the project boundary.
    foreign.native_identity(r.s.identity)
    peer_root = tmp_path / 'peer'; peer_root.mkdir()
    peer = Recovery(foreign, peer_root, monkeypatch).enable()
    assert peer.s.identity == r.s.identity and peer.s.project != r.s.project
    assert_scope_coverage(peer.i.coverage()['details'], absent=path)
    assert_scope_coverage(peer.restore('resume')['coverage'], absent=path)
    peer_path, _ = seed_scope_read(peer, 'scope_peer_read')
    assert_scope_coverage(peer.restore('resume')['coverage'], present=peer_path, absent=path)
    peer_rows = coverage_rows(peer.s.db, peer_path)
    peer.save(); peer.compact(); value = peer.restore()
    assert value['scope']['project'] != r.s.project
    assert_scope_coverage(value['coverage'], absent=path)
    current = peer.i.coverage()
    assert_empty_generation_coverage(current, 2)
    assert_scope_coverage(current['details'], absent=path)
    assert coverage_rows(peer.s.db, peer_path, peer_rows[0]) == peer_rows
    assert r.i.coverage()['details'] == own_details
    assert coverage_rows(r.s.db, path, own_rows[0]) == own_rows
    assert r.checkpoint_bytes() == own
    assert peer.checkpoint != r.checkpoint


def checkout_isolation(r):
    path, own_details = seed_scope_read(r)
    assert_scope_coverage(r.restore('resume')['coverage'], present=path)
    r.save(); original = r.s.checkout; original_cp = r.checkpoint; before = original_cp.read_bytes()
    own_rows = coverage_rows(r.s.db, path)
    second = r.parent / 'second-checkout'
    subprocess.run(['git', '-C', str(original), 'worktree', 'add', '-b', 'f06-second', str(second)],
                   check=True, capture_output=True)
    r.s.checkout = second
    try:
        r.s.write('docs/sample.txt', fixture()['source_text'].encode())
        r.s.context()  # Current owner, not a guessed checkout key.
        value = r.restore('resume')
        assert value['scope']['checkout'] == str(second)
        assert value['checkpoint']['state'] == 'missing'
        assert_scope_coverage(value['coverage'], absent=path)
        assert_scope_coverage(r.i.coverage()['details'], absent=path)
        # The same file/range is a FIRST read in B, not inherited coverage from A.
        seed_scope_read(r)
        assert_scope_coverage(r.restore('resume')['coverage'], present=path)
        assert r.checkpoint != original_cp
        assert original_cp.read_bytes() == before
        assert coverage_rows(r.s.db, path, own_rows[0]) == own_rows
    finally:
        r.s.checkout = original
    assert r.i.coverage()['details'] == own_details
    assert coverage_rows(r.s.db, path, own_rows[0]) == own_rows


def new_fork(r, monkeypatch):
    from tests.command_intake.helpers import Intake
    from types import SimpleNamespace
    path, own_details = seed_scope_read(r)
    assert_scope_coverage(r.restore('resume')['coverage'], present=path)
    r.save(); before = r.checkpoint_bytes(); source = deepcopy(r.s.episode)
    own_rows = coverage_rows(r.s.db, path)
    value = card(r.event('SessionStart', source='fork', identity='f06-fork'))
    assert value['scope']['episode']['session_id'] == 'f06-fork'
    assert value['scope']['episode'] != source
    assert value['checkpoint']['state'] == 'missing'
    assert_scope_coverage(value['coverage'], absent=path)
    # Read through the same installed owner/policy with its real new native binding.
    root = r.parent / 'fork-reader'; root.mkdir()
    peer = Scenario(r.s.base, root, identity='f06-fork')
    peer.native_identity('f06-fork')
    peer.policy, peer.data = r.s.policy, deepcopy(r.s.data)
    peer.hooks, peer.installation, peer.event_factory = r.service, r.installation, r.s.event_factory
    peer.episode = deepcopy(value['scope']['episode'])
    peer.ready()
    assert peer.context()['context']['native_episode'] == peer.episode
    intake = Intake(peer, root, monkeypatch)
    assert_scope_coverage(intake.coverage()['details'], absent=path)
    seed_scope_read(SimpleNamespace(s=peer, i=intake))
    current = card(r.event('SessionStart', source='resume', identity='f06-fork'))
    assert current['scope']['episode'] == peer.episode
    assert_scope_coverage(current['coverage'], present=path)
    assert r.i.coverage()['details'] == own_details
    assert coverage_rows(r.s.db, path, own_rows[0]) == own_rows
    assert r.checkpoint_bytes() == before


def missing_checkpoint(r):
    assert not r.checkpoint.exists()
    value = r.restore('resume')
    failure_card(value, 'missing')
    assert value['generation']['before'] is None


def invalid_checkpoint(r):
    r.save(); good = r.checkpoint_bytes(); original = strict_checkpoint(r)
    for payload in (b'{broken', json.dumps({**original, 'schema': 'unsupported-context-version'}).encode()):
        r.checkpoint.write_bytes(payload)
        value = r.restore('resume')
        failure_card(value, 'invalid')
        assert r.checkpoint.read_bytes() == payload
    r.checkpoint.write_bytes(good)
    assert r.restore('resume')['checkpoint']['state'] == 'restored'


def unreadable_checkpoint(r, monkeypatch):
    r.save(); original = r.checkpoint_bytes(); reached = []; real = Path.open
    with monkeypatch.context() as scoped:
        def denied(path, mode='r', *args, **kwargs):
            if path == r.checkpoint and 'r' in mode:
                reached.append(str(path)); raise PermissionError('F06_CONTEXT_READ_DENIED')
            return real(path, mode, *args, **kwargs)
        scoped.setattr(Path, 'open', denied)
        value = r.restore('resume')
        assert reached, 'The damaged actual read must reach the consumer'
        failure_card(value, 'unavailable')
    assert r.checkpoint.read_bytes() == original
    assert r.restore('resume')['checkpoint']['state'] == 'restored'


def instruction_unavailable(r, monkeypatch):
    from poise.application.project_instructions import ProjectInstructions
    r.save(); reached = []
    with monkeypatch.context() as scoped:
        def denied(owner, request):
            reached.append(request); raise OSError('F06_INSTRUCTION_OWNER_UNAVAILABLE')
        scoped.setattr(ProjectInstructions, 'prepare', denied)
        value = r.restore('resume')
        assert reached and value['instructions']['status'] == 'unavailable'
        assert value['status'] == 'partial' and value['actions'] and value['git']['head']
    assert r.restore('resume')['instructions']['status'] == 'ready'


def work_plugin_failure(r, monkeypatch):
    from poise.runtime import Poise
    options = deepcopy(r.options); options['work_owner']['enabled'] = True
    r.configure(options); before = deepcopy(r.work_runtime().current_task()); reached = []
    with monkeypatch.context() as scoped:
        def denied(owner, facts):
            reached.append(facts); raise OSError('F06_WORK_OWNER_UNAVAILABLE')
        scoped.setattr(Poise, 'context_metadata', denied)
        value = r.restore('resume')
        assert reached and value['work']['status'] == 'unavailable'
        assert value['git']['head'] and value['coverage']['ref']
        assert value['status'] == 'partial' and value['actions']
    assert r.work_runtime().current_task() == before
    assert r.restore('resume')['work']['status'] == 'available'


def check_unavailable(r, monkeypatch):
    from poise.runtime import Poise
    options = deepcopy(r.options); options['check_owner']['enabled'] = True
    r.configure(options); reached = []
    with monkeypatch.context() as scoped:
        def denied(owner, facts):
            reached.append(facts); raise OSError('F06_CHECK_OWNER_UNAVAILABLE')
        scoped.setattr(Poise, 'context_metadata', denied)
        value = r.restore('resume')
        assert reached and value['check']['status'] == 'unavailable'
        assert value['status'] == 'partial' and value['actions'] and value['git']['head']
    assert r.restore('resume')['check']['status'] == 'not_observed'


def multibyte_budget(r):
    for name in ('документ-один', 'документ-два', 'документ-три', 'документ-четыре'):
        r.s.write('docs/' + name + '.txt', b'metadata only\n')
    r.save(); r.compact(); value = r.restore()
    assert len(value['git']['changed_paths']) == 2
    actual_paths = subprocess.check_output(['git', '-C', str(r.s.checkout), 'status',
        '--porcelain=v1', '-z', '--untracked-files=all']).decode().split('\0')
    count = sum(len(p) > 3 for p in actual_paths)
    assert value['omitted']['changed_paths'] == count - 2
    assert value['checkpoint']['ref'] == str(r.checkpoint)
    assert len(r.outputs[-1]['hookSpecificOutput']['additionalContext'].encode()) <= 4096


def common_budget(r):
    value = cycle(r)
    text = r.outputs[-1]['hookSpecificOutput']['additionalContext']
    assert 'work.sh' in text and 'restore_context' in text and 'Context generation:' in text
    assert value['generation']['current'] == r.generation() == 2
    assert len(text.encode('utf-8')) <= 4096


def invalid_budget(r):
    from poise.infrastructure.hook_transport import HookSettings
    before = r.service.settings.path.read_bytes()
    candidate = deepcopy(r.settings); candidate['general_context']['summary']['max_bytes'] = 1
    with pytest.raises(PoiseError) as caught:
        HookSettings.proposed(r.service.settings.path, candidate)
    assert 'summary' in str(caught.value) or 'budget' in str(caught.value)
    assert r.service.settings.path.read_bytes() == before
    assert 'work.sh' in r.event('SessionStart', source='resume')['hookSpecificOutput']['additionalContext']


def end_cleanup(r):
    cycle(r); target = r.s.resource; sentinel = r.sentinel()
    coordination = Path(r.s.data['session']['coordination_root'])
    template = Path(r.s.data['session']['registry_template']); template_bytes = template.read_bytes()
    r.event('SessionEnd')
    code, value = r.s.invoke({'operation': 'recover'})
    assert code == 0 and value['status'] == 'cleaned'
    assert not target.exists() and template.read_bytes() == template_bytes
    assert coordination.exists() and sentinel.read_bytes() == b'FOREIGN-F06-SENTINEL\n'


def denied_cleanup(r, monkeypatch):
    r.save(); target = r.s.resource; baseline = files(target); sentinel = r.sentinel()
    # A bounded existing refusal, not an expectation of a nonexistent "active" outcome.
    r.s.data['coordination']['recovery_budget_seconds'] = 3.0
    r.s.data['coordination']['host_event_budget_seconds'] = 3.0
    r.s.save_policy()
    code, actual = r.s.invoke({'operation': 'recover'})
    assert code == 1 and actual['status'] == 'incomplete'
    assert files(target) == baseline
    from poise.infrastructure.hook_transport import HookService
    original = HookService.diagnose
    reached = []
    with monkeypatch.context() as scoped:
        def unavailable(owner, packet):
            reached.append(packet); raise OSError('F06_NATIVE_OBSERVATION_UNAVAILABLE')
        scoped.setattr(HookService, 'diagnose', unavailable)
        code, out, err = r.s.transport(scoped, {'operation': 'recover'})
        assert code == 1 and err == '' and json.loads(out)['status'] == 'incomplete'
        assert reached and files(target) == baseline
    r.event('SessionStart', source='startup', identity='f06-foreign')
    foreign_binding = r.service.latest_binding('f06-foreign', 'primary')['session_id']
    r.event('SessionEnd', identity='f06-foreign')
    foreign = original(r.service, {'definition_path': r.installation['definition_path'],
        'session_id': foreign_binding, 'event': 'SessionEnd', 'event_id': r.events[-1]['id'],
        'run_ids': [], 'probe_cwd': None})
    injected = []
    with monkeypatch.context() as scoped:
        def wrong_end(owner, packet):
            value = original(owner, packet)
            if 'queries' in packet:
                for item, returned in zip(packet['queries'], value['results'], strict=True):
                    if item['request']['event'] == 'SessionEnd':
                        injected.append(deepcopy(returned['result']))
                        returned['result'] = deepcopy(foreign)
            elif packet.get('event') == 'SessionEnd':
                injected.append(deepcopy(value)); value = deepcopy(foreign)
            return value
        scoped.setattr(HookService, 'diagnose', wrong_end)
        code, out, err = r.s.transport(scoped, {'operation': 'recover'})
        assert code == 1 and err == '' and json.loads(out)['status'] == 'incomplete'
        assert injected, 'The actual foreign End diagnostic must be consumed'
        assert files(target) == baseline
        assert sentinel.read_bytes() == b'FOREIGN-F06-SENTINEL\n'
    r.event('SessionEnd')
    assert r.s.invoke({'operation': 'recover'})[1]['status'] == 'cleaned'
    assert not target.exists()


def consumption_recovery(r, monkeypatch):
    r.save(); r.compact(); before_rows = r.transitions(); cp = r.checkpoint_bytes()
    real = os.replace; reached = []
    with monkeypatch.context() as scoped:
        def interrupted(source, target, *args, **kwargs):
            if Path(target) == r.checkpoint:
                reached.append(str(target)); raise OSError('F06_CONSUMPTION_INTERRUPTED')
            return real(source, target, *args, **kwargs)
        scoped.setattr(os, 'replace', interrupted)
        value = r.restore()
        assert reached and value['status'] == 'partial' and value['actions']
        assert r.transitions() == before_rows and r.checkpoint_bytes() == cp
        delivery = r.delivery()
    assert card(r.retry(delivery))['checkpoint']['state'] == 'restored'
    assert r.transitions() == before_rows and r.generation() == 2


def no_work_access(r, monkeypatch):
    from poise.runtime import Poise
    reached = []
    with monkeypatch.context() as scoped:
        def forbidden(*args, **kwargs):
            reached.append(True); raise AssertionError('Disabled work owner must not be queried')
        scoped.setattr(Poise, 'context_metadata', forbidden)
        scoped.setattr(Poise, 'current_task', forbidden)
        value = cycle(r)
        assert reached == [] and value['work']['status'] == 'not_configured'


def config_contract(r):
    from poise.infrastructure.hook_transport import HookSettings
    for field, wrong in (('reader_policy', 7), ('checkpoint_filename', '')):
        candidate = deepcopy(r.settings); candidate['general_context'][field] = wrong
        with pytest.raises(PoiseError) as caught:
            HookSettings.proposed(r.service.settings.path, candidate)
        assert field in str(caught.value)
    options = deepcopy(r.options); options['future_extra'] = {'sentinel': 'KEEP-F06-RAW'}
    r.configure(options); value = r.restore('resume')
    assert r.service.settings.raw['general_context']['future_extra'] == {'sentinel': 'KEEP-F06-RAW'}
    assert 'KEEP-F06-RAW' not in json.dumps(value)


HANDLERS = {
    'C01-all-current-sources': capture_all, 'C02-taskless-capture': taskless_capture,
    'C03-replace-current-checkpoint': replace_checkpoint, 'C04-capture-write-failure': capture_write_failure,
    'T01-post-then-compact-start': post_then_start, 'T02-exact-post-replay': exact_post_replay,
    'T03-compact-start-without-post': compact_without_post, 'T04-exact-start-replay': exact_start_replay,
    'T05-used-checkpoint-next-resume': used_next_resume, 'T06-clear-own-checkpoint': clear_own,
    'D01-worktree-status-change': worktree_change, 'D02-current-instruction-change': instruction_change,
    'D03-shared-coverage-generation': shared_coverage, 'D04-current-check-observation': current_check,
    'S01-owned-anchor-continuation': same_anchor, 'S02-session-project-isolation': project_session_isolation,
    'S03-checkout-isolation': checkout_isolation, 'S04-new-or-fork-session': new_fork,
    'F01-missing-checkpoint': missing_checkpoint, 'F02-invalid-checkpoint': invalid_checkpoint,
    'F03-checkpoint-unavailable': unreadable_checkpoint, 'F04-instructions-unavailable': instruction_unavailable,
    'F05-enabled-work-plugin-failure': work_plugin_failure, 'F06-check-owner-unavailable': check_unavailable,
    'B01-multibyte-and-omissions': multibyte_budget, 'B02-common-native-budget': common_budget,
    'B03-unusable-budget-refusal': invalid_budget, 'E01-confirmed-end-cleans-own': end_cleanup,
    'E02-unknown-active-foreign-preserve': denied_cleanup, 'E03-interrupted-consumption-recovery': consumption_recovery,
    'P01-no-work-owner-access': no_work_access, 'P02-policy-errors-and-raw-unknown': config_contract,
}
assert set(HANDLERS) == {row['id'] for row in CASES}


@pytest.mark.parametrize('case', CASES, ids=lambda row: row['id'])
def test_context_contract(case, project, tmp_path, monkeypatch):
    r = Recovery(project, tmp_path, monkeypatch).enable(specialized=case['id'] == 'B02-common-native-budget')
    action = HANDLERS[case['id']]
    import inspect
    arguments = {'r': r, 'project': project, 'tmp_path': tmp_path, 'monkeypatch': monkeypatch}
    action(**{name: arguments[name] for name in inspect.signature(action).parameters})
