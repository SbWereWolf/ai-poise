"""Six independent external Session scenarios; no production imports or mocks."""
from contextlib import closing
import json

from generic_checks import bind
from state_checks import zero_snapshot
from state_sql import connect
from session_support import (FIX, Installation, concurrent, fixture, postwrite_failure,
                             request, session)


def exact_success(body, result):
    expected = bind(fixture('success.json'), {'@RESULT@': result})
    assert body == expected, (body, expected)


def initial(app, identity='native-A'):
    app.native = {'CODEX_THREAD_ID': identity}
    app.ok('session.ensure', {'session': session(identity=identity), 'created': True})
    app.assert_session(session(identity=identity))


def assigned(app, name='author', identity='native-A'):
    app.ok('session.assign_role', {'session': session(name, identity), 'changed': True},
           value=request(name))
    app.assert_session(session(name, identity))


def native_checks():
    with Installation() as app:
        app.prepare()
        before = app.facts()
        app.error('session.status', 'session_not_found')
        assert app.facts() == before
        initial(app)
        established = app.facts()
        app.ok('session.ensure', {'session': session(), 'created': False})
        app.ok('session.status', {'session': session()})
        assert app.facts() == established
        assigned(app)
        after = app.facts()
        app.ok('session.status', {'session': session('author')})
        app.ok('session.assign_role', {'session': session('author'), 'changed': False},
               value=request('author'))
        later = request('author')
        later.update(request_id='later-same-role', authorization_ref='later-decision')
        app.ok('session.assign_role', {'session': session('author'), 'changed': False}, value=later)
        app.error('session.assign_role', 'role_locked', request('reviewer'),
                  coordinates={'@CURRENT@': 'author', '@REQUESTED@': 'reviewer'})
        assert app.facts() == after


def generated_checks():
    with Installation() as app:
        app.prepare()
        binding = app.generated()
        before = app.facts()
        app.error('session.status', 'session_not_found')
        assert not binding.exists() and app.facts() == before
        _, _, body = app.call('session.ensure')
        identity = body['result']['session']['id']
        exact_success(body, {'session': session('initial-generated', identity), 'created': True})
        saved_binding = binding.read_bytes()
        value = json.loads(saved_binding)
        assert set(value) == {'schema', 'caller_id'} and value['schema'] == 'poise-caller-binding-1'
        assert isinstance(value['caller_id'], str) and value['caller_id']
        app.ok('session.ensure', {'session': session('initial-generated', identity), 'created': False},
               cwd=app.root)
        app.ok('session.assign_role', {'session': session('generated-author', identity), 'changed': True},
               value=request('author'))
        app.assert_session(session('generated-author', identity))
        second = app.generated('second-caller.json')
        _, _, other = app.call('session.ensure')
        other_id = other['result']['session']['id']
        assert other_id != identity and second.exists()
        exact_success(other, {'session': session('initial-generated', other_id), 'created': True})
        app.ok('session.status', {'session': session('initial-generated', other_id)})
        app.assert_session(session('initial-generated', other_id))
        app.assert_session(session('generated-author', identity))
        assert binding.read_bytes() == saved_binding
        assert len(app.facts()['rows']['poise_sessions']) == 2
        assert len(app.facts()['rows']['poise_bindings']) == 2
    with Installation() as app:
        app.prepare()
        app.execute('INSERT INTO poise_sessions(id,origin,status,revision) VALUES(?,?,?,?)',
                    ('reserved-native', 'generated', 'open', 1))
        app.execute('INSERT INTO poise_bindings(identity_key,session_id,inventory) VALUES(?,?,?)',
                    ('other-binding', 'reserved-native', '[]'))
        before = app.facts()
        app.native = {'CODEX_THREAD_ID': 'reserved-native'}
        app.error('session.ensure', 'identity_conflict', coordinates={'@SESSION@': 'reserved-native'})
        assert app.facts() == before


