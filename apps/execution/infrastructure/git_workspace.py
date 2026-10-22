"""Invocation-owned Git observation and physical commit mechanics."""
import os
from pathlib import Path
import subprocess
import tempfile

from apps.execution.errors import ExecutionError, StageCommitError


GIT_FAILURES = (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired)


def command_detail(error):
    values = [str(error)]
    for name in ('stdout', 'stderr'):
        value = getattr(error, name, None)
        if value:
            values.append(f'{name}: ' + (value.decode('utf-8', errors='replace')
                                        if isinstance(value, bytes) else value))
    return '\n'.join(values)


class GitWorkspace:
    def __init__(self, root, seconds, observations):
        self.root = Path(root)
        self.seconds = seconds
        self.observations = Path(observations)

    def _run(self, *args, env=None):
        result = subprocess.run(['git', *args], cwd=self.root,
                                env=env, capture_output=True,
                                timeout=self.seconds, check=True)
        return result.stdout.decode('utf-8')

    def _ref(self, expression):
        return self._run('rev-parse', '--verify', '--end-of-options', expression).strip()

    def _snapshot(self, head_commit):
        self.observations.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='stage-', dir=self.observations) as directory:
            environment = {**os.environ, 'GIT_INDEX_FILE': str(Path(directory) / 'index')}
            self._run('read-tree', head_commit, env=environment)
            self._run('add', '--all', env=environment)
            return self._run('write-tree', env=environment).strip()

    def _paths(self, before, after):
        raw = self._run('diff', '--name-only', '--no-renames',
                        '--ignore-submodules=none', '-z', before, after)
        return sorted(set(name for name in raw.split('\0') if name))

    def observe(self, baseline_ref):
        try:
            actual_root = Path(self._run('rev-parse', '--show-toplevel').removesuffix('\n'))
            if actual_root.resolve() != self.root.resolve():
                raise ExecutionError('Select the declared root of the Git working copy')
            baseline = self._ref(baseline_ref + '^{tree}')
            head = self._ref('HEAD')
            head_tree = self._ref(head + '^{tree}')
            candidate = self._snapshot(head)
            return {'baseline_tree': baseline, 'head_commit': head, 'head_tree': head_tree,
                    'candidate_tree': candidate, 'stage_paths': self._paths(baseline, candidate),
                    'commit_paths': self._paths(head_tree, candidate)}
        except GIT_FAILURES as error:
            raise ExecutionError('Git observation failed: ' + command_detail(error)) from error

    def _commit_error(self, phase, before_head, candidate_tree, error):
        try:
            after_head = self._ref('HEAD')
        except GIT_FAILURES:
            after_head = None
        effect = ('unknown' if isinstance(error, subprocess.TimeoutExpired) or after_head is None
                  else 'head_changed' if after_head != before_head else 'none')
        raise StageCommitError(phase=phase, effect=effect, before_head=before_head,
                               after_head=after_head, candidate_tree=candidate_tree,
                               detail=command_detail(error)) from error

    def commit(self, candidate_tree, message, actor):
        try:
            before_head = self._ref('HEAD')
        except GIT_FAILURES as error:
            raise ExecutionError('Git commit preflight failed: ' + command_detail(error)) from error
        phase = 'add'
        try:
            self._run('add', '--all')
            phase = 'index'
            if self._run('write-tree').strip() != candidate_tree:
                raise ValueError('The real index differs from the admitted candidate')
            phase = 'commit'
            environment = {**os.environ,
                           'GIT_AUTHOR_NAME': actor['author_name'],
                           'GIT_AUTHOR_EMAIL': actor['author_email'],
                           'GIT_COMMITTER_NAME': actor['author_name'],
                           'GIT_COMMITTER_EMAIL': actor['author_email']}
            self._run('commit', '-m', message, env=environment)
            phase = 'postcondition'
            commit = self._ref('HEAD')
            tree = self._ref(commit + '^{tree}')
            if tree != candidate_tree or self._snapshot(commit) != candidate_tree:
                raise ValueError('The actual commit/working tree differs from the admitted candidate')
            return commit, tree
        except (*GIT_FAILURES, ValueError) as error:
            self._commit_error(phase, before_head, candidate_tree, error)
