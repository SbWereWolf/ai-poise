"""Git effects for an existing, explicitly supplied Task worktree."""
from pathlib import Path

from ..modules.foundation.errors import PoiseError
from ..modules.foundation.paths import matches_allowed_path


class GitReplayWorkspace:
    def __init__(self, git, data, repository):
        self.git = git
        self.data = data
        self.root = Path(data['worktree'])
        self.repository = Path(repository)

    def head(self):
        return self.git(self.root, 'rev-parse', 'HEAD')

    def require_clean(self):
        if self.root.is_symlink() or not self.root.is_dir():
            raise PoiseError('Replay requires the original nonsymlink workspace')
        if self.git(self.root, 'symbolic-ref', '--short', 'HEAD') != self.data['branch']:
            raise PoiseError('Replay workspace branch changed')
        for operation in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply'):
            path = Path(self.git(self.root, 'rev-parse', '--git-path', operation))
            if not path.is_absolute():
                path = self.root / path
            if path.exists():
                raise PoiseError('Replay refuses an unfinished Git operation; preserve task work')
        if self.git(self.root, 'status', '--porcelain=v1', '--untracked-files=all'):
            raise PoiseError('Commit all owned task work before rewind; workspace is dirty or untracked')

    def inspect_preservation(self, sources):
        self.require_clean()
        common = self.git(self.root, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        if common != self.git(self.repository, 'rev-parse', '--path-format=absolute', '--git-common-dir'):
            raise PoiseError('Replay workspace belongs to another repository')
        registered = self.git(self.repository, 'worktree', 'list', '--porcelain')
        if f'worktree {self.root.resolve()}\n' not in registered + '\n':
            raise PoiseError('Replay requires the registered original Task worktree')
        allowed = [pattern for contract in self.data['contract']['stage_contracts']
                   for pattern in contract['allowed_paths']]
        changed = filter(None, self.git(self.root, 'diff', '--name-only', '-z',
                                        self.data['base'], 'HEAD').split('\0'))
        if any(not any(matches_allowed_path(path, pattern) for pattern in allowed) for path in changed):
            raise PoiseError('Preservation commit contains forbidden out-of-scope configuration or files')
        return self.head()

    def current_identity(self):
        self.require_clean()
        return self.head(), self.git(self.root, 'rev-parse', 'HEAD^{tree}')

    def require_current(self, commit, tree):
        if self.current_identity() != (commit, tree):
            raise PoiseError('Current replay checkpoint changed; preserve work before continuing')

    def require_no_ignored_collisions(self, commits):
        ignored = set(filter(None, self.git(self.root, 'ls-files', '--others', '--ignored',
                                            '--exclude-standard', '-z').split('\0')))
        for commit in commits:
            paths = set(filter(None, self.git(self.root, 'ls-tree', '-r', '--name-only',
                                              '-z', commit).split('\0')))
            if any(left == right or left.startswith(right + '/') or right.startswith(left + '/')
                   for left in ignored for right in paths):
                raise PoiseError('Ignored files collide with accepted checkout; preserve them first')

    def contains(self, commit):
        try:
            return self.git(self.root, 'rev-parse', '--verify', f'{commit}^{{commit}}') == commit
        except PoiseError:
            return False

    def ensure_recovery_ref(self, ref, commit):
        try:
            existing = self.git(self.root, 'rev-parse', '--verify', ref)
        except PoiseError:
            self.git(self.root, 'update-ref', ref, commit, '0' * len(commit))
            existing = self.git(self.root, 'rev-parse', '--verify', ref)
        if existing != commit:
            raise PoiseError('Replay recovery reference does not preserve original work')

    def checkout_accepted(self, commit, tree, previous):
        self.require_clean()
        head = self.head()
        if head not in (previous, commit):
            raise PoiseError('Replay checkout drift; preserve work before recovery')
        if head != commit:
            self.require_no_ignored_collisions([commit])
            self.git(self.root, 'reset', '--hard', commit)
        if (self.head() != commit or self.git(self.root, 'write-tree') != tree
                or self.git(self.root, 'diff', '--name-only')
                or self.git(self.root, 'diff', '--cached', '--name-only', commit)):
            raise PoiseError('Historical checkout/index/working bytes do not match accepted source')
