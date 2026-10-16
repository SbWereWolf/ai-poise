"""Pure repository-path matching shared by workflow and creation contracts."""
from __future__ import annotations

import fnmatch
from functools import lru_cache


def matches_component_path(path: str, pattern: str) -> bool:
    """Match whole components; ** consumes zero or more directories."""
    parts, patterns = tuple(path.split('/')), tuple(pattern.split('/'))

    @lru_cache(maxsize=None)
    def match(i: int, j: int) -> bool:
        if j == len(patterns):
            return i == len(parts)
        if patterns[j] == '**':
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], patterns[j]) and match(i + 1, j + 1)

    return match(0, 0)


def matches_allowed_path(path: str, pattern: str) -> bool:
    """Match files and the explicit root of a declared recursive subtree."""
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.endswith("/**") and path == pattern[:-3]
    )
