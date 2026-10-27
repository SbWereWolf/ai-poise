"""Native event behavior checked externally against independent literal facts."""
from contextlib import closing
import json
from pathlib import Path
import subprocess
import time

from generic_checks import PY
from hook_support import Installation, assert_error, concurrent_events, equal, fixture, decode
from session_support import CALLS, OBSERVATIONS, capture
from state_sql import connect


def native_lifecycle():
    # Explicit incoming event is its own authority, including inherited dispatcher env.
    for environment in ({}, {'CODEX_THREAD_ID': 'native-A'},
                        {'CODEX_THREAD_ID': 'parent-dispatcher'},
                        {'CODEX_THREAD_ID': 'parent', 'CODEX_SESSION_ID': 'another-parent'}):
        with Installation() as app:
            app.prepare()
            app.native = environment
            binding = app.assert_event(app.event())
            for sequence, kind in enumerate(['UserPromptSubmit', 'Stop', 'SessionEnd'], start=2):
                app.assert_event(app.event(kind, event_id=f'event-{sequence}'), kind=kind,
                                 event_id=f'event-{sequence}', sequence=sequence, binding=binding)
            facts = app.facts()
            raw = json.dumps(facts, ensure_ascii=False)
            assert 'PRIVATE-PROMPT-TEXT' not in raw and 'PRIVATE-ASSISTANT-TEXT' not in raw
            assert len(facts['rows']['poise_sessions']) == 1
            assert len(facts['rows']['poise_bindings']) == 1
            assert len(facts['rows']['poise_hook_bindings']) == 1
            assert len(facts['rows']['poise_hook_events']) == 4
            assert not list(app.install.glob('src')) and not list(app.install.glob('apps'))
    with Installation() as app:
        app.prepare()
        binding = app.assert_event(app.event())
        app.assert_event(app.event('UserPromptSubmit', event_id='empty-prompt', prompt=''),
                         kind='UserPromptSubmit', event_id='empty-prompt', sequence=2, binding=binding)
    for source in ['resume', 'clear', 'compact', 'fork']:
        with Installation() as app:
            app.prepare()
            app.assert_event(app.event(source=source), source=source)


