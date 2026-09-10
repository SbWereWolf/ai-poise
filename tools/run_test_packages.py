"""Run test modules in isolated pytest processes and retain terminal evidence.

The outer deadline belongs to one package, not to individual application
checks. Test/configuration limits are not rewritten, and failures are not
retried. Failed or interrupted workspaces remain under the output directory
so paths in a traceback still lead to the original diagnostic artifacts.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

from harness.common import HarnessError
from harness.execution import run_command


def fingerprint(root: Path) -> dict[str, str]:
    paths = []
    for directory in ('src', 'tests', 'examples', 'config', 'tools'):
        paths.extend(p for p in (root / directory).rglob('*')
                     if p.suffix in ('.py', '.json', '.toml'))
    paths.append(root / 'pyproject.toml')
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def parse_junit(path: Path, module: str) -> tuple[list[dict], str | None]:
    """Read terminal pytest evidence; a zero process exit alone is insufficient."""
    if not path.is_file():
        return [], 'Missing terminal JUnit report'
    try:
        document = ET.parse(path)
    except (ET.ParseError, OSError) as exc:
        return [], f'Invalid terminal JUnit report: {exc}'
    rows = []
    for case in document.getroot().iter('testcase'):
        if 'name' not in case.attrib:
            return rows, 'Invalid testcase: missing name'
        filename = case.attrib.get('file', module)
        module_name = str(Path(filename).with_suffix('')).replace('/', '.')
        classname = case.attrib.get('classname', '')
        class_path = (classname[len(module_name) + 1:].replace('.', '::') + '::'
                      if classname.startswith(module_name + '.') else '')
        outcome = 'passed'
        for tag in ('error', 'failure', 'skipped'):
            if case.find(tag) is not None:
                outcome = tag
                break
        rows.append({'nodeid': filename + '::' + class_path + case.attrib['name'],
                     'outcome': outcome})
    if not rows:
        return [], 'Empty terminal JUnit report: no testcase records'
    if len({r['nodeid'] for r in rows}) != len(rows):
        return rows, 'Invalid terminal JUnit report: duplicate testcase identities'
    return rows, None


def run_package(*, root: Path, output: Path, module: Path, index: int,
                timeout: float, success_workspaces: str) -> dict:
    """Execute one declared module; share the existing bounded command executor."""
    if success_workspaces not in ('keep', 'delete'):
        raise ValueError('success_workspaces must be explicitly keep or delete')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    if module.is_absolute() or '..' in module.parts or not (root / module).is_file():
        raise ValueError('module must name an existing file relative to root')
    name = f'{index:02d}-{module.stem}'
    workspace = output / 'workspaces' / name
    workspace.mkdir(parents=True, exist_ok=False)
    xml = output / (name + '.xml')
    log = output / (name + '.log')
    stderr = output / (name + '.stderr.log')
    result_path = output / (name + '.json')
    # A repeated call must not truncate another invocation's evidence.
    if any(p.exists() for p in (xml, log, stderr, result_path)):
        workspace.rmdir()
        raise FileExistsError(f'Package output already exists: {name}')
    command = [sys.executable, '-m', 'pytest', str(module), '-q', '--tb=short',
               '--basetemp', str(workspace / 'pytest'), '--junitxml', str(xml),
               '-o', 'junit_family=legacy']
    began = datetime.now(timezone.utc).isoformat()
    execution_error = None
    try:
        execution = run_command(command, root,
                                {**os.environ, 'PYTHONPATH': str(root / 'src')},
                                timeout, log, stderr)
    except HarnessError as exc:
        execution_error = str(exc)
        execution = {'actual_exit_code': None, 'timed_out': False,
                     'duration_seconds': None, 'stdout': str(log), 'stderr': str(stderr)}
    tests, report_error = parse_junit(xml, str(module))
    success = (execution_error is None and not execution['timed_out']
               and execution['actual_exit_code'] == 0 and report_error is None
               and all(t['outcome'] in ('passed', 'skipped') for t in tests))
    retained = not success or success_workspaces == 'keep'
    if not retained:
        shutil.rmtree(workspace)
    result = {'module': str(module), 'command': command, 'started_at': began,
              'ended_at': datetime.now(timezone.utc).isoformat(),
              'exit_code': execution['actual_exit_code'], 'timed_out': execution['timed_out'],
              'duration_seconds': execution['duration_seconds'],
              'stdout': str(log), 'stderr': str(stderr), 'junit_path': str(xml),
              'report_error': report_error, 'execution_error': execution_error,
              'workspace': str(workspace), 'workspace_retained': retained,
              'result_path': str(result_path), 'tests': tests, 'success': success}
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--workers', required=True, type=int)
    parser.add_argument('--timeout', required=True, type=float)
    parser.add_argument('--success-workspaces', required=True, choices=('keep', 'delete'),
                        help='Failed/incomplete workspaces are always retained')
    args = parser.parse_args()
    if args.workers <= 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('workers and timeout must be finite positive values')
    root = args.root.resolve()
    out = args.output.resolve()
    cases = sorted(root.glob('tests/**/test_*.py'))
    if not cases:
        parser.error('No test modules selected')
    if not (root / 'pyproject.toml').is_file():
        parser.error('root must contain pyproject.toml')
    if out == root or out in root.parents or out.is_relative_to(root):
        parser.error('output must be outside the checked source tree')
    initial = fingerprint(root)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'source-hashes-before.json').write_text(json.dumps(initial, indent=2))

    def run(item):
        index, path = item
        result = run_package(root=root, output=out, module=path.relative_to(root),
                             index=index, timeout=args.timeout,
                             success_workspaces=args.success_workspaces)
        print(f'{index:02d}-{path.stem}', result['exit_code'], len(result['tests']),
              'PASS' if result['success'] else 'NOT_PASS', flush=True)
        return result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, enumerate(cases)))
    final = fingerprint(root)
    identities = [t['nodeid'] for r in results for t in r['tests']]
    unique = len(identities) == len(set(identities))
    report = {'started_sources': len(initial), 'unchanged_sources': initial == final,
              'packages': results, 'tests': len(identities), 'unique_test_ids': unique,
              'passed': sum(t['outcome'] == 'passed' for r in results for t in r['tests']),
              'skipped': sum(t['outcome'] == 'skipped' for r in results for t in r['tests']),
              'automatic_retries': False, 'workers': args.workers,
              'package_timeout_seconds': args.timeout,
              'success': initial == final and unique and all(r['success'] for r in results)}
    (out / 'source-hashes-after.json').write_text(json.dumps(final, indent=2))
    (out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
