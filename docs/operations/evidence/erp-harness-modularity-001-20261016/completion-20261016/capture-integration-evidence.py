"""Preserve authentic, terminal integration evidence before worktree cleanup."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess

SOURCE = Path('/home/sbwerewolf/workdata/erp/worktrees/ERP-HARNESS-MODULARITY-001')
DESTINATION = Path(__file__).parent / 'integration-checks'
ACCEPTED = 'a8ae8509d8c1204805e892bf6b2f24bb37e7b91a'
TARGET = '16c5b906615e57a5b5f89b495c42b79f51eab87e'
TREE = 'c2fcc025aa20b2be70432fd2ff3d45ea855c2329'
CHECKS = (
    'chk_20261016T063559Z_d5747946ec',
    'chk_20261016T064209Z_51021e5046',
    'chk_20261016T064328Z_91fd4a23c6',
    'chk_20261016T064437Z_2c724b2dc5',
    'chk_20261016T064532Z_f0a69d7eae',
    'chk_20261016T064656Z_c24ee4f05a',
)
PATHS = (
    'ai-assistant/checks/check_runner_lib.py',
    'ai-assistant/checks/command-profiles.toml',
    'ai-assistant/checks/registered_command_adoption_lib.py',
    'ai-assistant/checks/run_check.py',
    'ai-assistant/checks/tooling-verification.json',
    'ai-assistant/checks/command_profile_lib.py',
    'ai-assistant/mcp/repo-checks-server.py',
    'ai-assistant/tests/checks/artifact-evidence-inventory.py',
    'ai-assistant/tests/checks/check-runner.py',
    'ai-assistant/tests/checks/tooling-verification-config.py',
    'ai-assistant/tests/checks/evidence-manifest.py',
    'ai-assistant/tests/checks/registered-check-lock.py',
)


def git(*arguments):
    return subprocess.check_output(['git', '-C', str(SOURCE), *arguments])


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


assert git('write-tree').decode().strip() == TREE
assert git('diff', '--name-only') == b''
assert not DESTINATION.exists()
DESTINATION.mkdir()
for check_id in CHECKS:
    source = SOURCE / '.runtime/checks' / check_id
    record = json.loads((source / 'result/record.json').read_bytes())
    assert record['status'] in ('passed', 'failed', 'timed_out')
    shutil.copytree(source, DESTINATION / 'checks' / check_id)
for pid in ('460548', '476025', '482484', '486384', '489823', '494876'):
    for source in sorted((SOURCE / '.runtime/verify/runs').glob(f'*.{pid}.log')):
        destination = DESTINATION / 'suite-logs' / source.name
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, destination)
for label, parent in (('accepted', ACCEPTED), ('target', TARGET)):
    (DESTINATION / f'resolution-against-{label}.patch').write_bytes(
        git('diff', '--cached', parent, '--', *PATHS))
frozen = {'accepted_commit': ACCEPTED, 'target_commit': TARGET, 'index_tree': TREE,
          'files': [{'path': name, 'sha256': sha256(SOURCE / name)} for name in PATHS]}
(DESTINATION / 'frozen-source.json').write_text(json.dumps(frozen, indent=2) + '\n')
files = [{'path': str(path.relative_to(DESTINATION)), 'sha256': sha256(path),
          'bytes': path.stat().st_size}
         for path in sorted(DESTINATION.rglob('*')) if path.is_file()]
(DESTINATION / 'manifest.json').write_text(json.dumps({'files': files}, indent=2) + '\n')
for item in files:
    assert sha256(DESTINATION / item['path']) == item['sha256']
print(json.dumps({'status': 'captured_and_verified', 'files': len(files), 'tree': TREE}))