def isolation_checks():
    with Installation() as first, Installation() as second:
        first.prepare()
        second.prepare()
        first.config['session_store'] = '../state α/sessions.sqlite'
        first.config['operator_note'] = {'retained': 'root'}
        first.config['caller']['operator_note'] = 'caller'
        first.config['limits']['operator_note'] = 'limits'
        first.ok('session.ensure', {'session': session(), 'created': True}, cwd=second.state.cwd)
        assigned(first)
        initial(second)
        assigned(second, 'reviewer')
        before_first, before_second = first.facts(), second.facts()
        first.ok('session.status', {'session': session('author')}, cwd=second.root)
        second.ok('session.status', {'session': session('reviewer')}, cwd=first.root)
        assert first.facts() == before_first and second.facts() == before_second
        assert not (first.root / 'src/poise').exists()
        assert not (first.root / 'apps/execution').exists()


def concurrency_checks():
    with Installation() as app:
        app.prepare()
        app.native = {'CODEX_THREAD_ID': 'native-race'}
        app.config['limits']['sqlite_timeout_seconds'] = 1
        assert app.facts()['rows']['poise_sessions'] == []
        results = concurrent(app, 'session.ensure', [{}, {}])
        created = [value['result']['created'] for value in results]
        assert created.count(True) == created.count(False) == 1, results
        for value in results:
            exact_success(value, {'session': session(identity='native-race'),
                                  'created': value['result']['created']})
        app.assert_session(session(identity='native-race'))
        before = app.facts()
        assert len(before['rows']['poise_sessions']) == len(before['rows']['poise_bindings']) == 1
        assert before['rows']['poise_role_assignments'] == []
        app.ok('session.status', {'session': session(identity='native-race')})
        app.ok('session.ensure', {'session': session(identity='native-race'), 'created': False})
        assert app.facts() == before
        names = ['author', 'reviewer']
        results = concurrent(app, 'session.assign_role', [request(name) for name in names])
        winners = [index for index, value in enumerate(results) if value['status'] == 'ok']
        assert len(winners) == 1, results
        winner = winners[0]
        loser = 1 - winner
        expected = session(names[winner], 'native-race')
        exact_success(results[winner], {'session': expected, 'changed': True})
        assert results[loser] == bind(fixture('errors.json')['role_locked'],
                                    {'@CURRENT@': names[winner], '@REQUESTED@': names[loser]})
        app.assert_session(expected)
        after = app.facts()['rows']
        assert len(after['poise_sessions']) == len(after['poise_bindings']) == len(after['poise_role_assignments']) == 1


