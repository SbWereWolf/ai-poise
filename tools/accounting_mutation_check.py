"""Check sensitivity of accounting regression tests in disposable source copies."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

VARIANTS = (
    ('double_count_details', 'src/poise/modules/accounting/domain.py',
     "return d['counters']",
     "return {**d['counters'], 'total_tokens': d['counters']['total_tokens'] + d['counters']['cached_input_tokens'] + d['counters']['reasoning_tokens']}",
     'tests/accounting/test_domain.py::test_details_are_subsets_not_extra_spend'),
    ('tests_as_useful_code', 'src/poise/infrastructure/accounting_measurement.py',
     'useful=category in definition.git_categories', 'useful=True',
     'tests/accounting/test_paths.py::test_final_diff_credited_once_and_tests_excluded'),
    ('charge_user_wait', 'src/poise/infrastructure/sqlite/accounting.py',
     "elif d['turn_id']!=turn_id:", 'elif False:',
     'tests/accounting/test_extended.py::test_new_user_turn_does_not_charge_wait_after_failed_verify'),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--timeout', required=True, type=float)
    args = parser.parse_args()
    root, out = args.root.resolve(), args.output.resolve()
    if args.timeout <= 0 or not (root/'src/poise').is_dir():
        parser.error('Use a positive timeout and the Poise source root')
    out.mkdir(parents=True, exist_ok=False)
    results = []
    for name, filename, old, replacement, nodeid in VARIANTS:
        workspace = out/name
        workspace.mkdir()
        for folder in ('src', 'tests', 'config', 'examples'):
            shutil.copytree(root/folder, workspace/folder,
                            ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
        shutil.copy2(root/'pyproject.toml', workspace/'pyproject.toml')
        target = workspace/filename
        original = target.read_text()
        if original.count(old) != 1:
            raise RuntimeError(f'Mutation target no longer matches exactly: {filename}')
        target.write_text(original.replace(old, replacement))
        cmd = [sys.executable, '-m', 'pytest', nodeid, '-q', '--tb=short']
        log = out/(name+'.log')
        with log.open('w') as stream:
            try:
                run = subprocess.run(cmd, cwd=workspace,
                    env={**os.environ, 'PYTHONPATH': str(workspace/'src'),
                         'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
                    stdout=stream, stderr=subprocess.STDOUT, timeout=args.timeout)
                exit_code = run.returncode
            except subprocess.TimeoutExpired:
                exit_code = None
        text = log.read_text()
        detected = exit_code == 1 and '1 failed' in text and 'ERROR collecting' not in text
        results.append({'mutation': name, 'target': filename, 'test': nodeid,
                        'exit_code': exit_code, 'detected': detected})
        shutil.rmtree(workspace)
    success = all(item['detected'] for item in results)
    (out/'result.json').write_text(json.dumps({
        'at': datetime.now(timezone.utc).isoformat(), 'all_detected': success,
        'results': results}, ensure_ascii=False, indent=2)+'\n')
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
