"""Actual Git preparation, outside application-state transactions."""
from pathlib import Path
import subprocess

from apps.sequencing.domain.workspace import WorkspaceError


class GitWorkspace:
    def __init__(self, config):
        self.config = config

    def _run(self, *arguments, check=True):
        try:
            result = subprocess.run(
                ['git', '-C', str(self.config['repository_path']), *arguments],
                capture_output=True, text=True, timeout=self.config['git_timeout_seconds'])
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError('workspace_git_timeout: retry the original request after stopping its writer') from exc
        if check and result.returncode:
            raise WorkspaceError('workspace_git_failed: ' + (result.stderr.strip() or result.stdout.strip()))
        return result

    def _registrations(self):
        records = []
        for block in self._run('worktree', 'list', '--porcelain', '-z').stdout.split('\0\0'):
            record = {}
            for field in block.split('\0'):
                if field:
                    key, _, value = field.partition(' ')
                    record[key] = value
            if record:
                records.append(record)
        return records

    def initial(self, task_id, read_only):
        reference = 'HEAD' if read_only else self.config['base_ref']
        base = self._run('rev-parse', '--verify', reference + '^{commit}').stdout.strip()
        path = (self.config['repository_path'] if read_only
                else self.config['worktree_root'] / task_id)
        branch = '' if read_only else self.config['branch_prefix'] + task_id
        if not read_only:
            if path.exists() or any(record.get('branch') == 'refs/heads/' + branch
                                    or record.get('worktree') == str(path)
                                    for record in self._registrations()):
                raise WorkspaceError('workspace_conflict')
            if self._run('show-ref', '--verify', '--quiet', 'refs/heads/' + branch,
                         check=False).returncode == 0:
                raise WorkspaceError('workspace_conflict')
        return {'repository_id': self.config['repository_id'], 'base_commit': base,
                'branch': branch, 'path': str(path)}

    def ensure(self, workspace):
        path = next(root['path'] for root in workspace['roots']
                    if root['kind'] == 'code' and root['position'] == 0)
        branch, base = workspace['branch'], workspace['base_commit']
        registrations = self._registrations()
        for record in registrations:
            same_path = record.get('worktree') == path
            same_branch = record.get('branch') == 'refs/heads/' + branch
            if same_path and same_branch:
                if record.get('locked') == 'initializing':
                    raise WorkspaceError(
                        f'workspace_checkout_incomplete: path={path}; branch={branch}; base={base}; '
                        'complete the checkout, then unlock it, then retry the original request')
                return
            if same_path or same_branch:
                raise WorkspaceError('workspace_conflict')
        if Path(path).exists():
            raise WorkspaceError('workspace_conflict')
        existing = self._run('show-ref', '--verify', '--hash', 'refs/heads/' + branch, check=False)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if existing.returncode == 0:
            if existing.stdout.strip() != base:
                raise WorkspaceError('workspace_conflict')
            self._run('worktree', 'add', path, branch)
        else:
            self._run('worktree', 'add', '-b', branch, path, base)
