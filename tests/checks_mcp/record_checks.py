"""One literal selected group, one unchanged existing pytest recorder."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.skills.record_instruction_red import run


def main():
    groups = json.loads((Path(__file__).parent / 'fixtures/groups.json').read_text())
    parser = argparse.ArgumentParser()
    parser.add_argument('--group', required=True, choices=groups)
    args = parser.parse_args()
    actual, report = run(groups[args.group], Path(os.environ['POISE_RUN_OUTPUT_DIR']), args.group)
    # Inventory the real external-client witnesses without interpreting or
    # changing pytest's outcome. Large build copies/venvs are not archives.
    root = Path(os.environ['POISE_RUN_OUTPUT_DIR'])
    files = []
    for folder in (root / 'pytest-temp').rglob('mcp-observation'):
        for path in sorted(folder.rglob('*')):
            if any(part in ('venv', 'build-source', 'cache') for part in path.relative_to(folder).parts):
                continue
            if path.is_file():
                body = path.read_bytes()
                files.append({'path': str(path.relative_to(root)), 'size': len(body),
                              'sha256': hashlib.sha256(body).hexdigest()})
    (root / (args.group + '.subject-files.json')).write_text(
        json.dumps({'native_receipt': False, 'files': files}, ensure_ascii=False, indent=2) + '\n')
    sys.stdout.write(report)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
