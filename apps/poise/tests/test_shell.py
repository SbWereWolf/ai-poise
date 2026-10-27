#!/usr/bin/env python3
"""Maintained eight Session-entry smoke checks and four documentation checks."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from generic_checks import APP, LOG
from session_support import CALLS, OBSERVATIONS, Installation, fixture
from state_sql import SQL_LOG


def executable():
    with Installation() as app:
        entry = app.install / 'bin/poise'
        assert entry.is_file() and os.access(entry, os.X_OK)
        app.prepare()
        app.error('session.status', 'session_not_found', direct=True)


def isolated():
    with Installation() as app:
        app.prepare()
        before = app.facts()
        app.error('session.status', 'session_not_found', cwd=app.root)
        assert app.facts() == before
        assert not (app.root / 'src/poise').exists()


def two_configs():
    with Installation() as first, Installation() as second:
        for app in [first, second]:
            app.prepare()
            before = app.facts()
            app.error('session.status', 'session_not_found')
            assert app.facts() == before
        assert first.database != second.database


def required_settings():
    for case in fixture('config-invalid.json'):
        with Installation() as app:
            app.prepare()
            before = app.facts()
            target = app.config
            for key in case['path'][:-1]:
                target = target[key]
            field = case['path'][-1]
            if case.get('remove'):
                del target[field]
            else:
                target[field] = case['value']
            app.error('session.status', 'invalid_config', coordinates={'@PATH@': case['pointer']})
            assert app.facts() == before


def unknown_settings():
    with Installation() as app:
        app.prepare()
        before = app.facts()
        app.config['operator_note'] = {'retained': 'root'}
        app.config['caller']['operator_note'] = 'caller'
        app.config['limits']['operator_note'] = 'limits'
        app.error('session.status', 'session_not_found')
        assert app.facts() == before


def packet_bound():
    raw = b'{"schema":"stride-poise/v1","operation":"session.status","input":{}}\n'
    for extra in [False, True]:
        with Installation() as app:
            app.prepare()
            before = app.facts()
            app.config['limits']['packet_bytes'] = len(raw)
            app.error('session.status', 'packet_limit' if extra else 'session_not_found',
                      raw=raw + b' ' if extra else raw)
            assert app.facts() == before


def invalid_json():
    with Installation() as app:
        app.prepare()
        before = app.facts()
        app.error('session.status', 'invalid_json', raw='{')
        assert app.facts() == before


def exact_packet():
    with Installation() as app:
        app.prepare()
        before = app.facts()
        app.error('unknown', 'invalid_request', coordinates={'@PATH@': '/operation'})
        packet = app.packet('session.status', {})
        packet['extra'] = True
        app.error('session.status', 'invalid_request', raw=json.dumps(packet), coordinates={'@PATH@': '/'})
        app.error('session.status', 'invalid_request', value={'extra': True}, coordinates={'@PATH@': '/input'})
        for encoding in ['ascii', 'latin-1']:
            app.extra_environment['PYTHONIOENCODING'] = encoding
            packet = app.packet('session.status', {})
            packet['schema'] = 'иная/схема'
            app.error('session.status', 'unsupported_schema', raw=json.dumps(packet, ensure_ascii=False),
                      coordinates={'@SCHEMA@': 'иная/схема'})
        assert app.facts() == before


BEHAVIOR = [('entry', executable), ('isolation', isolated), ('two-configs', two_configs),
            ('required-settings', required_settings), ('unknown-settings', unknown_settings),
            ('packet-bytes', packet_bound), ('invalid-json', invalid_json), ('exact-packet', exact_packet)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=['behavior', 'documentation'])
    parser.add_argument('--output-root')
    args = parser.parse_args()
    output = Path(args.output_root or os.environ['POISE_RUN_OUTPUT_DIR'])
    report = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=APP,
              text=True).strip(), 'suite': args.suite, 'errors': [], 'failures': [],
              'calls': CALLS, 'state_setup_calls': LOG, 'sql': SQL_LOG, 'observations': OBSERVATIONS}
    if args.suite == 'documentation':
        path = APP / 'docs/shell-poi.md'
        text = path.read_text()
        report['document'] = {'path': str(path), 'text': text}
        checks = [('commands', ['bin/poise', '--config', 'session.ensure', 'session.assign_role', 'session.status']),
                  ('settings', ['stride-poise-config/v1', 'installation_id', 'session_store', 'caller', 'roles',
                                'packet_bytes', 'sqlite_timeout_seconds']),
                  ('owners', ['Session', 'Poise', 'Requirements', 'Sequencing', 'Execution']),
                  ('boundaries', ['poise-state', 'poise-generic', 'hooks', 'reader', 'apps/poise/tests/test_session.py'])]
        for name, words in checks:
            missing = [word for word in words if word not in text]
            if missing:
                report['failures'].append({'test': name, 'missing': missing})
        count = 4
    else:
        for name, check in BEHAVIOR:
            try:
                check()
            except AssertionError as error:
                report['failures'].append({'test': name, 'reason': str(error)})
            except Exception as error:
                report['errors'].append({'test': name, 'reason': type(error).__name__ + ': ' + str(error)})
        count = 8
    output.mkdir(parents=True, exist_ok=True)
    (output / 'shell-details.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'errors': report['errors'], 'failures': report['failures'], 'tests_run': count},
                     ensure_ascii=False, separators=(',', ':')))
    return 1 if report['errors'] or report['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