def repeat_isolation():
    with Installation() as app:
        app.prepare()
        binding = app.assert_event(app.event())
        app.native = {'CODEX_THREAD_ID': 'native-A'}
        assigned = app.call('session.assign_role', fixture('assign.json'))[2]
        assert assigned['status'] == 'ok', assigned
        before = app.facts()
        app.native = {'CODEX_THREAD_ID': 'different-parent'}
        with closing(connect(app.database)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            try:
                app.assert_event(app.event(), binding=binding, replayed=True, assigned=True)
                equal(app.facts(), before)
            finally:
                writer.rollback()
        app.assert_event(app.event(identity='native-B', event_id='other'), identity='native-B',
                         event_id='other', sequence=2)
        assert len(app.facts()['rows']['poise_sessions']) == 2
    with Installation() as left, Installation() as right:
        left.prepare()
        right.prepare()
        left.assert_event(left.event())
        right.assert_event(right.event())
        assert left.database != right.database
        equal(left.facts()['rows']['poise_sessions'], right.facts()['rows']['poise_sessions'])
    with Installation() as app:
        app.prepare()
        values = concurrent_events(app, app.event_input())
        assert sorted(value['result']['replayed'] for value in values) == [False, True], values
        binding = None
        for value in values:
            binding = app.assert_event(value, binding=binding, replayed=value['result']['replayed'])
        facts = app.facts()['rows']
        for table in ['poise_sessions', 'poise_bindings', 'poise_hook_bindings', 'poise_hook_events']:
            assert len(facts[table]) == 1, facts


def commit_failure():
    with Installation() as app:
        app.prepare()
        app.config['limits']['sqlite_timeout_seconds'] = 1
        before = app.facts()
        app.write_config()
        journal = Path(str(app.database) + '-journal')
        detail = {'qualified_initial_hook_commit': {}, 'journal_samples': []}
        OBSERVATIONS.append(detail)
        with closing(connect(app.database)) as reader:
            assert reader.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
            assert not journal.exists()
            reader.execute('BEGIN')
            detail['reader_rows'] = list(reader.execute('SELECT id,revision FROM poise_sessions'))
            raw = (json.dumps(app.packet('hook.event', app.event_input())) + '\n').encode()
            argv = [PY, '-I', '-S', '-B', str(app.install / 'bin/poise'), '--config', str(app.path)]
            record = {'argv': argv, 'input_hex': raw.hex(), 'cwd': str(app.state.cwd),
                      'environment': app.environment(), 'config_raw': app.path.read_text()}
            CALLS.append(record)
            child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, cwd=app.state.cwd, env=app.environment())
            try:
                child.stdin.write(raw)
                child.stdin.close()
                child.stdin = None
                while child.poll() is None:
                    if journal.exists():
                        size = journal.stat().st_size
                        detail['journal_samples'].append(size)
                    time.sleep(0.01)
                out, err = child.communicate()
                result = subprocess.CompletedProcess(argv, child.returncode, out, err)
                capture(result, record)
                body = decode(result, record)
                assert_error(body, 'state_busy', {'database': str(app.database)}, 'retry:state')
                assert any(size > 0 for size in detail['journal_samples']), detail
            finally:
                if child.poll() is None:
                    child.kill()
                if 'exit_code' not in record:
                    out, err = child.communicate()
                    capture(subprocess.CompletedProcess(argv, child.returncode, out, err), record)
                reader.rollback()
        equal(app.facts(), before)
        app.assert_event(app.event())
        for table in ['poise_sessions', 'poise_bindings', 'poise_hook_bindings', 'poise_hook_events']:
            assert len(app.facts()['rows'][table]) == 1


