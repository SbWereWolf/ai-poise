"""One invocation-owned working-tree observation without changing the real index."""
import os
from pathlib import Path
import shutil
import tempfile


def snapshot_tree(git, worktree, directory, prefix):
    directory.mkdir(parents=True, exist_ok=True)
    invocation = Path(tempfile.mkdtemp(prefix=prefix, dir=directory))
    env = {**os.environ, 'GIT_INDEX_FILE': str(invocation / 'index')}
    try:
        git(worktree, 'read-tree', 'HEAD', env=env)
        git(worktree, 'add', '--all', env=env)
        return git(worktree, 'write-tree', env=env)
    finally:
        shutil.rmtree(invocation)
