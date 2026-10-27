"""Public peer/command/MCP observations, never import implementation oracles."""
from copy import deepcopy
import json
from pathlib import Path

from generic_checks import bind
from hook_support import Installation, assert_error, equal, expected_session, fixture


def receipt(app, body, mode='primary'):
    files = app.outputs(body)
    matches = []
    for path, raw in files.items():
        if Path(path).name == 'receipt.json':
            value = json.loads(raw)
            if mode in value.get('argv', []):
                matches.append(value)
    assert len(matches) == 1, matches
    result = matches[0]
    assert result['cwd'] == str(app.state.cwd)
    assert result['environment'] == app.config['hooks']['external']['environment']
    equal(result['input'], fixture('request.json'))
    assert result['argv'] == app.config['hooks']['external']['argv']
    assert files[result['stderr']] == (Path(__file__).parent / 'fixtures/hooks/primary-stderr.txt').read_bytes() or result['launch_error']
    return result, files


def primary_effect(app, request=None):
    values = [value for value in app.effects() if value.get('mode') == 'primary']
    assert len(values) == 1, values
    actual = values[0]
    equal(actual['input'], fixture('request.json') if request is None else request)
    assert actual['cwd'] == str(app.state.cwd)
    assert actual['environment'] == app.config['hooks']['external']['environment']
    return actual


def public_call():
    for variant in ['accepted', 'changes', 'failed', 'null', 'false', 'empty', 'array']:
        with Installation() as app:
            app.prepare()
            app.ensure()
            app.external(variant)
            before = app.facts()
            body = app.intercept()
            app.assert_intercept(body, fixture(variant + '.json'), exit_code=7 if variant == 'failed' else 0,
                                 status='unavailable' if variant == 'failed' else 'ok',
                                 code='external_unavailable' if variant == 'failed' else None)
            primary_effect(app)
            observed, files = receipt(app, body)
            assert observed['exit_code'] == (7 if variant == 'failed' else 0)
            assert observed['timed_out'] is False and observed['launch_error'] is None
            assert files[observed['stdout']] == (Path(__file__).parent / 'fixtures/hooks' / (variant + '.json')).read_bytes()
            equal(app.facts(), before)
    # The external object is opaque and may carry its own unrelated session_id.
    with Installation() as app:
        app.prepare()
        app.ensure()
        app.external()
        value = fixture('request.json')
        value['session_id'] = 'external-owned-identity'
        body = app.intercept({'workspace': str(app.state.cwd), 'request': value})
        app.assert_intercept(body, fixture('accepted.json'))
        primary_effect(app, value)
    for variant, code, action in [('malformed', 'external_response', 'configure:external'),
                                  ('timeout', 'provider_timeout', 'adjust:provider'),
                                  ('large', 'provider_output_limit', 'adjust:provider'),
                                  ('missing', 'provider_unavailable', 'configure:provider')]:
        with Installation() as app:
            app.prepare()
            app.ensure()
            generic = app.external(variant)
            if variant == 'timeout':
                generic['limits']['provider_timeout_seconds'] = 0.5
            if variant == 'large':
                generic['limits']['provider_output_bytes'] = 100
            if variant == 'missing':
                app.config['hooks']['external']['argv'] = [str(app.root / 'missing-executable')]
            app.generic_path.write_text(json.dumps(generic))
            before = app.facts()
            body = app.intercept()
            assert body['result']['external'] is None, body
            equal(body['result']['session'], expected_session('native-A'))
            equal(body['result']['capability_checks'], fixture('empty-probes.json'))
            details = body['error']['details']
            if variant == 'timeout':
                equal(details, {'id': 'external', 'limit': 0.5})
            elif variant == 'large':
                equal(details, {'id': 'external', 'limit': 100})
            else:
                assert isinstance(details['reason'], str) and details['reason']
                assert set(details) == ({'id', 'reason'} if variant == 'missing' else {'reason'})
                if variant == 'missing':
                    assert details['id'] == 'external'
            assert_error(body, code, details, action, result=body['result'], evidence=body['evidence'])
            files = app.outputs(body)
            if variant == 'missing':
                assert app.effects() == []
                receipts = [json.loads(raw) for path, raw in files.items() if Path(path).name == 'receipt.json']
                assert len(receipts) == 1 and receipts[0]['exit_code'] is None and receipts[0]['launch_error']
            else:
                primary_effect(app)
                observed, files = receipt(app, body)
                expected = b'PRIMARY-START\n' if variant == 'timeout' else (
                    Path(__file__).parent / 'fixtures/hooks' / ('malformed.txt' if variant == 'malformed' else 'large.json')).read_bytes()
                assert files[observed['stdout']] == expected
                if variant == 'timeout':
                    assert observed['timed_out'] is True and observed['exit_code'] < 0
                else:
                    assert observed['exit_code'] == 0 and observed['timed_out'] is False
            equal(app.facts(), before)
    with Installation() as app:
        app.prepare()
        binding = app.generated('generated-hook-caller.json')
        ensured = app.call('session.ensure')[2]
        assert ensured['status'] == 'ok', ensured
        current = ensured['result']['session']
        equal(current, expected_session(current['id'], origin='generated'))
        binding_raw = binding.read_bytes()
        app.external()
        before = app.facts()
        app.assert_intercept(app.intercept(), fixture('accepted.json'), session=current)
        primary_effect(app)
        equal(app.facts(), before)
        assert binding.read_bytes() == binding_raw
    interception_preconditions()


