#!/usr/bin/env python3
"""Permanent independent black-box UAT qualification and evidence contracts.

The oracle is the literal fixture set and public contract, not application imports.
A failed independent deployment/decoder precondition is an ERROR, never intended RED.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / 'apps/stride-uat'
FIX = APP / 'fixtures/protocol'
CLI = APP / 'bin/stride-uat'
OBSERVATIONS = []


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def fixture(name):
    return json.loads((FIX/name).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def metadata(path):
    p = Path(path)
    st = p.lstat()
    return (stat.S_IMODE(st.st_mode), st.st_ino, st.st_mtime_ns,
            os.readlink(p) if p.is_symlink() else sha(p))


def inventory(root):
    root = Path(root)
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): metadata(p)
            for p in sorted(root.rglob('*')) if p.is_file() or p.is_symlink()}


def active_uid_processes(uid):
    result = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            text = (p/'status').read_text()
            m = re.search(r'^Uid:\s+(\d+)', text, re.MULTILINE)
            if m and int(m.group(1)) == uid and not re.search(r'^State:\s+Z', text, re.MULTILINE):
                result.append(int(p.name))
        except (OSError, ValueError):
            pass
    return result


class Case:
    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uat-T030-')
        self.root = Path(self.temp.name)
        try:
            if os.name != 'posix' or os.geteuid() != 0:
                raise RuntimeError('test setup requires the declared privileged Linux supervisor')
            self.root.chmod(0o755)
            self.test_root = self.root/'test'; self.test_root.mkdir(mode=0o711)
            self.inputs = self.root/'inputs'; self.inputs.mkdir(mode=0o755)
            self.source = self.inputs/'source'; shutil.copytree(FIX/'source', self.source)
            self.delivery = self.inputs/'delivery'; shutil.copytree(FIX/'delivery', self.delivery)
            self.protected = self.root/'production'; self.protected.mkdir(mode=0o700)
            self.canary = self.protected/'production-canary.db'
            self.canary.write_bytes(b'OWN TEST CANARY, NOT AN APPLICATION DATABASE\n')
            self.canary.chmod(0o600)
            self.canary_before = metadata(self.canary)
            self.adapter = self.source/'adapter.py'
            self.module = self.source/'module.py'
            self.subject = self.delivery/'subject.py'
            self.python = Path('/usr/bin/python3').resolve(strict=True)
            self.launcher = Path('/usr/bin/setpriv').resolve(strict=True)
            if self.python.parent != self.launcher.parent:
                raise RuntimeError('fixture binary input root must explicitly contain both selected executables')
            self.uid = next((uid for uid in range(62030, 62130) if not active_uid_processes(uid)), None)
            if self.uid is None:
                raise RuntimeError('no unused dedicated test uid in fixture allocation range')
            self.gid = self.uid
            self.program_path = self.inputs/'program.json'
            self.program = fixture('program.json')
            release = platform.freedesktop_os_release()
            replacements = {
                '@OS_ID@': release['ID'], '@OS_VERSION@': release['VERSION_ID'],
                '@MAJOR@': sys.version_info.major, '@MINOR@': sys.version_info.minor,
                '@PYTHON@': str(self.python), '@LAUNCHER@': str(self.launcher),
                '@BINARY_ROOT@': str(self.python.parent), '@UID@': self.uid, '@GID@': self.gid,
                '@ADAPTER@': str(self.adapter), '@SUBJECT@': str(self.subject),
                '@MODULE@': str(self.module), '@SOURCE@': str(self.source),
                '@DELIVERY@': str(self.delivery), '@PROTECTED_ROOT@': str(self.protected),
                '@CANARY@': str(self.canary), '@TEST_ROOT@': str(self.test_root),
                '@LEASE_ROOT@': str(self.test_root/'leases')}
            def substitute(value):
                if isinstance(value, str):
                    return replacements.get(value, value)
                if isinstance(value, list):
                    return [substitute(x) for x in value]
                if isinstance(value, dict):
                    return {k: substitute(v) for k, v in value.items()}
                return value
            self.config = substitute(fixture('config.json'))
            self.config_path = self.root/'uat.json'
            self.state = self.test_root/'state'; self.workspace = self.test_root/'workspace'
            self.save()
            self.config_path.chmod(0o600)
            # This is an independent actual kernel/deployment prerequisite, not a UAT result.
            probe = self.inputs/'probe.py'; shutil.copyfile(FIX/'probe.py', probe)
            paths = [self.protected, self.canary, self.source, self.adapter, self.delivery, self.subject]
            argv = [str(self.launcher), '--reuid', str(self.uid), '--regid', str(self.gid),
                    '--clear-groups', '--bounding-set=-all', '--inh-caps=-all',
                    '--ambient-caps=-all', '--no-new-privs', str(self.python), '-I', str(probe),
                    *map(str, paths)]
            p = subprocess.run(argv, input=b'', capture_output=True, timeout=10,
                               env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, check=False)
            OBSERVATIONS.append({'kind': 'independent_deployment_probe', 'argv': argv,
                                 'exit_code': p.returncode, 'stdout_hex': p.stdout.hex(),
                                 'stderr_hex': p.stderr.hex()})
            if p.returncode or p.stderr:
                raise RuntimeError('actual setpriv fixture probe failed: '+repr((p.returncode, p.stderr)))
            obs = json.loads(p.stdout.decode('utf-8'))
            valid = (obs['uid'] == obs['euid'] == self.uid and obs['gid'] == obs['egid'] == self.gid
                     and obs['groups'] == [] and obs['no_new_privs'] == 1
                     and all(int(v, 16) == 0 for v in obs['caps'].values())
                     and not any(obs['access'][str(self.protected)].values())
                     and not any(obs['access'][str(self.canary)].values())
                     and all(not obs['access'][str(x)]['write'] for x in paths[2:])
                     and all(obs['access'][str(x)]['read'] for x in paths[2:]))
            if not valid:
                raise RuntimeError('actual reduced identity did not satisfy fixture prerequisites: '+repr(obs))
            return self
        except BaseException:
            self.temp.cleanup()
            raise

    def __exit__(self, *args):
        self.temp.cleanup()

    def save(self):
        write(self.config_path, self.config); write(self.program_path, self.program)

    def mode(self, mode):
        self.config['commands']['external-cases']['argv'][3] = mode
        self.save()

    def graph(self):
        self.graph_path = self.inputs/'selected-graph.json'
        shutil.copyfile(FIX/'graph.json', self.graph_path)
        self.config['inputs']['graph-source'] = {'kind': 'file', 'path': str(self.graph_path)}
        self.program['graphs'] = [{'id': 'only-graph', 'input_id': 'graph-source', 'required_step_ids': ['first']}]
        self.program['steps'][0]['graph_id'] = 'only-graph'
        self.save()

    def packet(self, operation='run', step_limit=0, revision=None, run_id='run-1'):
        data = {'program_path': str(self.program_path), 'profile_id': 'isolated'}
        if operation != 'validate':
            data.update(run_id=run_id, step_limit=step_limit)
        if operation == 'resume':
            data['expected_revision'] = revision
        if operation == 'inspect':
            data = {'run_id': run_id}
        return {'schema': 'stride-uat/v1', 'operation': operation, 'input': data}

    def environment(self, outer=None):
        env = os.environ.copy()
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                   PYTHONPATH='', POISE_T030_AMBIENT_SECRET='must-not-be-inherited')
        env.update(outer or {})
        return env

    def call(self, operation='run', step_limit=0, revision=None, run_id='run-1', raw=None, outer=None):
        payload = raw if raw is not None else (compact(self.packet(operation, step_limit, revision, run_id))+'\n').encode('utf-8')
        p = subprocess.run([str(CLI), '--config', str(self.config_path)], cwd=self.root,
                           env=self.environment(outer), input=payload, capture_output=True,
                           timeout=45, check=False)
        OBSERVATIONS.append({'kind': 'call', 'operation': operation, 'request_hex': payload.hex(),
                             'exit_code': p.returncode, 'stdout_hex': p.stdout.hex(), 'stderr_hex': p.stderr.hex()})
        # Keep decoder failures out of the assertion-failure channel.
        body = json.loads(p.stdout.decode('utf-8'))
        if not isinstance(body, dict):
            raise ValueError('public response must be a single JSON object')
        assert metadata(self.canary) == self.canary_before, 'declared production canary changed'
        assert not (self.protected/'MUST_NOT_EXIST').exists(), 'adapter created a production file'
        return p, body

    def raw_protocol(self, run_id='run-1'):
        path = self.state/run_id/'protocol.json'
        result = json.loads(path.read_text(encoding='utf-8'))
        OBSERVATIONS.append({'kind': 'protocol', 'path': str(path), 'sha256': sha(path), 'value': result})
        return result

    def observation(self, step='first', run_id='run-1'):
        path = self.workspace/run_id/step/'adapter-observation.json'
        result = json.loads(path.read_text(encoding='utf-8'))
        OBSERVATIONS.append({'kind': 'adapter_observation', 'path': str(path), 'sha256': sha(path), 'value': result})
        return result

    def fault(self, action):
        instrument = self.root/'instrument'; instrument.mkdir()
        shutil.copyfile(FIX/'fault-observer.py', instrument/'sitecustomize.py')
        return {'PYTHONPATH': str(instrument), 'POISE_T030_FAULT_PROTOCOL': str(self.state/'run-1/protocol.json'),
                'POISE_T030_FAULT_TRACE': str(self.root/'fault-trace.jsonl'),
                'POISE_T030_FAULT_ACTION': action, 'POISE_T030_FAULT_SOURCE': str(self.module)}


def check_digest(value):
    assert isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), value


def checked_record(c, record):
    assert set(record) == set(fixture('field-sets.json')['output']), record
    p = Path(record['path'])
    assert p.is_absolute() and (p.is_relative_to(c.state) or p.is_relative_to(c.workspace)), record
    assert not p.is_symlink() and stat.S_ISREG(p.stat().st_mode), record
    raw = p.read_bytes()
    assert type(record['bytes']) is int and record['bytes'] == len(raw), record
    assert record['sha256'] == hashlib.sha256(raw).hexdigest(), record
    return raw


def context_for(journal, index=0):
    for argument in journal['steps'][index]['argv']:
        p = Path(argument)
        if not p.is_absolute() or not p.is_file() or p.stat().st_size > 100000:
            continue
        try:
            value = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, ValueError):
            continue
        if isinstance(value, dict) and value.get('schema') == 'stride-uat-case-input/v1':
            return p, value
    raise AssertionError('saved exact argv lacks its retained public case context')


def check_protocol(c, response):
    j = c.raw_protocol(response['run_id'])
    fields = fixture('field-sets.json')
    assert set(j) == set(fields['protocol']), set(j)
    assert j['schema'] == 'stride-uat-protocol/v1' and j['run_id'] == response['run_id']
    assert j['program_id'] == c.program['program_id'] and j['program_version'] == c.program['version']
    assert j['profile_id'] == 'isolated'
    assert type(j['revision']) is int and j['revision'] >= 1
    assert j['revision'] == response['revision'] and j['verdict'] == response['verdict']
    assert j['graph_results'] == response['graph_results']
    assert j['fingerprint'] == response['fingerprint']; check_digest(j['fingerprint'])
    assert set(j['input_snapshot']) == set(fields['snapshot'])
    snap = j['input_snapshot']
    assert j['fingerprint'] == hashlib.sha256(canonical(snap)).hexdigest(), 'fingerprint does not hash frozen identity'
    assert snap['program_sha256'] == sha(c.program_path)
    for name in ('program_sha256', 'config_sha256', 'environment_sha256'):
        check_digest(snap[name])
    assert snap['isolation_id'] == 'dedicated'
    selected = ['adapter-source', 'installed-subject', 'launcher', 'python']
    if c.program['graphs']:
        selected.append('graph-source')
    assert [x['input_id'] for x in snap['inputs']] == sorted(selected), snap
    for x in snap['inputs']:
        assert set(x) == set(fields['input'])
        assert x['kind'] == c.config['inputs'][x['input_id']]['kind']
        check_digest(x['inventory_sha256'])
    assert snap['graphs'] == ([{'graph_id': 'only-graph', 'input_id': 'graph-source', 'sha256': sha(c.graph_path)}] if c.program['graphs'] else [])
    env = j['environment_observation']
    assert set(env) == set(fields['environment'])
    assert snap['environment_sha256'] == hashlib.sha256(canonical(env)).hexdigest(), 'observed environment digest mismatch'
    actual = platform.freedesktop_os_release()
    assert env['os_id'] == actual['ID'] and env['os_version'] == actual['VERSION_ID']
    assert env['python_version'] == [sys.version_info.major, sys.version_info.minor] and env['uid'] == os.geteuid()
    assert env['configured_environment_sha256'] == hashlib.sha256(canonical(c.config['profiles']['isolated']['environment'])).hexdigest()
    ids = [x['id'] for x in c.program['steps']]
    assert [x['step_id'] for x in j['steps']] == ids[:len(j['steps'])]
    assert j['remaining_step_ids'] == ids[len(j['steps']):]
    attempts = [x['attempt_id'] for x in j['steps']]
    assert len(set(attempts)) == len(attempts) and all(isinstance(x, str) and x for x in attempts)
    assert response['completed_steps'] == sum(x['state'] != 'RUNNING' for x in j['steps'])
    for index, step in enumerate(j['steps']):
        assert set(step) == set(fields['step']), set(step)
        assert set(step['outputs']) == {'result', 'stdout', 'stderr'}
        assert step['state'] in ('RUNNING', 'PASS', 'FAIL', 'BLOCKED')
        assert step['effects'] in ('none', 'possible', 'unknown')
        assert type(step['timed_out']) is bool
        for name in ('exit_code', 'signal'):
            assert step[name] is None or type(step[name]) is int
        if step['state'] == 'RUNNING':
            assert step['exit_code'] is step['signal'] is step['duration_seconds'] is None
            assert all(x is None for x in step['outputs'].values()), step
            continue
        assert type(step['duration_seconds']) in (int, float) and step['duration_seconds'] >= 0
        p, ctx = context_for(j, index)
        assert sha(p) == step['context_sha256']
        assert set(ctx) == set(fields['context'])
        assert ctx['run_id'] == j['run_id'] and ctx['step_id'] == step['step_id']
        assert ctx['application_id'] == 'external-toy'
        assert ctx['public_argv'] == c.config['applications']['external-toy']['public_argv']
        expected = c.config['commands']['external-cases']['argv'] + [str(p) if x == '${context}' else x for x in c.program['steps'][index]['arguments']]
        assert step['argv'] == expected, (step['argv'], expected)
        for record in step['outputs'].values():
            if record is not None:
                checked_record(c, record)
        if step['state'] == 'PASS':
            assert step['exit_code'] == 0 and step['signal'] is None and not step['timed_out']
            assert step['reason'] is None and step['effects'] == 'possible'
            assert all(x is not None for x in step['outputs'].values())
            result = json.loads(checked_record(c, step['outputs']['result']).decode('utf-8'))
            assert set(result) == set(fields['result']) and result['status'] == 'PASS'
            assert result['run_id'] == j['run_id'] and result['step_id'] == step['step_id']
            assert result['application_id'] == 'external-toy'
    if j['verdict'] == 'PASS':
        assert len(j['steps']) == len(ids) and all(x['state'] == 'PASS' for x in j['steps'])
        assert j['stop_reason'] is None
    assert isinstance(j['diagnostics'], list) and all(isinstance(x, str) for x in j['diagnostics'])
    text = compact(j)
    assert 'private-test-token' not in text and 'must-not-be-inherited' not in text
    return j


def reported(c, verdict='PASS', operation='run', step_limit=0, revision=None, replayed=False, run_id='run-1', outer=None):
    p, b = c.call(operation, step_limit, revision, run_id, outer=outer)
    assert p.returncode == {'PASS': 0, 'FAIL': 1, 'BLOCKED': 3, 'RUNNING': 3}[verdict] and p.stderr == b'', (p.returncode, b, p.stderr)
    assert set(b) == set(fixture('field-sets.json')['response']), b
    assert b['schema'] == 'stride-uat/v1' and b['status'] == 'reported' and b['verdict'] == verdict, b
    assert b['run_id'] == run_id and b['protocol_path'] == str(c.state/run_id/'protocol.json')
    assert b['total_steps'] == len(c.program['steps']) and b['replayed'] is replayed
    return b, check_protocol(c, b)


def rejected(c, code, **kwargs):
    before = (inventory(c.state), inventory(c.workspace))
    p, b = c.call(**kwargs)
    assert p.returncode == 2 and p.stderr == b'', (p.returncode, b, p.stderr)
    assert set(b) == {'schema', 'status', 'error'} and b['schema'] == 'stride-uat/v1' and b['status'] == 'rejected', b
    assert set(b['error']) == set(fixture('field-sets.json')['error'])
    assert b['error']['code'] == code and b['error']['retryable'] is (code == 'store_busy'), b
    assert isinstance(b['error']['message'], str) and b['error']['message']
    assert isinstance(b['error']['details'], dict)
    assert before == (inventory(c.state), inventory(c.workspace)), 'rejection rewrote retained run/evidence'
    return b


def prefix(c):
    b, j = reported(c, 'RUNNING', step_limit=1)
    assert b['completed_steps'] == 1 and j['stop_reason'] == 'step_limit'
    assert [s['state'] for s in j['steps']] == ['PASS'] and j['remaining_step_ids'] == ['second']
    assert not (c.workspace/'run-1/second').exists()
    return b, j


def qualification():
    with Case() as c:
        p, b = c.call()
        assert p.returncode == 0 and b.get('verdict') == 'PASS', (
            'protocol.qualification expected PASS; observed exit='+str(p.returncode)+
            ',status='+str(b.get('status'))+',code='+str(b.get('error', {}).get('code')))
        assert p.stderr == b''
        expected = fixture('reported.json')
        expected.update(revision=b['revision'], protocol_path=str(c.state/'run-1/protocol.json'), fingerprint=b['fingerprint'])
        assert b == expected, (b, expected)
        j = check_protocol(c, b)
        assert all(s['state'] == 'PASS' for s in j['steps'])
        first = c.observation(); second = c.observation('second')
        for o in (first, second):
            assert o['public_exit'] == 0 and json.loads(o['public_stdout']) == {'schema': 'toy-public-result/v1', 'value': 'toy-source'}
            assert all(o['denials'].values()), o['denials']


def validation():
    with Case() as c:
        c.graph()
        c.config['profiles']['isolated']['os_ids'] = ['intentionally-not-runtime']; c.save()
        p, b = c.call('validate')
        assert p.returncode == 0 and p.stderr == b'' and b == {
            'schema': 'stride-uat/v1', 'status': 'validated', 'program_id': c.program['program_id'],
            'program_sha256': sha(c.program_path), 'profile_id': 'isolated', 'step_count': 2, 'graph_count': 1}, b
        assert not c.state.exists() and not c.workspace.exists() and not (c.test_root/'leases').exists()
    with Case() as c:
        c.program['steps'][1]['id'] = 'first'; c.save()
        b = rejected(c, 'program_invalid', operation='validate')
        assert isinstance(b['error']['details']['issues'], list)


def resume():
    with Case() as c:
        b, j = prefix(c); before = inventory(c.workspace/'run-1/first')
        b2, j2 = reported(c, operation='resume', revision=b['revision'])
        assert b2['revision'] > b['revision'] and j2['steps'][0] == j['steps'][0]
        assert inventory(c.workspace/'run-1/first') == before, 'resume changed successful prefix'
        assert all(c.observation('second')['denials'].values())
        complete = (inventory(c.state), inventory(c.workspace))
        b3, _ = reported(c, operation='resume', revision=b2['revision'], replayed=True)
        assert b3 == {**b2, 'replayed': True}
        assert complete == (inventory(c.state), inventory(c.workspace))


def replay():
    with Case() as c:
        b, _ = prefix(c); before = (inventory(c.state), inventory(c.workspace))
        b2, _ = reported(c, 'RUNNING', step_limit=0, replayed=True)
        assert b2 == {**b, 'replayed': True} and before == (inventory(c.state), inventory(c.workspace))
    with Case() as c:
        b, _ = reported(c); before = (inventory(c.state), inventory(c.workspace))
        b2, _ = reported(c, step_limit=1, replayed=True)
        assert b2 == {**b, 'replayed': True} and before == (inventory(c.state), inventory(c.workspace))


def revision():
    with Case() as c:
        b, _ = prefix(c)
        old = b['revision']-1
        result = rejected(c, 'revision_conflict', operation='resume', revision=old)
        assert result['error']['details'] == {'expected': old, 'actual': b['revision']}
        future = b['revision']+100
        result = rejected(c, 'revision_conflict', operation='resume', revision=future)
        assert result['error']['details'] == {'expected': future, 'actual': b['revision']}
        reported(c, operation='resume', revision=b['revision'])

    with Case() as c:
        b, j = prefix(c)
        with Path(j['steps'][0]['outputs']['stdout']['path']).open('ab') as out:
            out.write(b'corrupt retained prefix')
        stale = b['revision'] - 1
        result = rejected(c, 'revision_conflict', operation='resume', revision=stale)
        assert result['error']['details'] == {'expected': stale, 'actual': b['revision']}
        rejected(c, 'evidence_mismatch', operation='resume', revision=b['revision'])


def changed_input(c, mutate):
    b, _ = prefix(c)
    mutate(c)
    result = rejected(c, 'input_mismatch', operation='resume', revision=b['revision'])
    assert result['error']['details']['run_id'] == 'run-1'
    assert result['error']['details']['changed'], result
    assert not (c.workspace/'run-1/second').exists()


def source():
    for change in ('content', 'untracked', 'mode', 'link'):
        with Case() as c:
            def mutate(case):
                if change == 'content': case.module.write_bytes(case.module.read_bytes()+b'\n# changed source\n')
                elif change == 'untracked': (case.source/'untracked.txt').write_text('new selected member\n')
                elif change == 'mode': case.module.chmod(0o600)
                else: (case.source/'alias.py').symlink_to('module.py')
            changed_input(c, mutate)


def configuration():
    for change in ('limit', 'binding', 'exclusion', 'profile'):
        with Case() as c:
            def mutate(case):
                if change == 'limit': case.config['limits']['stdout_bytes'] += 1
                elif change == 'binding': case.config['commands']['external-cases']['argv'][3] = 'pass-nonzero'
                elif change == 'exclusion': case.config['inputs']['adapter-source']['excludes'] = ['unused']
                else: case.config['profiles']['isolated']['environment']['CASE_FLAG'] = 'changed-profile'
                case.save()
            changed_input(c, mutate)
    with Case() as c:
        b, _ = prefix(c)
        c.config['profiles']['unselected'] = copy.deepcopy(c.config['profiles']['isolated'])
        c.config['profiles']['unselected']['environment']['UNSELECTED'] = 'not-qualified'
        c.save()
        b2, _ = reported(c, 'RUNNING', replayed=True)
        assert b2['fingerprint'] == b['fingerprint'], 'unselected metadata widened qualification scope'


def environment():
    with Case() as c:
        _, j = reported(c)
        for step in ('first', 'second'):
            obs = c.observation(step)
            configured = c.config['profiles']['isolated']['environment']
            assert {k: v for k, v in obs['environment'].items() if k != 'LC_CTYPE'} == configured
            assert 'POISE_T030_AMBIENT_SECRET' not in obs['environment'] and obs['stdin'] == ''
            assert obs['uid'] == obs['euid'] == c.uid and obs['no_new_privs'] == 1
        assert 'private-test-token' not in compact(j)
    with Case() as c:
        def mutate(case):
            case.config['profiles']['isolated']['environment']['SECRET_VALUE'] = 'different-test-token'
            case.save()
        changed_input(c, mutate)
    with Case() as c:
        # A real loader constructor records the identity *before* Python starts.
        shared = c.source/'loader-probe.so'
        compiler = shutil.which('cc')
        if compiler is None:
            raise RuntimeError('test fixture requires a C compiler for actual pre-drop loader observation')
        p = subprocess.run([compiler, '-shared', '-fPIC', str(FIX/'loader-probe.c'), '-o', str(shared)],
                           capture_output=True, check=False, timeout=20)
        OBSERVATIONS.append({'kind': 'loader_fixture_compile', 'exit_code': p.returncode,
                             'stdout_hex': p.stdout.hex(), 'stderr_hex': p.stderr.hex()})
        if p.returncode:
            raise RuntimeError('loader fixture compilation failed: '+repr(p.stderr))
        log = c.workspace/'run-1/first/loader-identities.txt'
        c.config['profiles']['isolated']['environment'].update(LD_PRELOAD=str(shared), POISE_LOADER_TRACE=str(log))
        c.save(); reported(c)
        ids = [int(line) for line in log.read_text().splitlines()]
        assert ids and set(ids) == {c.uid}, ('case environment reached privileged loader', ids)
    with Case() as c:
        inject = c.source/'python-injection'; inject.mkdir()
        (inject/'sitecustomize.py').write_text(
            'import os\nfrom pathlib import Path\nPath("python-loader-identity.txt").write_text(str(os.geteuid()))\n')
        c.config['commands']['external-cases']['argv'].remove('-I')
        c.config['profiles']['isolated']['environment']['PYTHONPATH'] = str(inject)
        c.save(); reported(c)
        assert (c.workspace/'run-1/first/python-loader-identity.txt').read_text() == str(c.uid)
        assert not (c.root/'python-loader-identity.txt').exists(), 'privileged supervisor applied case Python environment'


def programme():
    for change in ('bytes', 'version', 'order'):
        with Case() as c:
            def mutate(case):
                if change == 'bytes':
                    with case.program_path.open('ab') as stream: stream.write(b' ')
                elif change == 'version':
                    case.program['version'] = 'same-subject-different-program'; case.save()
                else:
                    case.program['steps'].reverse(); case.save()
            changed_input(c, mutate)


def delivery():
    for change in ('data', 'executable', 'new-installed-file'):
        with Case() as c:
            def mutate(case):
                if change == 'data': (case.delivery/'data.txt').write_text('new installed data\n')
                elif change == 'executable': case.subject.write_bytes(case.subject.read_bytes()+b'\n# changed installed code\n')
                else: (case.delivery/'new.py').write_text('INSTALLED = True\n')
            changed_input(c, mutate)


def inventory_contract():
    with Case() as c:
        (c.source/'ignored').mkdir(); (c.source/'ignored/cache.bin').write_bytes(b'old cache')
        c.config['inputs']['adapter-source']['excludes'] = ['ignored']; c.save()
        b, _ = prefix(c)
        (c.source/'ignored/cache.bin').write_bytes(b'changed excluded cache')
        reported(c, operation='resume', revision=b['revision'])
    with Case() as c:
        (c.source/'other.py').write_bytes(c.module.read_bytes())
        alias = c.source/'alias.py'; alias.symlink_to('module.py')
        c.save()
        def mutate(case):
            alias.unlink(); alias.symlink_to('other.py')
        changed_input(c, mutate)
    with Case() as c:
        c.config['inputs']['adapter-source']['excludes'] = ['../outside']; c.save()
        rejected(c, 'program_invalid')
    with Case() as c:
        c.config['commands']['external-cases']['source_inputs'] = ['python', 'launcher']
        c.config['applications']['external-toy']['delivery_inputs'] = ['installed-subject', 'python']
        c.save()
        rejected(c, 'program_invalid')


def old_pass():
    with Case() as c:
        old, _ = reported(c)
        c.module.write_bytes(c.module.read_bytes()+b'\n# cannot reuse old PASS\n')
        rejected(c, 'run_conflict')
        rejected(c, 'input_mismatch', operation='resume', revision=old['revision'])
        # Inspect validates the retained historical record, not live changed source.
        p, b = c.call('inspect')
        assert p.returncode == 0 and p.stderr == b'' and b == {**old, 'replayed': False}, b
        new, _ = reported(c, run_id='run-2')
        assert new['fingerprint'] != old['fingerprint'] and new['run_id'] == 'run-2'


def inspect():
    with Case() as c:
        rejected(c, 'not_found', operation='inspect')
        assert not c.state.exists() and not c.workspace.exists()
    for verdict, mode in (('PASS', 'pass'), ('FAIL', 'fail'), ('BLOCKED', 'blocked')):
        with Case() as c:
            c.mode(mode); b, _ = reported(c, verdict)
            before = (inventory(c.state), inventory(c.workspace))
            inspected, _ = reported(c, verdict, operation='inspect')
            assert inspected == b and before == (inventory(c.state), inventory(c.workspace))
    with Case() as c:
        b, _ = prefix(c); inspected, _ = reported(c, 'RUNNING', operation='inspect')
        assert inspected == b


def damaged(c, path, action='append', revision=None):
    path = Path(path)
    if action == 'remove': path.unlink()
    elif action == 'replace': path.write_bytes(b'{"tampered":true}\n')
    elif action == 'link':
        foreign = c.root/'foreign-evidence'; foreign.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(foreign)
    else:
        with path.open('ab') as stream: stream.write(b'X')
    rejected(c, 'evidence_mismatch', operation='inspect')
    if revision is not None:
        rejected(c, 'evidence_mismatch', operation='resume', revision=revision)


def result_integrity():
    for action in ('append', 'remove', 'link'):
        with Case() as c:
            b, j = prefix(c)
            damaged(c, j['steps'][0]['outputs']['result']['path'], action, b['revision'])


def stream_integrity():
    for channel in ('stdout', 'stderr'):
        for action in ('append', 'remove'):
            with Case() as c:
                b, j = prefix(c)
                damaged(c, j['steps'][0]['outputs'][channel]['path'], action, b['revision'])


def context_integrity():
    with Case() as c:
        b, j = prefix(c); path, ctx = context_for(j)
        ctx['application_id'] = 'foreign-subject'; write(path, ctx)
        rejected(c, 'evidence_mismatch', operation='inspect')
        rejected(c, 'evidence_mismatch', operation='resume', revision=b['revision'])
    with Case() as c:
        b, j = prefix(c); path, _ = context_for(j)
        damaged(c, path, 'remove', b['revision'])

    for target in ('context', 'launch', 'control', 'frozen', 'journal', 'completed_workspace'):
        with Case() as c:
            b, j = prefix(c)
            context_path, _ = context_for(j)
            path = {
                'context': context_path, 'launch': context_path.with_name('launch.json'),
                'control': context_path.parent, 'frozen': c.state/'run-1/frozen/environment.json',
                'journal': c.state/'run-1/protocol.json',
                'completed_workspace': c.workspace/'run-1/first',
            }[target]
            path.chmod(0o755 if path.is_dir() else 0o644)
            if target == 'control': path.chmod(0o775)
            rejected(c, 'evidence_mismatch', operation='inspect')
            rejected(c, 'evidence_mismatch', operation='resume', revision=b['revision'])


def journal_integrity():
    for change in ('duplicate', 'order', 'attempt', 'partial-pass', 'exit', 'graph', 'revision', 'extra',
                   'fingerprint', 'snapshot', 'environment'):
        with Case() as c:
            b, j = reported(c) if change != 'partial-pass' else prefix(c)
            if change == 'duplicate': j['steps'][1] = copy.deepcopy(j['steps'][0])
            elif change == 'order': j['steps'].reverse()
            elif change == 'attempt': j['steps'][1]['attempt_id'] = j['steps'][0]['attempt_id']
            elif change == 'partial-pass': j.update(verdict='PASS', remaining_step_ids=[], stop_reason=None)
            elif change == 'exit': j['steps'][0]['exit_code'] = 1
            elif change == 'graph': j['graph_results'] = [{'graph_id': 'invented'}]
            elif change == 'revision': j['revision'] = True
            elif change == 'fingerprint':
                j['fingerprint'] = ('1' if j['fingerprint'][0] != '1' else '2')+j['fingerprint'][1:]
            elif change == 'snapshot':
                j['input_snapshot']['config_sha256'] = '0'*64
                j['fingerprint'] = hashlib.sha256(canonical(j['input_snapshot'])).hexdigest()
            elif change == 'environment':
                j['environment_observation']['uid'] += 1
                j['input_snapshot']['environment_sha256'] = hashlib.sha256(canonical(j['environment_observation'])).hexdigest()
                j['fingerprint'] = hashlib.sha256(canonical(j['input_snapshot'])).hexdigest()
            else: j['extra'] = 'not-allowed'
            write(c.state/'run-1/protocol.json', j)
            rejected(c, 'evidence_mismatch', operation='inspect')
    with Case() as c:
        _, j = reported(c); j['schema'] = 'unknown-journal/v2'; write(c.state/'run-1/protocol.json', j)
        rejected(c, 'store_incompatible', operation='inspect')

    with Case() as c:
        p, body = c.call(outer=c.fault('corrupt-prefix'))
        trace = [json.loads(line) for line in (c.root/'fault-trace.jsonl').read_text().splitlines()]
        assert len(trace) == 1 and trace[0]['action'] == 'corrupt-prefix', trace
        assert [x['state'] for x in trace[0]['journal']['steps']] == ['PASS', 'RUNNING']
        assert p.returncode == 2 and p.stderr == b'' and body['error']['code'] == 'evidence_mismatch', (p.returncode, body)
        persisted = c.raw_protocol()
        assert persisted['verdict'] == 'RUNNING', 'invalid prefix was durably certified PASS before rejection'
        assert [x['state'] for x in persisted['steps']] == ['PASS', 'RUNNING']
        assert persisted['stop_reason'] == 'unknown_attempt'
        rejected(c, 'evidence_mismatch', operation='inspect')


def inventory_integrity():
    with Case() as c:
        b, _ = prefix(c)
        candidates = []
        for path in sorted((c.state/'run-1').rglob('*')):
            if not path.is_file() or path.name == 'protocol.json':
                continue
            try:
                raw = path.read_text(encoding='utf-8'); value = json.loads(raw)
            except (UnicodeError, ValueError):
                continue
            if isinstance(value, (dict, list)) and ('module.py' in raw or 'subject.py' in raw):
                candidates.append(path)
        assert candidates, 'no retained full selected inventory/configuration found'
        damaged(c, candidates[0], 'replace', b['revision'])
    with Case() as c:
        b, _ = prefix(c)
        raw = c.program_path.read_bytes()
        copies = [p for p in (c.state/'run-1').rglob('*') if p.is_file() and p.read_bytes() == raw]
        assert copies, 'raw programme was not retained under the run'
        damaged(c, copies[0], 'append', b['revision'])


def isolation():
    with Case() as c:
        reported(c)
        for step in ('first', 'second'):
            obs = c.observation(step)
            assert obs['uid'] == obs['euid'] == c.uid and obs['gid'] == obs['egid'] == c.gid
            assert obs['groups'] == [] and obs['no_new_privs'] == 1
            assert all(int(v, 16) == 0 for v in obs['caps'].values())
            assert all(obs['denials'].values())
            assert not os.stat(c.state).st_mode & 0o077, 'own state is not private to the supervisor'
        assert not active_uid_processes(c.uid), 'owned execution left a live dedicated-uid process'
    with Case() as c:
        literal = '$(touch '+str(c.root/'MUST_NOT_EXIST')+'); echo injected'
        c.program['steps'][0]['arguments'].append(literal); c.save(); reported(c)
        assert c.observation()['arguments'] == [literal] and not (c.root/'MUST_NOT_EXIST').exists()


def zero_blocked(c, reason=None):
    p, b = c.call()
    assert p.returncode == 3 and p.stderr == b'' and b['status'] == 'reported' and b['verdict'] == 'BLOCKED', (p.returncode, b, p.stderr)
    assert set(b) == set(fixture('field-sets.json')['response'])
    assert b['completed_steps'] == 0 and b['total_steps'] == 2 and b['replayed'] is False
    j = c.raw_protocol()
    assert j['verdict'] == 'BLOCKED' and j['steps'] == [] and j['remaining_step_ids'] == ['first', 'second']
    assert isinstance(j['stop_reason'], str) and j['stop_reason']
    if reason is not None: assert j['stop_reason'] == reason, j
    assert not list(c.workspace.rglob('adapter-observation.json')) if c.workspace.exists() else True
    return b, j


def isolation_blocked():
    for change in ('missing-launcher', 'unsupported', 'readable-production', 'outside-test-root', 'live-uid'):
        with Case() as c:
            held = None
            try:
                iso = c.config['isolations']['dedicated']
                if change == 'missing-launcher': iso['launcher'] = str(c.inputs/'no-launcher')
                elif change == 'unsupported': iso['kind'] = 'unavailable-launcher/v1'
                elif change == 'readable-production': c.protected.chmod(0o755)
                elif change == 'outside-test-root': iso['test_roots'] = [str(c.root/'other-test-root')]
                else:
                    held = subprocess.Popen([str(c.launcher), '--reuid', str(c.uid), '--regid', str(c.gid),
                                             '--clear-groups', '--no-new-privs', str(c.python), '-I', '-c',
                                             'import time; time.sleep(20)'], stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
                    deadline = time.monotonic()+5
                    while time.monotonic() < deadline and held.pid not in active_uid_processes(c.uid):
                        if held.poll() is not None: raise RuntimeError('dedicated-uid busy fixture did not start')
                        time.sleep(0.02)
                    if held.pid not in active_uid_processes(c.uid): raise RuntimeError('dedicated-uid busy fixture unavailable')
                c.save(); zero_blocked(c, 'isolation_unavailable')
                if held is not None: assert held.poll() is None, 'runner killed a preexisting foreign uid process'
            finally:
                if held is not None:
                    held.terminate(); held.communicate(timeout=5)
    with Case() as c:
        c.config['profiles']['isolated']['os_ids'] = ['not-the-observed-os']; c.save()
        zero_blocked(c, 'environment_mismatch')

    with Case() as c:
        b, _ = prefix(c)
        before = inventory(c.workspace/'run-1/first')
        c.protected.chmod(0o755)
        blocked, journal = reported(c, 'BLOCKED', operation='resume', revision=b['revision'])
        assert journal['stop_reason'] == 'isolation_unavailable'
        assert [x['state'] for x in journal['steps']] == ['PASS']
        assert blocked['completed_steps'] == 1 and not (c.workspace/'run-1/second').exists()
        inspected, _ = reported(c, 'BLOCKED', operation='inspect')
        assert inspected == blocked and before == inventory(c.workspace/'run-1/first')


def result_matrix():
    for vector in fixture('result-matrix.json'):
        with Case() as c:
            c.mode(vector['mode']); _, j = reported(c, vector['verdict'])
            step = j['steps'][0]
            assert step['exit_code'] == vector['exit'] and step['state'] == vector['verdict']
            raw = json.loads(checked_record(c, step['outputs']['result']).decode('utf-8'))
            assert raw['status'] == vector['result']
            if vector['verdict'] == 'FAIL': assert j['stop_reason'] == vector['reason']
            if vector['verdict'] == 'BLOCKED': assert raw['reason'] == vector['reason'] and j['stop_reason']


def result_schema():
    modes = ('missing-result', 'malformed-result', 'duplicate-result', 'nonfinite-result',
             'invalid-utf8-result', 'oversized-result', 'wrong-id', 'extra-field',
             'bad-reason', 'wrong-status', 'wrong-schema', 'result-link')
    for mode in modes:
        with Case() as c:
            c.mode(mode); _, j = reported(c, 'FAIL')
            assert j['stop_reason'] in ('invalid_case_result', 'evidence_missing'), (mode, j['stop_reason'])
            assert len(j['steps']) == 1 and not (c.workspace/'run-1/second').exists()
            assert j['steps'][0]['effects'] == 'possible'


def fail_fast():
    with Case() as c:
        c.mode('fail-zero'); b, j = reported(c, 'FAIL')
        assert b['completed_steps'] == 1 and j['remaining_step_ids'] == ['second']
        assert not (c.workspace/'run-1/second').exists()
        rejected(c, 'run_not_resumable', operation='resume', revision=b['revision'])
        b2, _ = reported(c, 'FAIL', replayed=True)
        assert b2 == {**b, 'replayed': True}


def blocked_case():
    with Case() as c:
        c.mode('blocked'); b, j = reported(c, 'BLOCKED')
        assert b['completed_steps'] == 1 and j['steps'][0]['state'] == 'BLOCKED'
        assert not (c.workspace/'run-1/second').exists()
        result = rejected(c, 'run_not_resumable', operation='resume', revision=b['revision'])
        assert result['error']['details'] == {'run_id': 'run-1', 'verdict': 'BLOCKED'}


def process_limits():
    for mode, expected in (('stdout-overflow', 'output_limit'), ('stderr-overflow', 'output_limit'),
                           ('sleep', 'timeout'), ('signal', 'signal')):
        with Case() as c:
            if mode == 'sleep': c.program['steps'][0]['timeout_seconds'] = 1
            c.mode(mode); _, j = reported(c, 'FAIL')
            assert j['stop_reason'] == expected and j['steps'][0]['reason'] == expected
            assert j['steps'][0]['effects'] == 'possible'
            for channel in ('stdout', 'stderr'):
                record = j['steps'][0]['outputs'][channel]
                assert record['bytes'] <= c.config['limits'][channel+'_bytes']
            if expected == 'timeout': assert j['steps'][0]['timed_out'] is True
            if expected == 'signal': assert j['steps'][0]['signal'] == signal.SIGTERM
            assert not active_uid_processes(c.uid), 'timeout/signal left an owned live process'
    with Case() as c:
        c.config['limits']['total_timeout_seconds'] = 1; c.mode('sleep')
        _, j = reported(c, 'FAIL'); assert j['stop_reason'] == 'timeout'


def intent_crash():
    with Case() as c:
        payload = (compact(c.packet())+'\n').encode('utf-8')
        p = subprocess.run([str(CLI), '--config', str(c.config_path)], cwd=c.root,
                           env=c.environment(c.fault('crash')), input=payload,
                           capture_output=True, timeout=45, check=False)
        OBSERVATIONS.append({'kind': 'durable_intent_crash', 'exit_code': p.returncode,
                             'stdout_hex': p.stdout.hex(), 'stderr_hex': p.stderr.hex()})
        trace = [json.loads(line) for line in (c.root/'fault-trace.jsonl').read_text().splitlines()]
        assert p.returncode == 94 and len(trace) == 1 and trace[0]['event'] == 'post_durable_intent', (p.returncode, trace)
        j = c.raw_protocol(); assert len(j['steps']) == 1 and j['steps'][0]['state'] == 'RUNNING'
        step = j['steps'][0]
        assert step['exit_code'] is step['signal'] is step['duration_seconds'] is None
        assert all(x is None for x in step['outputs'].values())
        assert not (c.workspace/'run-1/first/adapter-observation.json').exists()
        _, ctx = context_for(j)
        # A plausible file does not convert an unobserved intent into completion.
        write(ctx['result_path'], fixture('result.json'))
        result = rejected(c, 'unknown_attempt', operation='resume', revision=j['revision'])
        assert result['error']['details'] == {'run_id': 'run-1', 'step_id': 'first'}
        assert not (c.workspace/'run-1/second').exists()
        assert not active_uid_processes(c.uid)


def lock_contention():
    with Case() as c:
        c.program['steps'][0]['timeout_seconds'] = 5; c.mode('sleep')
        payload = (compact(c.packet())+'\n').encode('utf-8')
        first = subprocess.Popen([str(CLI), '--config', str(c.config_path)], cwd=c.root,
                                 env=c.environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        first.stdin.write(payload); first.stdin.close(); first.stdin = None
        try:
            deadline = time.monotonic()+12
            marker = c.workspace/'run-1/first/started'
            while not marker.exists() and time.monotonic() < deadline:
                if first.poll() is not None: break
                time.sleep(0.02)
            assert marker.exists(), 'first invocation did not reach real external step'
            p, b = c.call()
            assert p.returncode == 2 and p.stderr == b'' and b['error']['code'] == 'store_busy' and b['error']['retryable'] is True, (p.returncode, b)
            assert b['error']['details'] == {'run_id': 'run-1'}
        finally:
            out, err = first.communicate(timeout=15)
            OBSERVATIONS.append({'kind': 'lock_owner', 'exit_code': first.returncode,
                                 'stdout_hex': out.hex(), 'stderr_hex': err.hex()})
        owner = json.loads(out.decode('utf-8'))
        assert first.returncode == 1 and err == b'' and owner['verdict'] == 'FAIL'
        assert not active_uid_processes(c.uid)


def workspace():
    with Case() as c:
        foreign = c.workspace/'run-1'; foreign.mkdir(parents=True)
        (foreign/'KEEP').write_bytes(b'foreign bytes')
        rejected(c, 'workspace_conflict')
        assert (foreign/'KEEP').read_bytes() == b'foreign bytes'
    with Case() as c:
        foreign = c.root/'foreign'; foreign.mkdir(); (foreign/'KEEP').write_bytes(b'foreign bytes')
        c.workspace.mkdir(); (c.workspace/'run-1').symlink_to(foreign, target_is_directory=True)
        rejected(c, 'workspace_conflict')
        assert list(foreign.iterdir()) == [foreign/'KEEP']
    with Case() as c:
        b, _ = prefix(c)
        prior = inventory(c.workspace/'run-1/first')
        reported(c, operation='resume', revision=b['revision'])
        assert prior == inventory(c.workspace/'run-1/first')

    for relative in ('second', '.control/second'):
        with Case() as c:
            b, _ = prefix(c)
            foreign = c.workspace/'run-1'/relative
            foreign.mkdir(); (foreign/'KEEP').write_bytes(b'foreign bytes must survive')
            rejected(c, 'workspace_conflict', operation='resume', revision=b['revision'])
            assert (foreign/'KEEP').read_bytes() == b'foreign bytes must survive'


def graph():
    with Case() as c:
        c.graph(); b, j = prefix(c)
        expected = {'graph_id': 'only-graph', 'graph_sha256': sha(c.graph_path),
                    'required_step_ids': ['first'], 'passed_step_ids': ['first'], 'verdict': 'RUNNING'}
        assert b['graph_results'] == [expected] and j['graph_results'] == [expected]
        captured = j['steps'][0]['graph']
        assert set(captured) == {'id', 'sha256', 'checks'}
        proof = checked_record(c, captured['checks'][0]['evidence_paths'][0])
        observed = json.loads(proof.decode('utf-8'))
        assert observed['graph_sha256'] == sha(c.graph_path) and observed['public_exit'] == 0
        assert observed['selected_graph'] == fixture('graph.json')
        b2, _ = reported(c, operation='resume', revision=b['revision'])
        assert b2['graph_results'] == [{**expected, 'verdict': 'PASS'}]
    with Case() as c:
        c.graph(); b, _ = prefix(c)
        with c.graph_path.open('ab') as stream: stream.write(b' ')
        rejected(c, 'input_mismatch', operation='resume', revision=b['revision'])
    with Case() as c:
        c.graph(); b, j = prefix(c)
        proof = j['steps'][0]['graph']['checks'][0]['evidence_paths'][0]['path']
        damaged(c, proof, 'append', b['revision'])


def graph_rejection():
    for mode, reasons in (
            ('graph-wrong', ('graph_mismatch',)), ('graph-fail', ('graph_checks_failed',)),
            ('graph-missing', ('evidence_missing',)), ('graph-escape', ('evidence_missing', 'graph_mismatch')),
            ('graph-duplicate', ('invalid_case_result', 'graph_mismatch')),
            ('graph-link', ('evidence_missing', 'graph_mismatch')), ('graph-null', ('graph_mismatch',))):
        with Case() as c:
            c.graph(); c.mode(mode); b, j = reported(c, 'FAIL')
            assert j['stop_reason'] in reasons, (mode, j['stop_reason'])
            assert b['graph_results'][0]['verdict'] == 'FAIL' and not (c.workspace/'run-1/second').exists()
    for change in ('duplicate', 'required', 'binding'):
        with Case() as c:
            c.graph()
            if change == 'duplicate': c.program['graphs'] *= 2
            elif change == 'required': c.program['graphs'][0]['required_step_ids'] = ['second']
            else: c.program['steps'][0]['graph_id'] = 'unselected'
            c.save(); rejected(c, 'program_invalid')


def drift():
    with Case() as c:
        _, j = reported(c, 'FAIL', outer=c.fault('drift'))
        trace = [json.loads(line) for line in (c.root/'fault-trace.jsonl').read_text().splitlines()]
        assert len(trace) == 1 and trace[0]['action'] == 'drift'
        assert j['stop_reason'] == 'input_drift' and len(j['steps']) == 1
        assert j['steps'][0]['state'] == 'PASS' and j['remaining_step_ids'] == ['second']
        assert not (c.workspace/'run-1/second').exists()
        assert b'actual source drift' in c.module.read_bytes()


def bounds():
    for operation, field, value in (('run', 'step_limit', True), ('run', 'step_limit', -1),
                                    ('run', 'step_limit', 1.5), ('resume', 'expected_revision', True),
                                    ('resume', 'expected_revision', 0), ('run', 'extra', 'unknown')):
        with Case() as c:
            packet = c.packet(operation, revision=1); packet['input'][field] = value
            rejected(c, 'invalid_packet', operation=operation, raw=(compact(packet)+'\n').encode())
    with Case() as c:
        packet = c.packet(); del packet['input']['step_limit']
        b = rejected(c, 'invalid_packet', raw=(compact(packet)+'\n').encode())
        assert b['error']['details'] == {'path': '/input/step_limit'}
    for raw in (b'{bad', b'{"schema":1,"schema":2}', b'{"value":NaN}', b'\xff', b'{} {}'):
        with Case() as c:
            rejected(c, 'invalid_json', raw=raw)
    with Case() as c:
        c.config['limits']['steps'] = 1; c.save(); rejected(c, 'limit_exceeded')
    with Case() as c:
        c.config['limits']['packet_bytes'] = 16; c.save(); rejected(c, 'limit_exceeded')
    with Case() as c:
        c.module.unlink(); zero_blocked(c)

    for graph_id in ([], {}, True, 3, "\ud800"):
        with Case() as c:
            c.program['steps'][0]['graph_id'] = graph_id
            c.program_path.write_text(json.dumps(c.program, ensure_ascii=True), encoding='utf-8')
            rejected(c, 'program_invalid')
    with Case() as c:
        packet = c.packet(); packet['input']['run_id'] = "\ud800"
        rejected(c, 'invalid_packet', raw=(json.dumps(packet, ensure_ascii=True)+'\n').encode())
    with Case() as c:
        c.config['limits']['lock_timeout_seconds'] = 10**400
        c.save(); rejected(c, 'program_invalid')


GROUPS = [('protocol.'+name, fn) for name, fn in (
    ('qualification', qualification), ('validation', validation), ('resume', resume),
    ('replay', replay), ('revision', revision), ('source', source),
    ('configuration', configuration), ('environment', environment), ('programme', programme),
    ('delivery', delivery), ('inventory', inventory_contract), ('old_pass', old_pass),
    ('inspect', inspect), ('result_integrity', result_integrity), ('stream_integrity', stream_integrity),
    ('context_integrity', context_integrity), ('journal_integrity', journal_integrity),
    ('inventory_integrity', inventory_integrity), ('isolation', isolation),
    ('isolation_blocked', isolation_blocked), ('result_matrix', result_matrix),
    ('result_schema', result_schema), ('fail_fast', fail_fast), ('blocked_case', blocked_case),
    ('process_limits', process_limits), ('intent_crash', intent_crash),
    ('lock_contention', lock_contention), ('workspace', workspace), ('graph', graph),
    ('graph_rejection', graph_rejection), ('drift', drift), ('bounds', bounds))]


def documentation():
    path = APP/'docs/uat-protocol.md'
    text = path.read_text(encoding='utf-8')
    checks = (
        ('commands', ['apps/stride-uat/bin/stride-uat', '--config', 'run', 'resume', 'inspect', 'step_limit', 'expected_revision']),
        ('schemas', ['stride-uat/v1', 'stride-uat-program/v1', 'stride-uat-protocol/v1', 'stride-uat-case-result/v1', 'reported']),
        ('provenance', ['source', 'delivery', 'excludes', 'input_snapshot', 'fingerprint', 'config_sha256', 'environment_sha256']),
        ('resume', ['unknown_attempt', 'input_mismatch', 'evidence_mismatch', 'revision_conflict', 'run_not_resumable', 'replayed']),
        ('isolation', ['linux-setpriv/v1', 'setpriv', 'uid', 'gid', 'lease_root', 'no-new-privs', 'BLOCKED', 'DAC']),
        ('limits', ['stdout_bytes', 'stderr_bytes', 'result_bytes', 'total_timeout_seconds', 'store_busy', 'output_limit', 'timeout']),
        ('graphs', ['graph_results', 'required_step_ids', 'graph_sha256', 'T058', 'graph_checks_failed']),
        ('delivery', ['contract-uat.md', 'uat-runner.md', 'test_protocol.py', '--suite behavior', 'execute', 'trusted-local']))
    failures = []
    for name, tokens in checks:
        missing = [token for token in tokens if token not in text]
        if missing: failures.append({'reason': 'missing literals: '+','.join(missing), 'test': 'docs.'+name})
    if not re.search('[А-Яа-яЁё]', text):
        failures.append({'reason': 'Russian human documentation required', 'test': 'docs.delivery'})
    requests = []
    for block in re.findall(r'```json\s*\n(.*?)\n```', text, re.DOTALL):
        value = json.loads(block)
        if isinstance(value, dict) and value.get('schema') == 'stride-uat/v1' and 'operation' in value:
            requests.append(value)
    expected_fields = {'run': {'run_id', 'program_path', 'profile_id', 'step_limit'},
                       'resume': {'run_id', 'program_path', 'profile_id', 'step_limit', 'expected_revision'},
                       'inspect': {'run_id'}}
    for op, fields in expected_fields.items():
        examples = [x for x in requests if x['operation'] == op]
        if not examples or any(set(x) != {'schema', 'operation', 'input'} or set(x['input']) != fields for x in examples):
            failures.append({'reason': 'missing or wrong exact '+op+' example', 'test': 'docs.commands'})
    env = os.environ.copy(); env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT/'src'))
    p = subprocess.run([sys.executable, '-B', str(ROOT/'tools/check_documentation.py'),
                        '--checkout', str(ROOT), '--changed', 'apps/stride-uat/docs/uat-protocol.md'],
                       cwd=ROOT, env=env, capture_output=True, timeout=30, check=False)
    OBSERVATIONS.append({'kind': 'existing_changed_file_documentation_owner', 'exit_code': p.returncode,
                         'stdout_hex': p.stdout.hex(), 'stderr_hex': p.stderr.hex()})
    result = json.loads(p.stdout.decode('utf-8'))
    if p.returncode or p.stderr or result.get('errors') != []:
        failures.append({'reason': 'existing changed-file link owner rejected documentation: '+compact(result), 'test': 'docs.delivery'})
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=('red', 'behavior', 'documentation'))
    args = parser.parse_args()
    errors, failures, count = [], [], 0
    if args.suite == 'documentation':
        count = 8
        try:
            failures = documentation()
        except Exception as exc:
            errors.append({'reason': type(exc).__name__+': '+str(exc), 'test': 'docs.file'})
    else:
        groups = GROUPS[:1] if args.suite == 'red' else GROUPS
        for name, fn in groups:
            count += 1
            try:
                fn()
            except AssertionError as exc:
                failures.append({'reason': str(exc) or 'assertion failed', 'test': name})
            except Exception as exc:
                errors.append({'reason': type(exc).__name__+': '+str(exc), 'test': name})
    output = os.environ.get('POISE_RUN_OUTPUT_DIR')
    if output:
        write(Path(output)/'uat-protocol-observations.json',
              {'schema': 'uat-protocol-test-observations/v1', 'suite': args.suite, 'observations': OBSERVATIONS})
    sys.stdout.buffer.write((compact({'errors': errors, 'failures': failures, 'tests_run': count})+'\n').encode('utf-8'))
    return 1 if errors or failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
