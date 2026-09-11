"""Pure repository-path matching shared by workflow and creation contracts."""
from __future__ import annotations

import fnmatch


def matches_allowed_path(path: str, pattern: str) -> bool:
    """Match files and the explicit root of a declared recursive subtree."""
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.endswith("/**") and path == pattern[:-3]
    )
