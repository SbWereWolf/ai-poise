"""Full research reader contract; 28 stable calls with independent oracles."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
import sqlite3
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from tests.content_reader.helpers import (
    Scenario, before_final_guard, canonical, files, fixture, sha, text_frames,
)


@pytest.fixture
def s(project, tmp_path):
    return Scenario(project, tmp_path)


def test_complete_small_and_empty_files(s):
    s.ready()
    text = fixture()['source_texts']['lf']
    s.row(s.read(complete=True), text=text, selection='complete', classification='first_read')
    s.write('docs/sample.txt', b'')
    s.row(s.read(complete=True), text='', selection='complete', classification='content_changed')
    summary = s.context()['summary']
    assert summary['cache_entries'] == 2 and summary['event_count'] == 2


def test_large_range_and_actual_eof(s):
    s.ready()
    s.write('docs/sample.txt', ''.join(f'{n:04d} literal line {n:04d}\n'
                                     for n in range(1, 2001)).encode())
    oracle = fixture()['large_file_oracle']
    s.row(s.read(start=100, end=140), text=oracle['expected_content'],
          selection=oracle['expected_range'], classification='first_read')
    last = '2000 literal line 2000\n'
    s.row(s.read(start=2000, end=2010), text=last, selection='lines:2000-2000',
          classification='new_range')
    before = s.context()
    s.error(s.invoke(s.request(start=2001, end=2002)), 'range_after_eof')
    assert s.context() == before


def test_utf8_and_mode_specific_newlines(s):
    s.ready()
    data = fixture()['source_texts']
    s.write('docs/sample.txt', data['crlf'].encode())
    s.row(s.read(complete=True), text=data['lf'], selection='complete', classification='first_read')
    s.row(s.read(start=1, end=2), text=data['range_crlf_1_2'],
          selection='lines:1-2', classification='new_range')
    assert len(data['lf'].encode()) == 16
    assert len(data['crlf'].encode()) == 18


def test_strict_inclusive_range_contract(s):
    s.prove()
    for selection in fixture('selection-invalid.json'):
        request = s.request(); request['reads'][0]['selection'] = selection
        s.error(s.invoke(request), 'invalid_selection')
        assert not s.db.exists() and not s.lock.exists()
    s.ready()
    s.row(s.read(start=1, end=1), text='alpha\n', selection='lines:1-1', classification='first_read')


def test_complete_and_bounded_limits(s):
    s.ready()
    for mode, bound, value, kwargs, error in [
        ('complete', 'max_bytes', 16, {'complete': True}, 'complete_limit'),
        ('complete', 'max_lines', 3, {'complete': True}, 'complete_limit'),
        ('bounded', 'max_bytes', 10, {}, 'bounded_limit'),
        ('bounded', 'max_lines', 7, {}, 'bounded_limit'),
    ]:
        original = deepcopy(s.data)
        s.data[mode][bound] = value; s.save_policy()
        assert s.invoke(s.request(**kwargs))[0] == 0
        s.data[mode][bound] = value - 1; s.save_policy()
        before = s.context()['summary']
        s.error(s.invoke(s.request(**kwargs)), error)
        assert s.context()['summary'] == before
        s.data = original; s.save_policy()
    s.write('docs/sample.txt', fixture()['source_texts']['crlf'].encode())
    s.data['complete']['max_bytes'] = 17; s.save_policy()
    s.error(s.invoke(s.request(complete=True)), 'complete_limit')


def test_full_output_budgets_before_registration(s, monkeypatch):
    s.ready()
    for fmt in ['json', 'text']:
        s.reset('clear-' + fmt, operation='clear')
        candidate = s.fill(fixture()['full_preflight_response'])
        expected = canonical(candidate) if fmt == 'json' else (
            canonical(dict({k: v for k, v in candidate.items() if k != 'items'},
                           item={k: v for k, v in candidate['items'][0].items() if k != 'content'}))
            + fixture()['source_texts']['range_2_3'])
        # Raw-policy hash is always 64 bytes: setting the limit does not alter width.
        s.data['output']['max_bytes'] = len(expected.encode()); s.save_policy()
        assert s.raw(s.request(), fmt)[0] == 0
        s.data['output']['max_bytes'] -= 1; s.save_policy()
        before = s.context()['summary']
        code, out, err = s.raw(s.request(), fmt)
        assert err == ''
        s.error((code, json.loads(out)), 'output_limit')
        assert s.context()['summary'] == before
        s.data['output']['max_bytes'] = 32768; s.save_policy()
        # Line bound is independent of the byte bound, including repeat reads.
        lines = len(expected.splitlines())
        s.data['output']['max_lines'] = lines; s.save_policy()
        assert s.raw(s.request(), fmt)[0] == 0
        if lines > 1:
            s.data['output']['max_lines'] = lines - 1; s.save_policy()
            code, out, err = s.raw(s.request(), fmt)
            assert err == ''; s.error((code, json.loads(out)), 'output_limit')
        s.data['output']['max_lines'] = 512; s.save_policy()
        for allow in [False, True]:
            s.reset('clear-race-' + fmt + str(allow), operation='clear')
            for n in range(9):
                s.reset('race-' + fmt + str(allow) + '-' + str(n))
            candidate = s.fill(fixture()['full_preflight_response'])
            generation = 10 if allow else 9
            candidate['context']['generation'] = generation
            candidate['items'][0]['context_generation'] = generation
            if fmt == 'json':
                encoded = canonical(candidate)
            else:
                encoded = canonical(dict({k: v for k, v in candidate.items() if k != 'items'},
                                         item={k: v for k, v in candidate['items'][0].items() if k != 'content'}))
                encoded += fixture()['source_texts']['range_2_3']
            s.data['output']['max_bytes'] = len(encoded.encode()); s.save_policy()
            results, errors = [], []

            def read_worker():
                try:
                    results.append(s.transport(monkeypatch, s.request(), fmt=fmt))
                except BaseException as exc:
                    errors.append(exc)

            with before_final_guard(monkeypatch, s, s.lock) as (entered, release):
                worker = threading.Thread(target=read_worker, name='reader-interleaving')
                worker.start()
                try:
                    assert entered.wait(10), 'Reader never reached final guard after source I/O'
                    assert s.reset('race-tenth-' + fmt + str(allow))['context']['generation'] == 10
                finally:
                    release.set(); worker.join(10)
                assert not worker.is_alive() and not errors
            assert len(results) == 1
            code, out, err = results[0]; assert err == ''
            if not allow:
                s.error((code, json.loads(out)), 'output_limit')
                assert s.context()['summary'] == s.fill(fixture()['context_before'])['summary']
            else:
                assert code == 0
                if fmt == 'json':
                    result = json.loads(out)
                    assert result['context']['generation'] == 10
                    item = result['items'][0]
                else:
                    header, content = text_frames(out)[0]
                    assert header['context']['generation'] == 10
                    assert content == fixture()['source_texts']['range_2_3'].encode()
                    item = header['item']
                assert item['context_generation'] == 10 and item['classification'] == 'first_read'
                assert s.context()['summary']['cache_entries'] == 1
                assert s.context()['summary']['event_count'] == 1
                assert len(out.encode()) <= s.data['output']['max_bytes']
            assert s.context()['context']['generation'] == 10
            s.data['output']['max_bytes'] = 32768; s.save_policy()


def test_first_and_exact_repeat_contract(s):
    s.ready()
    assert s.read() == s.fill(fixture()['first_response'])
    assert s.context() == s.fill(fixture()['context_after_first'])
    assert s.read() == s.fill(fixture()['repeat_response'])
    assert s.context() == s.fill(fixture()['context_after_repeat'])


def test_new_range_and_changed_selected_content(s):
    s.ready(); s.read()
    s.row(s.read(start=1, end=1), text='alpha\n', selection='lines:1-1', classification='new_range')
    s.write('docs/sample.txt', 'outside\nβeta\nlast'.encode())
    assert s.read() == s.fill(fixture()['repeat_response'])
    s.write('docs/sample.txt', 'outside\nγeta\nlast'.encode())
    s.row(s.read(), text='γeta\nlast', selection='lines:2-3', classification='content_changed')


def test_new_generation_and_classification_precedence(s):
    s.ready(); s.read(); s.reset()
    s.row(s.read(), text='βeta\nlast', selection='lines:2-3',
          classification='new_context_generation', generation=1)
    s.row(s.read(), text='βeta\nlast', selection='lines:2-3',
          classification='exact_repeat', status='unchanged', generation=1)
    s.reset('manual-2'); s.write('docs/sample.txt', 'alpha\nγeta\nlast'.encode())
    s.row(s.read(), text='γeta\nlast', selection='lines:2-3',
          classification='content_changed', generation=2)


def test_previously_seen_version_is_available(s):
    s.ready()
    for step in fixture()['state_sequences'][0]['steps']:
        text = 'γeta\nlast' if step['hash'] == fixture()['selected_sha256_literals']['changed_range_2_3'] else 'βeta\nlast'
        if step['read'] == 'lines:1-1':
            s.read(start=1, end=1)
            continue
        s.write('docs/sample.txt', ('alpha\n' + text).encode())
        s.row(s.read(), text=text, selection=step['read'], classification=step['classification'],
              status=step['status'], generation=step['generation'])
    s.reset()
    # Latest FIRST-registered version was B; a repeat A did not reorder history.
    s.row(s.read(), text='βeta\nlast', selection='lines:2-3', classification='content_changed', generation=1)


def test_project_checkout_and_caller_isolation(s, project, tmp_path):
    s.ready(); s.read()
    other = Scenario(project, tmp_path, identity='second-caller')
    other.ready(); assert other.read()['items'][0]['classification'] == 'first_read'
    assert s.read()['items'][0]['classification'] == 'exact_repeat'
    other.native_identity(); assert other.invoke({'operation': 'prepare'})[0] == 0
    assert other.read()['items'][0]['classification'] == 'first_read'
    # A separate configured project and repository via the existing setup owner.
    from tests.skills.instruction_delivery_helpers import Scenario as ProjectScenario
    from tests.projects.helpers import bootstrap_fixture_resources
    beta_project = ProjectScenario(project, tmp_path, 'beta')
    beta_config = json.loads(beta_project.config.read_text())
    if 'runtime_resources' not in beta_config:
        beta_config = bootstrap_fixture_resources(
            beta_project.root / 'setup.json', beta_project.config)
    beta = Scenario({'app': beta_project.checkout, 'root': beta_project.config.parent,
                     'config_path': beta_project.config,
                     'cfg': beta_config}, tmp_path, identity='beta-reader')
    beta.ready(); assert beta.read()['items'][0]['classification'] == 'first_read'
    original = s.checkout
    s.checkout = beta.checkout
    s.error(s.invoke(s.request()), 'project_binding')
    s.checkout = original
    assert s.read()['items'][0]['classification'] == 'exact_repeat'


def test_scoped_reset_clear_and_event_replay(s, project, tmp_path):
    s.prove(); s.error(s.invoke({'operation': 'context'}), 'registry_missing')
    s.ready(); s.read()
    other = Scenario(project, tmp_path, identity='other-scope'); other.ready(); other.read()
    before = other.context()
    first = s.reset(); assert s.reset() == first
    s.error(s.invoke({'operation': 'reset', 'reason': 'conflict', 'event_id': 'manual-1'}), 'event_conflict')
    s.error(s.invoke({'operation': 'clear', 'reason': 'manual', 'event_id': 'manual-1'}), 'event_conflict')
    clear = s.reset('clear-1', operation='clear')
    assert s.reset('clear-1', operation='clear') == clear
    assert clear['context']['generation'] == 0
    assert s.context()['summary'] == s.fill(fixture()['context_before'])['summary']
    assert s.read()['items'][0]['classification'] == 'first_read'
    assert other.context() == before


def test_concurrent_exact_reads(s):
    s.ready()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(s.read) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    assert sorted(v['items'][0]['status'] for v in results) == ['content', 'unchanged']
    assert s.context() == s.fill(fixture()['context_after_repeat'])


def test_batch_validation_and_registration_atomicity(s):
    s.ready()
    request = s.request(); first = request['reads'][0]
    before = s.context()
    for second in [first, dict(first, key='z', path='docs/missing.txt'),
                   dict(first, key='z', path='../foreign')]:
        actual = s.invoke({'operation': 'read', 'reads': [first, second]})
        assert actual[0] in (2, 3) and actual[1]['status'] == 'rejected'
        assert s.context() == before
    count = s.base['cfg']['batch']['max_items'] + 1
    s.error(s.invoke({'operation': 'read', 'reads': [dict(first, key='k' + str(n))
                                                   for n in range(count)]}), 'invalid_request')
    code, result = s.invoke({'operation': 'read', 'reads': [dict(first, key='z'), dict(first, key='a')]})
    assert code == 0 and [v['key'] for v in result['items']] == ['a', 'z']
    assert [v['status'] for v in result['items']] == ['content', 'unchanged']
    assert s.context()['summary']['cache_entries'] == 1
    assert s.context()['summary']['event_count'] == 2


def test_strict_request_and_policy_shapes(s):
    s.prove()
    for request in fixture('request-invalid.json'):
        s.error(s.invoke(request), 'invalid_request'); assert not s.db.exists()
    for raw in [b'{', b'{"operation":"context","operation":"prepare"}', b'NaN']:
        s.error(s.invoke({}, raw=raw), 'invalid_request'); assert not s.db.exists()
    base = deepcopy(s.data)
    for case in fixture('policy-invalid.json'):
        s.data = deepcopy(base); target = s.data
        for key in case['path'][:-1]: target = target[key]
        target[case['path'][-1]] = case['value']; s.save_policy()
        s.error(s.invoke({'operation': 'prepare'}), 'invalid_policy'); assert not s.db.exists()
    s.data = deepcopy(base); del s.data['complete']; s.save_policy()
    s.error(s.invoke({'operation': 'prepare'}), 'invalid_policy')


def test_source_failure_diagnostics(s, monkeypatch):
    s.ready()
    s.error(s.invoke(s.request(path='docs/missing.txt')), 'source_missing')
    s.error(s.invoke(s.request(path='docs')), 'source_not_file')
    s.error(s.invoke(s.request(path='../foreign')), 'path_outside_project')
    s.write('docs/sample.txt', b'\xff\xfe')
    s.error(s.invoke(s.request()), 'source_not_utf8')
    s.write('docs/sample.txt', fixture()['source_texts']['lf'].encode())
    original = Path.open

    def denied(path, *args, **kwargs):
        if path == s.checkout / 'docs/sample.txt': raise PermissionError('fixture permission denied')
        return original(path, *args, **kwargs)

    before = s.context()
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', denied)
        code, out, err = s.transport(patch, s.request())
    assert err == ''; s.error((code, json.loads(out)), 'source_unreadable')
    assert s.context() == before


def test_prepared_storage_and_schema_preservation(s):
    s.prove()
    directory = s.db.parent
    assert not directory.exists()
    s.error(s.invoke(s.request()), 'registry_missing'); assert not directory.exists()
    s.ready()
    s.lock.unlink()
    before = s.db.read_bytes()
    s.error(s.invoke(s.request()), 'registry_missing')
    assert not s.lock.exists() and s.db.read_bytes() == before
    assert s.invoke({'operation': 'prepare'})[0] == 0
    with sqlite3.connect(s.db) as db: db.execute('PRAGMA user_version=71')
    before = files(directory)
    s.error(s.invoke(s.request()), 'registry_schema')
    s.error(s.invoke({'operation': 'prepare'}), 'registry_schema')
    assert files(directory) == before
    # Unknown/corrupt storage belongs to its existing owner and is not replaced.
    s.db.write_bytes(b'foreign database marker')
    before = files(directory)
    s.error(s.invoke({'operation': 'prepare'}), 'registry_invalid')
    assert files(directory) == before


def test_genuine_caller_admission_profile(s):
    s.ready()
    assert s.read()['context']['caller_key'] == s.caller_key
    s.native_identity()
    assert s.invoke({'operation': 'prepare'})[0] == 0
    assert s.read()['context']['caller_key'] == s.caller_key
    invalid = dict(s.env, CODEX_SESSION_ID='conflicting')
    s.error(s.invoke(s.request(), env=invalid), 'invalid_caller')
    invalid = {k: v for k, v in s.env.items() if k not in ('CODEX_THREAD_ID', 'CODEX_SESSION_ID')}
    invalid['POISE_SESSION'] = 'not-supported'
    s.error(s.invoke(s.request(), env=invalid), 'invalid_caller')


def test_native_hook_observation_profile(s, tmp_path):
    s.prove(); s.native_identity()
    assert s.invoke({'operation': 'prepare'})[0] == 0
    s.native_hooks(tmp_path)
    start = s.event()
    s.error(s.invoke(s.request()), 'context_profile_changed')
    assert s.invoke({'operation': 'prepare'})[0] == 0
    value = s.read()
    assert value['context']['native_episode'] == start
    assert value['context']['admission_mode'] == 'native_hook'
    s.event('SessionEnd'); s.event('Stop')
    s.error(s.invoke(s.request()), 'admission_dead')
    binding = s.hooks.latest_binding(s.identity, 'primary')
    s.hooks.registry.record_event(binding['session_id'], 'PostToolUse', 'tool', None)
    s.error(s.invoke(s.request()), 'admission_dead')
    second = s.event()
    assert second > start
    s.error(s.invoke(s.request()), 'context_episode_changed')
    assert s.invoke({'operation': 'prepare'})[0] == 0
    assert s.read()['items'][0]['classification'] == 'first_read'
    # Binding publication and SessionStart observation are distinct operations.
    known = deepcopy(s.hooks.latest_binding(s.identity, 'primary'))
    previous_identity = s.identity
    known['session_id'] = 'known-reader-without-start'
    known['external_session'] = 'known-reader-without-start'
    s.hooks.registry.bind(known)
    s.native_identity('known-reader-without-start')
    s.error(s.invoke({'operation': 'prepare'}), 'admission_uncertain')
    s.native_identity(previous_identity)
    with sqlite3.connect(s.hooks.settings.database) as db:
        db.execute('PRAGMA user_version=71')
    before = s.db.read_bytes()
    s.error(s.invoke(s.request()), 'admission_unavailable')
    assert s.db.read_bytes() == before
    with sqlite3.connect(s.hooks.settings.database) as db:
        db.execute('PRAGMA user_version=1')
    s.hooks.settings.lock.unlink()
    before = s.db.read_bytes()
    s.error(s.invoke(s.request()), 'admission_unavailable')
    assert not s.hooks.settings.lock.exists() and s.db.read_bytes() == before


def test_native_end_serializes_registration_admission(s, tmp_path, monkeypatch):
    s.prove(); s.native_hooks(tmp_path); s.event()
    assert s.invoke({'operation': 'prepare'})[0] == 0
    for resume in [False, True]:
        if resume:
            s.event(); assert s.invoke({'operation': 'prepare'})[0] == 0
        results, errors = [], []

        def reader():
            try: results.append(s.transport(monkeypatch, s.request()))
            except BaseException as exc: errors.append(exc)

        with before_final_guard(monkeypatch, s, s.hooks.settings.lock) as (entered, release):
            thread = threading.Thread(target=reader, name='reader-interleaving'); thread.start()
            try:
                assert entered.wait(10)
                s.event('SessionEnd')
                if resume: s.event()
            finally:
                release.set(); thread.join(10)
            assert not thread.is_alive() and not errors
        code, out, err = results[0]; assert err == ''
        s.error((code, json.loads(out)), 'context_episode_changed' if resume else 'admission_dead')
        if resume:
            for request in [s.request(), {'operation': 'context'},
                            {'operation': 'reset', 'reason': 'manual', 'event_id': 'new'},
                            {'operation': 'clear', 'reason': 'manual', 'event_id': 'new'}]:
                s.error(s.invoke(request), 'context_episode_changed')
            assert s.invoke({'operation': 'prepare'})[0] == 0
            assert s.context()['summary']['event_count'] == 0
            assert s.read()['items'][0]['classification'] == 'first_read'
    original = deepcopy(s.data)
    s.data['registry']['directory'] = str(s.hooks.settings.lock.parent)
    s.data['registry']['lock'] = s.hooks.settings.lock.name
    s.save_policy()
    s.error(s.invoke(s.request()), 'lock_resources_conflict')
    s.data = original; s.save_policy()


def test_registry_write_lock_failure(s):
    s.ready(); before = s.context()
    writer = sqlite3.connect(s.db)
    try:
        writer.execute('BEGIN IMMEDIATE')
        s.error(s.invoke(s.request()), 'registry_write')
    finally:
        writer.rollback(); writer.close()
    assert s.context() == before
    assert s.read()['items'][0]['classification'] == 'first_read'


def test_registry_read_and_connection_failure(s, monkeypatch):
    s.ready(); before = s.context(); original = sqlite3.connect

    def denied(path, *args, **kwargs):
        if str(s.db) in str(path) or s.db.as_uri() in str(path):
            raise sqlite3.OperationalError('fixture connection unavailable')
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', denied)
        code, out, err = s.transport(patch, {'operation': 'context'})
    assert err == ''; s.error((code, json.loads(out)), 'registry_read')
    assert s.context() == before


def test_writer_failure_preserves_registered_availability(s, monkeypatch):
    s.ready()

    class BrokenSink(io.StringIO):
        def __init__(self, prefix):
            super().__init__(); self.prefix = prefix

        def write(self, value):
            super().write(value[:self.prefix])
            raise OSError('fixture output failed')

    for prefix in [0, 17]:
        s.reset('writer-clear-' + str(prefix), operation='clear')
        sink = BrokenSink(prefix)
        code, out, err = s.transport(monkeypatch, s.request(), sink=sink)
        s.error((code, json.loads(err)), 'output_write')
        assert len(out) == prefix
        assert s.context()['summary']['cache_entries'] == 1
        assert s.read()['items'][0]['status'] == 'unchanged'
        s.reset('writer-recover-' + str(prefix))
        assert s.read()['items'][0]['status'] == 'content'


def test_both_complete_representation_consumers(s):
    s.ready()
    assert s.read() == s.fill(fixture()['first_response'])
    s.reset('text-clear', operation='clear')
    request = s.request()
    request['reads'].append(dict(request['reads'][0], key='z'))
    code, out, err = s.raw(request, 'text')
    assert code == 0 and err == ''
    frames = text_frames(out)
    assert len(frames) == 2
    assert frames[0][1] == 'βeta\nlast'.encode() and frames[1][1] == b''
    assert frames[0][0]['item']['status'] == 'content'
    assert frames[1][0]['item']['status'] == 'unchanged'
    assert 'content' not in frames[0][0]['item']
    s.reset('text-single', operation='clear')
    code, out, err = s.raw(s.request(), 'text')
    assert (code, out, err) == (0, s.fill(fixture()['first_text_utf8_template']), '')
    code, out, err = s.raw(s.request(), 'text')
    assert (code, out, err) == (0, s.fill(fixture()['repeat_text_utf8_template']), '')


def test_complete_machine_output_and_common_views(s, tmp_path):
    s.ready()
    from tests.content_reader.helpers import FIXTURES
    text = (FIXTURES / 'long-body.txt').read_text(encoding='utf-8')
    s.write('docs/sample.txt', text.encode())
    s.data['complete']['max_bytes'] = 16384; s.save_policy()
    code, out, err = s.raw(s.request(complete=True)); assert code == 0 and err == ''
    assert json.loads(out)['items'][0]['content'] == text
    saved = tmp_path / 'reader-response.json'; saved.write_bytes(out.encode())
    for view in ['brief', 'expanded', 'full']:
        result = subprocess.run([sys.executable, '-B', '-m', 'poise', 'result-view',
                                 '--response', str(saved), '--view', view, '--output-chars', '1200'],
                                capture_output=True, env=s.env, check=False)
        assert result.returncode == 0 and result.stderr == b''
        assert len(result.stdout.decode()) <= 1200
        value = json.loads(result.stdout)
        assert value['response_path'] == str(saved) and value['status'] == 'ready'
        assert saved.read_bytes() == out.encode()


def test_functional_metadata_and_clock_owner(s, monkeypatch):
    s.ready()
    from tests.accounting.test_clock import FakeClock, observation
    class ObservedClock(FakeClock):
        observations = 0

        def observe(self):
            self.observations += 1
            return super().observe()

    clock = ObservedClock([observation('2026-01-01T00:00:00+00:00', n, 'fixture-domain')
                           for n in range(32)])
    code, out, err = s.transport(monkeypatch, s.request(), clock=clock)
    assert code == 0 and err == '' and json.loads(out) == s.fill(fixture()['first_response'])
    assert clock.observations > 0
    assert s.context() == s.fill(fixture()['context_after_first'])
    s.read()
    assert s.context() == s.fill(fixture()['context_after_repeat'])
    assert b'\xce\xb2eta' not in s.db.read_bytes(), 'Registry must not retain source bodies'
    assert 'duration' not in canonical(s.context())


def test_configuration_unknown_values_remain_raw(s):
    s.ready()
    s.data['future_option'] = {'opaque': ['retained', 123]}; s.data['version'] = 'future-opaque'
    s.save_policy(); before = s.policy.read_bytes()
    result = s.read()
    assert result['policy_sha256'] == sha(before)
    assert s.policy.read_bytes() == before
    assert json.loads(before)['future_option'] == {'opaque': ['retained', 123]}
    s.data['bounded']['max_bytes'] = False; s.save_policy()
    s.error(s.invoke(s.request()), 'invalid_policy')


def test_taskless_reader_preserves_specialized_owners(s):
    s.ready()
    task_db = s.state / s.base['cfg']['paths']['database']
    before_task = task_db.read_bytes() if task_db.exists() else None
    checkout = files(s.checkout)
    foreign = s.state / 'foreign-specialized-receipt.json'
    foreign.write_bytes(b'foreign immutable specialized receipt')
    for request in [s.request(), {'operation': 'context'},
                    fixture()['requests']['reset'], fixture()['requests']['clear']]:
        assert s.invoke(request)[0] == 0
    assert (task_db.read_bytes() if task_db.exists() else None) == before_task
    assert foreign.read_bytes() == b'foreign immutable specialized receipt'
    assert files(s.checkout) == checkout