def state_boundaries():
    with Installation() as app:
        binding = app.generated()
        app.error('session.ensure', 'state_not_found')
        assert not app.database.exists() and not app.database.parent.exists() and not binding.exists()
    with Installation() as app:
        app.state.empty_zero()
        before = zero_snapshot(app.database)
        app.error('session.ensure', 'state_not_prepared')
        assert zero_snapshot(app.database) == before == {'version': 0, 'tables': []}
    with Installation() as app:
        app.prepare()
        app.execute('PRAGMA user_version=2')
        before = app.facts()
        app.error('session.ensure', 'state_incompatible')
        assert app.facts() == before
    with Installation() as app:
        app.prepare()
        initial(app)
        assigned(app)
        initial(app, 'native-B')
        before = app.facts()
        with closing(connect(app.database)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            app.native = {'CODEX_THREAD_ID': 'native-A'}
            app.ok('session.status', {'session': session('author')})
            app.ok('session.ensure', {'session': session('author'), 'created': False})
            app.ok('session.assign_role', {'session': session('author'), 'changed': False}, value=request('author'))
            app.native = {'CODEX_THREAD_ID': 'native-B'}
            app.error('session.assign_role', 'state_busy', request('author'))
            assert app.facts() == before
            writer.rollback()
        assert app.facts() == before
        assigned(app, identity='native-B')
    with Installation() as app:
        app.prepare()
        initial(app)
        retained = app.retain()
        postwrite_failure(app)
        assert all(app.facts()['rows'][name] == rows for name, rows in retained.items())


def caller_and_input_boundaries():
    with Installation() as app:
        app.prepare()
        initial(app)
        before = app.facts()
        for case in fixture('assignment-invalid.json'):
            app.error('session.assign_role', 'invalid_request', case['input'],
                      coordinates={'@PATH@': case['path']})
            assert app.facts() == before
        unknown = request('author')
        unknown['role'] = 'outside'
        app.error('session.assign_role', 'role_not_allowed', unknown,
                  coordinates={'@REQUESTED@': 'outside'})
        assert app.facts() == before
        app.native = {'CODEX_THREAD_ID': 'unestablished'}
        app.error('session.assign_role', 'session_not_found', request('author'))
        assert app.facts() == before
        for native, reason in [({}, 'native_missing'),
                               ({'CODEX_THREAD_ID': ''}, 'native_invalid'),
                               ({'CODEX_THREAD_ID': 'one', 'CODEX_SESSION_ID': 'two'}, 'native_conflict')]:
            app.native = native
            app.error('session.ensure', 'invalid_caller', coordinates={'@REASON@': reason})
            assert app.facts() == before
        binding = app.generated()
        app.native = {'CODEX_THREAD_ID': 'native-A'}
        app.error('session.ensure', 'invalid_caller',
                  coordinates={'@REASON@': 'native_generated_conflict'})
        assert not binding.exists() and app.facts() == before
        app.native = {}
        binding.write_text('{')
        app.error('session.ensure', 'invalid_caller', coordinates={'@REASON@': 'binding_invalid'})
        assert binding.read_text() == '{' and app.facts() == before
        occupied = app.root / 'occupied-parent'
        literal = b'Operator file retained.\n'
        occupied.write_bytes(literal)
        app.config['caller']['binding_file'] = str(occupied / 'caller.json')
        try:
            _, record, body = app.call('session.ensure')
            assert body['error']['code'] == 'caller_io', record
            reason = body['error']['details']['reason']
            assert isinstance(reason, str) and reason
            assert body == bind(fixture('errors.json')['caller_io'], {'@REASON@': reason}), record
            assert app.facts() == before
        finally:
            assert occupied.read_bytes() == literal


def unicode_boundaries():
    raw = (FIX / 'assignment-byte-packet.json').read_bytes()
    count = len(raw)
    assert count > len(raw.decode('utf-8'))
    for extra in [False, True]:
        with Installation() as app:
            app.prepare()
            app.config['roles'] = ['автор', 'проверяющий']
            initial(app)
            before = app.facts()
            app.config['limits']['packet_bytes'] = count
            if not extra:
                app.ok('session.assign_role', {'session': session('utf8'), 'changed': True}, raw=raw)
                app.assert_session(session('utf8'))
            else:
                app.error('session.assign_role', 'packet_limit', raw=raw + b' ')
                assert app.facts() == before


def boundary_checks():
    state_boundaries()
    caller_and_input_boundaries()
    unicode_boundaries()


def retained_checks():
    with Installation() as app:
        app.prepare()
        initial(app)
        retained = app.retain()
        assigned(app)
        app.ok('session.assign_role', {'session': session('author'), 'changed': False}, value=request('author'))
        app.ok('session.status', {'session': session('author')})
        assert all(app.facts()['rows'][name] == rows for name, rows in retained.items())
    with Installation() as app:
        app.prepare()
        initial(app)
        app.execute('UPDATE poise_sessions SET status=? WHERE id=?', ('closed', 'native-A'))
        before = app.facts()
        app.ok('session.status', {'session': session('closed-native')})
        app.ok('session.ensure', {'session': session('closed-native'), 'created': False})
        app.error('session.assign_role', 'session_closed', request('author'))
        assert app.facts() == before
    with Installation() as app:
        app.prepare()
        initial(app)
        assigned(app, 'reviewer')
        before = app.facts()
        app.config['roles'] = ['author']
        app.ok('session.status', {'session': session('reviewer')})
        app.ok('session.ensure', {'session': session('reviewer'), 'created': False})
        app.error('session.assign_role', 'role_locked', request('author'),
                  coordinates={'@CURRENT@': 'reviewer', '@REQUESTED@': 'author'})
        assert app.facts() == before


GROUPS = [('native', native_checks), ('generated', generated_checks),
          ('isolation', isolation_checks), ('concurrency', concurrency_checks),
          ('boundaries', boundary_checks), ('retained-facts', retained_checks)]
