"""T017 delivery-boundary tests; no production app CLI is impersonated.

Only temporary fixture executables are installed. The shipped launchers are copied
byte-for-byte and executed by the OS, with their original executable mode.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
# Test-owned expectations, deliberately not imported from production wrappers.
ROUTES = {
    'bootstrap': 'bootstrap',
    'poise': 'poise',
    'stride': 'orchestrator',
    'stride-uat': 'stride-uat',
}
APPS = ['bootstrap', 'execution', 'orchestrator', 'poise',
        'requirements', 'sequencing', 'stride-uat']
CASES = ['argv_bytes_exit', 'caller_cwd_environment', 'empty_success',
         'missing_target', 'nonexecutable_target', 'process_replacement']
PROVIDER = '''import json,os,sys
payload=sys.stdin.buffer.read()
header={"argv":sys.argv[1:],"cwd":os.getcwd(),"environment":os.environ.get("LAUNCHER_TEST"),"cdpath":os.environ.get("CDPATH"),"pid":os.getpid()}
sys.stdout.buffer.write(json.dumps(header,ensure_ascii=False).encode("utf-8")+b"\\n"+payload)
sys.stdout.buffer.flush()
sys.stderr.buffer.write(b"provider-stderr:\\xff\\x00\\n")
sys.stderr.buffer.flush()
raise SystemExit(int(os.environ.get("PROVIDER_EXIT","0")))
'''


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_boundary(alias: str, scenario: str) -> None:
    source = ROOT / alias
    require(source.is_file(), f'missing launcher: {alias}')
    require(not source.is_symlink(), f'launcher is not a regular file: {alias}')
    require(bool(source.stat().st_mode & 0o111), f'launcher not executable: {alias}')
    with tempfile.TemporaryDirectory(prefix='t017-boundary-') as directory:
        base = Path(directory)
        delivery = base / 'unpacked delivery Юникод'
        cwd = base / 'caller cwd with spaces'
        delivery.mkdir()
        cwd.mkdir()
        launch = delivery / alias
        shutil.copy2(source, launch)
        target = delivery / 'apps' / ROUTES[alias] / 'bin' / alias
        target.parent.mkdir(parents=True)
        if scenario != 'missing_target':
            target.write_text('#!' + sys.executable + '\n' + PROVIDER, encoding='utf-8')
            target.chmod(0o644 if scenario == 'nonexecutable_target' else 0o755)
        argv = ['', 'two words', 'Юникод', '*', '--', '-n', 'literal;$(no-run)', "'quote'"]
        payload = bytes(range(256)) * 4
        env = os.environ.copy()
        env.update({'LAUNCHER_TEST': 'unchanged ; $value', 'CDPATH': str(base),
                    'PWD': str(cwd), 'PROVIDER_EXIT': '7' if scenario == 'argv_bytes_exit' else '0'})
        if scenario == 'empty_success':
            argv, payload = [], b''
        process = subprocess.Popen([str(launch), *argv], cwd=cwd, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            stdout, stderr = process.communicate(payload, timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise AssertionError('launcher/provider exceeded five-second bound')
        if scenario in ('missing_target', 'nonexecutable_target'):
            code = 127 if scenario == 'missing_target' else 126
            require(process.returncode == code, f'{scenario}: expected exit{code}, got {process.returncode}')
            require(stdout == b'', f'{scenario}: must not fabricate stdout')
            require(bool(stderr), f'{scenario}: missing shell diagnostic')
            return
        require(process.returncode == int(env['PROVIDER_EXIT']), 'provider exit status not preserved')
        header, separator, echoed = stdout.partition(b'\n')
        require(bool(separator), 'provider header missing or launcher stdout contamination')
        record = json.loads(header.decode('utf-8'))
        require(record['argv'] == argv, 'argv was split, expanded, dropped or reordered')
        require(echoed == payload, 'binary stdin/stdout bytes changed')
        require(stderr == b'provider-stderr:\xff\x00\n', 'binary stderr changed')
        require(record['cwd'] == str(cwd), 'caller cwd changed')
        require(record['environment'] == env['LAUNCHER_TEST'], 'environment changed')
        require(record['cdpath'] == env['CDPATH'], 'CDPATH was changed outside root-resolution subshell')
        if scenario == 'process_replacement':
            require(record['pid'] == process.pid, 'launcher did not exec the app-owned process')
        # A launcher must neither create a dispatcher nor migrate state.
        require(sorted(p.name for p in delivery.iterdir()) == sorted([alias, 'apps']),
                'launcher created an unexpected root file or directory')


def seven_app_bins() -> None:
    require(sorted(p.name for p in (ROOT / 'apps').iterdir() if p.is_dir()) == APPS,
            'apps directory must contain exactly the seven agreed application owners')
    missing = [name for name in APPS if not (ROOT / 'apps' / name / 'bin').is_dir()]
    require(not missing, 'missing app bin: ' + ','.join(missing))
    require(not (ROOT / 'bin').exists(), 'root bin is forbidden')
    require(not (ROOT / 'stack').exists(), 'mandatory stack dispatcher is forbidden')


def documented_contract() -> None:
    path = ROOT / 'apps' / 'README.md'
    require(path.is_file(), 'missing co-delivered apps/README.md')
    text = path.read_text(encoding='utf-8')
    for alias, owner in ROUTES.items():
        require(f'`{alias}` | `apps/{owner}/bin/{alias}`' in text,
                f'missing exact documented route: {alias}')
    for owner in APPS:
        require(f'`{owner}`' in text, f'missing documented owner: {owner}')
    for token in ['argv', 'stdin', 'stdout', 'stderr', '127', '126', 'cwd',
                  'T018', 'T024', 'fixture', 'not implemented', 'bin/', 'stack']:
        require(token in text, f'missing documented contract term: {token}')


class Case(unittest.TestCase):
    def __init__(self, name, function):
        super().__init__('runTest')
        self.name, self.function = name, function

    def id(self):
        return self.name

    def runTest(self):
        self.function()


class Result(unittest.TestResult):
    """Retain exact exception messages, not timing/path-dependent trace rendering."""
    def __init__(self):
        super().__init__()
        self.failure_records = []
        self.error_records = []

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.failure_records.append({'test': test.id(), 'reason': str(err[1])})

    def addError(self, test, err):
        super().addError(test, err)
        self.error_records.append({'test': test.id(), 'type': err[0].__name__, 'reason': str(err[1])})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=['behavior', 'documentation'])
    args = parser.parse_args()
    if args.suite == 'behavior':
        cases = [Case(f'launchers.{scenario}.{alias}',
                      lambda a=alias, s=scenario: run_boundary(a, s))
                 for alias in ROUTES for scenario in CASES]
        cases.append(Case('layout.seven_app_bins', seven_app_bins))
    else:
        cases = [Case('documentation.launcher_contract', documented_contract)]
    result = Result()
    unittest.TestSuite(cases).run(result)
    print(json.dumps({'errors': sorted(result.error_records, key=lambda x: x['test']),
                      'failures': sorted(result.failure_records, key=lambda x: x['test']),
                      'tests_run': result.testsRun}, sort_keys=True, separators=(',', ':')))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