def boundaries_atomic():
    for version, name, action in [(None, 'state_not_found', 'prepare:state'),
                                  (0, 'state_not_prepared', 'prepare:state'),
                                  (99, 'state_incompatible', 'select:state')]:
        with Installation() as app:
            if version is not None:
                app.state.empty_zero()
                app.execute(f'PRAGMA user_version={version}')
            body = app.event()
            details = {'database': str(app.database)} if version is None else {'version': version}
            assert_error(body, name, details, action)
            if version is None:
                assert not app.database.exists()
            else:
                with closing(connect(app.database, readonly=True)) as db:
                    assert db.execute('PRAGMA user_version').fetchone()[0] == version
                    assert not list(db.execute('SELECT name FROM sqlite_master WHERE type=?', ('table',)))
    with Installation() as app:
        app.prepare()
        before = app.facts()
        for case in fixture('native-invalid.json'):
            value = app.event_input(case['kind'])
            if case.get('change') == 'remove':
                del value['event'][case['field']]
            else:
                value['event'][case['field']] = case['value']
            body = app.call('hook.event', value)[2]
            assert_error(body, 'invalid_request', {'path': case['pointer']}, 'correct:request', status='rejected')
            equal(app.facts(), before)
        for value, pointer in [({'event_id': '', 'event': app.event_input()['event']}, '/input/event_id'),
                               ({'event_id': 'e', 'event': []}, '/input/event'),
                               ({'event_id': 'e', 'event': {'hook_event_name': 'Unknown'}}, '/input/event/hook_event_name')]:
            assert_error(app.call('hook.event', value)[2], 'invalid_request', {'path': pointer},
                         'correct:request', status='rejected')
            equal(app.facts(), before)
        for case in fixture('event-input-invalid.json'):
            value = app.event_input()
            target = value if case['scope'] == 'input' else value['event']
            if case['change'] == 'remove':
                del target[case['field']]
            else:
                target[case['field']] = case['value']
            assert_error(app.call('hook.event', value)[2], 'invalid_request',
                         {'path': case['pointer']}, 'correct:request', status='rejected')
            equal(app.facts(), before)
        assert_error(app.event('Stop'), 'session_not_found', {}, 'ensure:session')
        app.ensure()
        before = app.facts()
        assert_error(app.event('Stop'), 'hook_not_found', {}, 'start:hook')
        equal(app.facts(), before)
    # Configuration contracts are bounded ordinary inputs, with zero owned effects.
    cases = [('events', [], '/hooks/events'),
             ('events', ['SessionStart', 'SessionStart'], '/hooks/events'),
             ('events', ['Stop'], '/hooks/events'),
             ('context_template', '{unknown}', '/hooks/context_template'),
             ('stop_template', '{session_id}', '/hooks/stop_template'),
             ('external', [], '/hooks/external'),
             ('generic_config', 'absent.json', '/hooks/generic_config')]
    for field, value, pointer in cases:
        with Installation() as app:
            app.prepare()
            app.config['hooks'][field] = value
            before = app.facts()
            assert_error(app.event(), 'invalid_config', {'path': pointer}, 'correct:config', status='rejected')
            equal(app.facts(), before)
    with Installation() as app:
        app.prepare()
        del app.config['hooks']
        before = app.facts()
        assert_error(app.event(), 'invalid_config', {'path': '/hooks'}, 'correct:config', status='rejected')
        equal(app.facts(), before)
    with Installation() as app:
        app.prepare()
        binding_path = app.generated()
        before = app.facts()
        assert_error(app.event(), 'invalid_config', {'path': '/caller/mode'}, 'correct:config', status='rejected')
        equal(app.facts(), before)
        assert not binding_path.exists()
    with Installation() as app:
        app.prepare()
        with closing(connect(app.database)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            try:
                before = app.facts()
                assert_error(app.event(), 'state_busy', {'database': str(app.database)}, 'retry:state')
                equal(app.facts(), before)
            finally:
                writer.rollback()
    commit_failure()
    with Installation() as app:
        app.prepare()
        packet = app.packet('hook.event', app.event_input(event_id='многобайтный'))
        raw = (json.dumps(packet, ensure_ascii=False) + '\n').encode()
        assert len(raw) > len(raw.decode())
        app.config['limits']['packet_bytes'] = len(raw)
        app.assert_event(app.call('hook.event', raw=raw)[2], event_id='многобайтный')
        before = app.facts()
        body = app.call('hook.event', raw=raw + b' ')[2]
        assert_error(body, 'packet_limit', {'limit': 'packet_bytes'}, 'reduce:packet', status='rejected')
        equal(app.facts(), before)


def retained_standalone():
    with Installation() as app:
        app.prepare()
        app.ensure()
        app.retain()
        before = app.facts()['rows']
        app.config['session_store'] = '../state α/sessions.sqlite'
        app.config['unknown_root'] = {'operator_value': 'сохранить'}
        app.config['hooks']['unknown_option'] = ['preserve', False, 0]
        raw_config = app.write_config()
        body = app.event()
        # Retained fixture adds an unrelated event at sequence10.
        binding = app.assert_event(body, sequence=11)
        for table in ['poise_role_assignments', 'poise_reader_state', 'poise_cursors']:
            equal(app.facts()['rows'][table], before[table])
        assert len(app.facts()['rows']['poise_hook_bindings']) == 2
        assert len(app.facts()['rows']['poise_hook_events']) == 2
        for table, key in [('poise_hook_bindings', 'hook-keep'), ('poise_hook_events', 10)]:
            retained_before = [row for row in before[table] if row[0] == key]
            retained_after = [row for row in app.facts()['rows'][table] if row[0] == key]
            assert len(retained_before) == 1, retained_before
            equal(retained_after, retained_before)
        assert app.path.read_bytes() == raw_config
        app.native = {'CODEX_THREAD_ID': 'native-A'}
        app.call('session.assign_role', fixture('assign.json'))
        facts = app.facts()
        app.native = {}
        app.assert_event(app.event(), sequence=11, replayed=True, assigned=True, binding=binding)
        equal(app.facts(), facts)
