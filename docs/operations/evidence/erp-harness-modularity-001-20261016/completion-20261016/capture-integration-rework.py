"""Freeze the four-path contract migration and actual registered proof."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess

source = Path('/home/sbwerewolf/workdata/erp/worktrees/ERP-HARNESS-MODULARITY-001')
destination = Path(__file__).parent / 'rework-0719cdf6'
commit = '0719cdf67fe789f54798f82775aae6597c316ade'
tree = '4f2f1a03175f6cdda5b31f826932a6a8358f1e50'
parent = 'b3b676d1ca9dae040060fc7fa09f1f8dde82792f'


def git(*args):
    return subprocess.check_output(['git', '-C', str(source), *args])


assert git('rev-parse', 'HEAD').decode().strip() == commit
assert git('rev-parse', 'HEAD^{tree}').decode().strip() == tree
assert git('status', '--porcelain') == b''
assert not destination.exists()
destination.mkdir()
paths = git('diff', '--name-only', parent, commit).decode().splitlines()
assert len(paths) == 4
metadata = {'commit': commit, 'tree': tree, 'parent': parent,
            'files': [{'path': name, 'sha256': hashlib.sha256((source / name).read_bytes()).hexdigest()}
                      for name in paths]}
(destination / 'frozen-source.json').write_text(json.dumps(metadata, indent=2) + '\n')
(destination / 'delta.patch').write_bytes(git('diff', parent, commit))
for check_id in (
    'chk_20261016T080015Z_a98137a86b',
    'chk_20261016T080525Z_74ca27882d',
    'chk_20261016T081218Z_3d040da8ea',
    'chk_20261016T081329Z_188a46dcdf',
    'chk_20261016T081441Z_636b4f92cb',
):
    folder = source / '.runtime/checks' / check_id
    record = json.loads((folder / 'result/record.json').read_bytes())
    assert record['status'] in ('passed', 'failed')
    shutil.copytree(folder, destination / check_id)
    log = (folder / 'result/command.log').read_text()
    for path in re.findall(r'full_log=(\S+)', log):
        actual = Path(path)
        assert actual.resolve().is_relative_to((source / '.runtime/verify/runs').resolve())
        target = destination / 'suite-logs' / actual.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(actual, target)
files = [{'path': str(path.relative_to(destination)),
          'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
         for path in sorted(destination.rglob('*')) if path.is_file()]
(destination / 'manifest.json').write_text(json.dumps({'files': files}, indent=2) + '\n')
for item in files:
    assert hashlib.sha256((destination / item['path']).read_bytes()).hexdigest() == item['sha256']
print(json.dumps({'status': 'captured_and_verified', 'files': len(files), 'tree': tree}))
