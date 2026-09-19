"""Git adapter for exact repository-input facts used by Task creation."""
from __future__ import annotations

from pathlib import Path
import subprocess

from ..modules.foundation.errors import PoiseError


class GitRepositoryTree:
    def __init__(self, repository, timeout_seconds, preview_chars):
        self.repository = Path(repository).resolve(strict=True)
        self.timeout_seconds = timeout_seconds
        self.preview_chars = preview_chars

    def existing_paths(self, revision, paths):
        if not paths:
            return frozenset()
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repository), "ls-tree", "-r", "-z", "--name-only", revision, "--", *paths],
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PoiseError(f"Git tree preflight не завершён: {exc}") from exc
        if result.returncode:
            error = result.stderr.decode(errors="replace")[-self.preview_chars:]
            raise PoiseError(f"Git tree preflight: {error}")
        entries = tuple(
            path.decode() for path in result.stdout.split(b"\0") if path
        )
        return frozenset(
            path
            for path in paths
            if path in entries or any(entry.startswith(path + "/") for entry in entries)
        )

    def contains_commit(self, revision, commit):
        """Check the exact reserved creation base before any Task start effects."""
        try:
            result = subprocess.run(
                ['git', '-C', str(self.repository), 'merge-base', '--is-ancestor', commit, revision],
                capture_output=True, timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PoiseError(f'Git ancestry preflight did not finish: {exc}') from exc
        if result.returncode not in (0, 1):
            raise PoiseError('Git ancestry preflight: ' +
                             result.stderr.decode(errors='replace')[-self.preview_chars:])
        return result.returncode == 0
