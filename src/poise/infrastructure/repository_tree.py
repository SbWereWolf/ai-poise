"""Git adapter for explicit commit-tree, index and ignore facts."""
from __future__ import annotations

from pathlib import Path
import os
import subprocess

from ..modules.foundation.errors import PoiseError


class GitRepositoryTree:
    def __init__(self, repository, timeout_seconds, preview_chars):
        self.repository = Path(repository).resolve(strict=True)
        self.timeout_seconds = timeout_seconds
        self.preview_chars = preview_chars

    def _read_path_facts(self, arguments, input_data, allowed_exit_codes):
        try:
            result = subprocess.run(
                ['git', '-C', str(self.repository), *arguments],
                input=input_data, capture_output=True, timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PoiseError(f'Git-проверка путей не завершена: {exc}') from exc
        if result.returncode not in allowed_exit_codes:
            detail = result.stderr.decode(errors='replace')[-self.preview_chars:]
            raise PoiseError(f'Ошибка Git-проверки путей ({result.returncode}): {detail}')
        if ((result.stdout and not result.stdout.endswith(b'\0'))
                or (result.returncode == 1 and result.stdout)
                or (result.returncode == 0 and 1 in allowed_exit_codes and not result.stdout)):
            raise PoiseError('Git вернул некорректные сведения о путях')
        if not result.stdout:
            return frozenset()
        records = result.stdout[:-1].split(b'\0')
        if any(not name for name in records):
            raise PoiseError('Git вернул пустую запись пути')
        return frozenset(os.fsdecode(name) for name in records)

    def current_path_facts(self, paths):
        """Read current tracking and ignore facts without creating path members."""
        tracked = self._read_path_facts(
            ['--literal-pathspecs', 'ls-files', '--cached', '-z', '--', *paths], None, (0,),
        )
        ignored = self._read_path_facts(
            ['check-ignore', '--no-index', '-z', '--stdin'],
            b''.join(os.fsencode(path) + b'\0' for path in paths), (0, 1),
        )
        if (tracked | ignored) - set(paths):
            raise PoiseError('Git вернул сведения о других путях')
        return {'tracked': tracked, 'ignored': ignored}

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
