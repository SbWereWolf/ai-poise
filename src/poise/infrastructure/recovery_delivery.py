"""Delivery mechanics without native sessions or Task execution composition."""
import json
import os
from pathlib import Path
import shutil

from ..common import PoiseError, descendant, digest, file_digest
from .git_snapshot import snapshot_tree
from .git_transport import run_git_receipt
from .goal_config import atomic_write
from .recovery_flow import WorkspaceRecoveryFlow
from .sqlite.transfers import SqliteTransferRepository, snapshot_fingerprint


class RecoveryDelivery:
    def __init__(self, descriptor):
        self.d = descriptor
        self.stage = Path(descriptor['staging'])
        self.manifest = descriptor['manifest']
        self.binding = descriptor['binding']
        self.policy = descriptor['policy']
        self.directory = Path(descriptor['directory'])

    def git(self, cwd, *arguments, env=None):
        result = run_git_receipt(cwd, list(arguments), self.d['git_seconds'], env)
        if result['actual_exit_code'] != 0:
            raise PoiseError(result['stderr'] or result['stdout'])
        return result['stdout'].rstrip('\n')

    def tree(self, path):
        return snapshot_tree(self.git, path, self.directory / 'git-observations', 'delivery-')

    def snapshot(self):
        path = descendant(self.stage, self.policy['database'])
        tables = SqliteTransferRepository.read_snapshot(path)
        if snapshot_fingerprint(tables) != self.manifest['state_fingerprint']:
            raise PoiseError('Snapshot record fingerprint mismatch')
        if ([r['id'] for r in tables['tasks']] != self.manifest['task_ids']
                or [r['id'] for r in tables['sprints']] != self.manifest['sprint_ids']):
            raise PoiseError('Transfer owner inventory differs from snapshot')
        return tables

    def inventory(self):
        for item in self.manifest['files']:
            path = descendant(self.stage, item['path'])
            if (path.is_symlink() or not path.is_file() or path.stat().st_size != item['size']
                    or file_digest(path) != item['digest']):
                raise PoiseError('Missing or changed delivery file: ' + item['path'])

    def place(self):
        self.inventory(); self.snapshot()
        for item in self.manifest['files']:
            if 'placement' not in item:
                continue
            target = Path(self.binding['locations'][item['placement']])
            source = descendant(self.stage, item['path'])
            if target.exists():
                if target.is_symlink() or not target.is_file() or file_digest(target) != item['digest']:
                    raise PoiseError('Prepared destination file changed; no overwrite')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(target, source.read_bytes(), self.policy['file_mode'])
        repository = Path(self.d['repository'])
        source = Path(self.policy['recovery']['repositories']['task'])
        for tid, spec in self.manifest['workspaces'].items():
            if spec is None:
                continue
            # A declared source is required even when this installation happens
            # to have an object already; no implicit source discovery.
            if not source.is_dir():
                return {'status': 'failed', 'reason': 'source_unavailable'}
            tree = Path(self.binding['worktrees'][tid])
            if not tree.exists():
                try:
                    self.git(source, 'cat-file', '-e', spec['commit'] + '^{commit}')
                    self.git(repository, 'fetch', '--no-tags', str(source), spec['commit'])
                    self.git(repository, 'check-ref-format', '--branch', spec['branch'])
                    tree.parent.mkdir(parents=True, exist_ok=True)
                    self.git(repository, 'worktree', 'add', '-b', spec['branch'], str(tree), spec['commit'])
                except PoiseError as exc:
                    return {'status': 'failed', 'reason': 'source_unavailable', 'error': str(exc)}
                patch = descendant(self.stage, spec['diff'])
                if patch.stat().st_size:
                    try:
                        self.git(tree, 'apply', '--check', '--binary', str(patch))
                        self.git(tree, 'apply', '--binary', str(patch))
                    except PoiseError as exc:
                        return {'status': 'failed', 'reason': 'diff_inapplicable', 'error': str(exc)}
            self.validate_tree(tid, spec)
        for root in self.d['owner_roots']:
            Path(root).mkdir(parents=True, exist_ok=True)
        return {'status': 'complete'}

    def validate_tree(self, tid, spec):
        tree = Path(self.binding['worktrees'][tid])
        if (self.git(tree, 'rev-parse', 'HEAD') != spec['commit']
                or self.git(tree, 'symbolic-ref', '--short', 'HEAD') != spec['branch']
                or self.tree(tree) != spec['tree']):
            raise PoiseError('Prepared imported worktree changed; do not reset it')
        self.git(tree, 'cat-file', '-e', spec['base'] + '^{commit}')

    def components(self):
        steps = self.policy['recovery']['component_steps']
        if not steps:
            return {'status': 'complete', 'steps': []}
        flow = {'schema': 'workspace-recover/flow/v3', 'workspace': str(self.directory), 'steps': steps}
        return WorkspaceRecoveryFlow(self.policy['recovery']['tool_argv']).run(
            flow, self.d['variables'], self.directory / 'components-flow.json',
            self.directory / 'components-session')

    def validate(self):
        self.inventory(); self.snapshot()
        for item in self.manifest['files']:
            if 'placement' in item:
                path = Path(self.binding['locations'][item['placement']])
                if path.is_symlink() or not path.is_file() or file_digest(path) != item['digest']:
                    raise PoiseError('Placed Task material missing or changed')
        for tid, spec in self.manifest['workspaces'].items():
            if spec is not None:
                self.validate_tree(tid, spec)
        return {'status': 'complete'}
