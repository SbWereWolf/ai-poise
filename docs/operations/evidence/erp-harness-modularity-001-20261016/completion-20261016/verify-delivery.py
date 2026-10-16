"""Verify the actual publication, non-overlapping WIP and protected config backups."""
from pathlib import Path
import hashlib
import json
import stat
import subprocess

root = Path('/home/sbwerewolf/workdata/erp')
archive = Path(__file__).parent
data = json.loads((archive / 'integrated-response-10309a04.json').read_bytes())
source = '0719cdf67fe789f54798f82775aae6597c316ade'
target = '16c5b906615e57a5b5f89b495c42b79f51eab87e'
tree = '4f2f1a03175f6cdda5b31f826932a6a8358f1e50'


def git(*arguments):
    return subprocess.check_output(['git', '-C', str(root), *arguments]).decode()


assert data['status'] == data['phase'] == 'integrated'
assert data['integration_head'] == data['target_after'] == source
assert git('rev-parse', 'HEAD').strip() == source
assert git('rev-parse', 'HEAD^{tree}').strip() == tree
assert data['publication']['receipt']['argv'] == [
    'git', '-C', str(root), 'merge', '--ff-only', 'poise/ERP-HARNESS-MODULARITY-001']
assert data['publication']['receipt']['actual_exit_code'] == 0
current = [item for item in data['checks'] if item['integration_head'] == source]
assert len(current) == 10
assert all(item['passed'] and item['actual_exit_code'] == 0 and item['capture_complete']
           and not item['cancelled'] and not item['timed_out']
           and item['verified_tree'] == tree
           and all(item['termination'][key] for key in
                   ('child_reaped', 'process_group_stopped', 'capture_complete'))
           for item in current)
changed = git('diff', '--name-only', target, source).splitlines()
dirty = git('diff', '--name-only').splitlines()
staged = git('diff', '--cached', '--name-only').splitlines()
untracked = git('ls-files', '--others', '--exclude-standard').splitlines()
assert len(changed) == 40
assert not set(changed).intersection(dirty + staged + untracked)
assert not (root / 'worktrees/ERP-HARNESS-MODULARITY-001').exists()
assert git('branch', '--list', 'poise/ERP-HARNESS-MODULARITY-001') == ''
assert data['cleanup'] == {'task_branch': 'deleted', 'task_worktree': 'removed',
                           'temporary_backups': 'removed'}
backups = []
for event in data['history']:
    if event.get('event') == 'configuration_backup_completed':
        for item in event['details']['files']:
            assert item['status'] == 'copied'
            path = Path(item['recovery_path'])
            assert path.is_file() and not path.is_symlink()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            backups.append({**item, 'verified_mode': '0600', 'hash_verified': True})
assert len(backups) == 2
report = {'status': 'verified', 'source_commit': source, 'source_tree': tree,
          'target_before': target, 'current_native_checks': 10,
          'historical_failed_checks_retained': sum(not item['passed'] for item in data['checks']),
          'publication_argv': data['publication']['receipt']['argv'],
          'published_paths': changed, 'foreign_tracked_dirty_paths': dirty,
          'foreign_staged_paths': staged, 'foreign_untracked_paths': untracked,
          'scope_intersection': [], 'cleanup': data['cleanup'], 'configuration_backups': backups,
          'preservation_proof_kind': 'confirmed_ff_only_and_disjoint_paths_with_surviving_wip',
          'whole_workspace_before_after_byte_identity_claimed': False,
          'reason': 'No before/after byte snapshot exists for successful publication; concurrent foreign writers were not frozen.'}
(archive / 'delivery-verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'status': 'verified', 'current_native_checks': 10,
                  'published_paths': len(changed), 'configuration_backups': len(backups),
                  'foreign_tracked_paths_preserved': len(dirty), 'scope_intersection': []}))
