"""Six independent external regressions for the reviewed generic boundaries."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from generic_checks import APP, FIX, Installation, LOG, PY, bind, peer_argv, process, read


FINITE = FIX / 'finite_mcp_peer.py'


def call(app, operation, value):
    try:
        return app.call(operation, value)
    except json.JSONDecodeError as exc:
        raise AssertionError('Public CLI must return one JSON envelope; actual IO retained') from exc


def receipt(app):
    return json.loads(app.evidence_text('receipt'))


def limit_error(code, body, identity, value, error):
    assert code == 1 and body['status'] == 'unavailable', body
    expected = bind(read(FIX / 'regression-errors.json')['limit'],
                    {'@ERROR@': error, '@ID@': identity, '@LIMIT@': value})
    assert body['error'] == expected, body
    result = body['result']
    assert result['ready'] is False and result['missing_required'] == [identity], result
    assert len(result['observations']) == 1, result
    observed = result['observations'][0]
    assert observed['id'] == identity and observed['status'] == 'unavailable', observed
    assert observed['receipt'] and observed['reason'], observed


def required_command(mode, identity, timeout, maximum):
    with Installation() as app:
        probe = app.probe(identity, mode)
        probe.update(timeout_seconds=timeout, max_output_bytes=maximum,
                     stdout_contains=[], stderr_contains=[], json_assertions=[], project_bound=False)
        code, body = call(app, 'capabilities', {'workspace': str(app.project), 'probes': [probe]})
        actual = receipt(app)
        assert actual['timeout_seconds'] == timeout and actual['max_output_bytes'] == maximum
        if mode == 'timeout':
            assert actual['timed_out'] is True
            assert app.evidence_text('stdout') == 'START\n'
            assert app.evidence_text('stderr') == 'TIMEOUT-DETAIL\n'
            pid = json.loads(app.trace.read_text().splitlines()[0])['pid']
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError('Timed-out fixture child remains live')
            limit_error(code, body, identity, timeout, 'provider_timeout')
        else:
            assert actual['exit_code'] == 0
            assert json.loads(app.evidence_text('stdout')) == read(FIX / 'regression-large-expected.json')
            assert app.evidence_text('stderr') == 'OUTPUT-DETAIL-END\n'
            limit_error(code, body, identity, maximum, 'provider_output_limit')


def finite_mcp(expected):
    with Installation() as app:
        probe = app.mcp(call=False)
        probe.update(id='finite-mcp', argv=[PY, '-I', '-S', '-B', str(FINITE)],
                     timeout_seconds=3, max_output_bytes=32768)
        probe['mcp'].update(max_messages=1000, shutdown_seconds=0.2)
        code, body = call(app, 'capabilities', {'workspace': str(app.project), 'probes': [probe]})
        actual = app.evidence_text('stdout')
        assert actual == expected, {'actual_bytes': len(actual.encode()),
                                    'expected_bytes': len(expected.encode())}
        assert app.evidence_text('stderr') == 'FINITE-PEER-FINISHED\n'
        assert receipt(app)['exit_code'] == 0, receipt(app)
        limit_error(code, body, 'finite-mcp', 32768, 'provider_output_limit')


def result_write_failure():
    with Installation() as app:
        occupied = app.root / 'results-file'
        content = b'Existing regular file stays intact.\n'
        occupied.write_bytes(content)
        app.config['results_root'] = str(occupied)
        app.config['limits']['result_bytes'] = 32
        try:
            code, body = call(app, 'skills.list', app.skills())
            assert code == 1 and body['status'] == 'unavailable', body
            assert body['error']['code'] == 'io_unavailable', body
            assert body['error']['action'] == 'configure:files', body
            assert body['error']['details']['reason'], body
            assert body['result'] is None and body['evidence'] == [], body
        finally:
            assert occupied.read_bytes() == content


def nul_config(field):
    with Installation() as app:
        if field == 'results_root':
            app.config['results_root'] = 'bad\0root'
            pointer = '/results_root'
        else:
            app.config['files']['stdout'] = 'bad\0name'
            pointer = '/files/stdout'
        code, body = call(app, 'context', app.context(app.external('large')))
        assert not app.trace.exists(), 'Invalid known configuration launched a provider'
        assert code == 2 and body['status'] == 'rejected', body
        expected = bind(read(FIX / 'regression-errors.json')['config'], {'@PATH@': pointer})
        assert body['error'] == expected, body
        assert body['result'] is None and body['evidence'] == [], body


def qualify():
    """A separate draining caller establishes finite protocol bytes and fixture health."""
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError('Explicit CPython 3.13 prerequisite required')
    for mode in ['large', 'timeout']:
        child, _ = process(peer_argv(mode), raw='')
        if child.returncode != 0:
            raise RuntimeError('Command fixture did not qualify: ' + mode)
        if mode == 'large':
            good = (json.loads(child.stdout) == read(FIX / 'regression-large-expected.json')
                    and child.stderr == 'OUTPUT-DETAIL-END\n')
        else:
            good = child.stdout == 'START\nUNREACHED-END\n' and child.stderr == 'TIMEOUT-DETAIL\n'
        if not good:
            raise RuntimeError('Command fixture output differs: ' + mode)
    exchange = read(FIX / 'finite-mcp-exchange.json')
    child, record = process([PY, '-I', '-S', '-B', FINITE],
                            raw=''.join(json.dumps(value) + '\n' for value in exchange))
    if child.returncode or child.stderr != 'FINITE-PEER-FINISHED\n':
        raise RuntimeError('Finite MCP fixture did not finish normally')
    lines = child.stdout.splitlines(keepends=True)
    if len(lines) != 402:
        raise RuntimeError('Finite MCP fixture must emit 400 notifications and two replies')
    for index, line in enumerate(lines[:400]):
        notification = read(FIX / 'regression-notification-expected.json')
        notification['params']['index'] = index
        if json.loads(line) != notification:
            raise RuntimeError('Literal finite notification differs')
    expected_initialize = read(FIX / 'finite-mcp-initialize.json')
    if json.loads(lines[400]) != expected_initialize:
        raise RuntimeError('Literal initialize reply differs')
    if json.loads(lines[401]) != read(FIX / 'finite-mcp-tools.json'):
        raise RuntimeError('Literal inventory reply differs')
    expected = ''.join(lines[:401])
    if len(expected.encode()) != 856461:
        raise RuntimeError('Qualified finite initialize prefix byte count differs')
    return expected, record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-root')
    args = parser.parse_args()
    output = Path(args.output_root or os.environ['POISE_RUN_OUTPUT_DIR']).absolute()
    report = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'],
              cwd=APP, text=True).strip(), 'cases': [], 'calls': LOG,
              'errors': [], 'failures': [], 'qualification': None}
    try:
        expected, report['qualification'] = qualify()
    except Exception as exc:
        report['errors'].append({'case': 'qualification', 'reason': repr(exc)})
    else:
        cases = [
            ('required-timeout', lambda: required_command('timeout', 'timed-probe', 0.1, 32768)),
            ('required-output', lambda: required_command('large', 'large-probe', 2, 256)),
            ('finite-mcp-capture', lambda: finite_mcp(expected)),
            ('result-write-failure', result_write_failure),
            ('nul-results-root', lambda: nul_config('results_root')),
            ('nul-output-file', lambda: nul_config('stdout')),
        ]
        for name, check in cases:
            start = len(LOG)
            try:
                check()
            except AssertionError as exc:
                report['failures'].append({'case': name, 'reason': str(exc)})
                status = 'failed'
            except Exception as exc:
                report['errors'].append({'case': name, 'reason': repr(exc)})
                status = 'error'
            else:
                status = 'passed'
            report['cases'].append({'id': name, 'status': status,
                                    'first_call': start, 'call_count': len(LOG) - start})
    status = 'error' if report['errors'] else 'failed' if report['failures'] else 'passed'
    report['status'] = status
    output.mkdir(parents=True, exist_ok=True)
    (output / 'regression-details.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': status, 'checks': 6, 'executed': len(report['cases']),
                      'errors': len(report['errors']), 'failures': len(report['failures'])}))
    return 2 if report['errors'] else 1 if report['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