def interception_preconditions():
    with Installation() as app:
        app.prepare()
        app.ensure()
        app.external(probes=[app.probe()])
        before = app.facts()
        for case in fixture('intercept-invalid.json'):
            value = {'workspace': str(app.state.cwd), 'request': fixture('request.json')}
            if case['change'] == 'remove':
                del value[case['field']]
            else:
                value[case['field']] = bind(case['value'], {'@MISSING@': str(app.root / 'missing-directory')})
            assert_error(app.intercept(value), 'invalid_request', {'path': case['pointer']},
                         'correct:request', status='rejected')
            assert app.effects() == []
            equal(app.facts(), before)
        app.config['hooks']['external'] = None
        assert_error(app.intercept(), 'external_not_configured', {}, 'configure:external')
        equal(app.facts(), before)
        assert app.effects() == []
        app.external()
        first = app.probe('valid-first')
        second = app.probe('malformed-second')
        del second['argv']
        app.config['hooks']['probes'] = [first, second]
        assert_error(app.intercept(), 'invalid_config', {'path': '/hooks/probes/1'},
                     'correct:config', status='rejected')
        equal(app.facts(), before)
        assert app.effects() == []
    for generated in [False, True]:
        with Installation() as app:
            app.prepare()
            binding = app.generated() if generated else None
            if not generated:
                app.native = {'CODEX_THREAD_ID': 'native-A'}
            app.external(probes=[app.probe()])
            before = app.facts()
            assert_error(app.intercept(), 'session_not_found', {}, 'ensure:session')
            equal(app.facts(), before)
            assert app.effects() == []
            if generated:
                assert not binding.exists()


def observation(app, actual, identity, mode='available', *, mcp=False):
    available = mode != 'unavailable'
    target = bind(fixture('probe-observation.json'), {
        '@PROBE@': identity, '@STATUS@': 'available' if available else 'unavailable',
        '@WORKSPACE@': str(app.state.cwd), '@RECEIPT@': actual['receipt'],
        '@OBSERVATION@': 'mcp_smoke_passed' if mcp else 'command_executed' if available else 'probe_failed',
        '@REASON@': None if available else 'Actual command exit did not match'})
    if mcp:
        target['version'] = '2.4'
        target['tools'] = ['find_symbol', 'describe_project']
    assert isinstance(actual['receipt'], str) and Path(actual['receipt']).is_file(), actual
    equal(actual, target)
    if mode == 'after-primary':
        result = json.loads(Path(actual['receipt']).read_text())
        out = json.loads(Path(result['stdout']).read_text())
        equal(out, {'checkout': str(app.state.cwd), 'message': 'READY', 'primary_stopped': True})


def advisory_probes():
    for primary in ['accepted', 'failed', 'timeout', 'malformed']:
        with Installation() as app:
            app.prepare()
            app.ensure()
            after = app.probe('after-primary', 'after-primary')
            after['json_assertions'].append({'path': ['primary_stopped'], 'equals': True})
            probes = [after, app.probe('required-missing', 'unavailable')]
            if primary == 'accepted':
                probes.append(app.probe('mcp', mcp=True))
            generic = app.external(primary, probes)
            if primary == 'timeout':
                generic['limits']['provider_timeout_seconds'] = 0.5
                for probe in app.config['hooks']['probes']:
                    probe['timeout_seconds'] = 0.5
                app.generic_path.write_text(json.dumps(generic))
            before = app.facts()
            body = app.intercept()
            summary = body['result']['capability_checks']
            assert set(summary) == {'ready', 'missing_required', 'observations'}
            assert summary['ready'] is False
            equal(summary['missing_required'], ['required-missing'])
            assert len(summary['observations']) == len(probes)
            observation(app, summary['observations'][0], 'after-primary', 'after-primary')
            observation(app, summary['observations'][1], 'required-missing', 'unavailable')
            if primary == 'accepted':
                observation(app, summary['observations'][2], 'mcp', mcp=True)
            if primary in ('accepted', 'failed'):
                app.assert_intercept(body, fixture(primary + '.json'), exit_code=7 if primary == 'failed' else 0,
                    summary=summary, code='external_unavailable' if primary == 'failed' else None,
                    status='unavailable' if primary == 'failed' else 'ok')
            else:
                assert body['result']['external'] is None
                equal(body['result']['session'], expected_session('native-A'))
                code = 'provider_timeout' if primary == 'timeout' else 'external_response'
                details = {'id': 'external', 'limit': 0.5} if primary == 'timeout' else body['error']['details']
                if primary == 'malformed':
                    assert set(details) == {'reason'} and isinstance(details['reason'], str) and details['reason']
                assert_error(body, code, details, 'adjust:provider' if primary == 'timeout' else 'configure:external',
                             result=body['result'], evidence=body['evidence'])
            primary_effect(app)
            effects = app.effects()
            assert effects[0]['mode'] == 'primary'
            assert [item['variant'] for item in effects if item.get('mode') == 'probe'] == ['after-primary', 'unavailable']
            observed, files = receipt(app, body)
            if primary == 'timeout':
                assert observed['timed_out'] is True and observed['exit_code'] < 0
                assert files[observed['stdout']] == b'PRIMARY-START\n'
            elif primary == 'malformed':
                assert observed['exit_code'] == 0 and observed['timed_out'] is False
                assert files[observed['stdout']] == b'{'
            equal(app.facts(), before)
