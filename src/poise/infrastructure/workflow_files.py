"""Explicit project-owned workflow files, independent of configuration placement."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from ..modules.foundation.errors import PoiseError


def workflow_path(config_path: Path, value: str) -> Path:
    """Use an absolute selection as supplied; resolve relative to its manifest."""
    selected = Path(value)
    if selected.is_absolute():
        return selected
    return (Path(config_path).resolve().parent / selected).resolve()


def read_workflows(config_path: Path, selections: dict, *, documents: dict | None = None) -> dict:
    """Read selected files, or inspect a complete in-memory candidate without I/O."""
    if documents is not None:
        if set(documents) != set(selections):
            raise PoiseError('Workflow candidate must contain every selected graph')
        return deepcopy(documents)
    result = {}
    for goal_type, value in selections.items():
        path = workflow_path(config_path, value)
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise PoiseError(f'Не удалось прочитать JSON {path}: {exc}') from exc
        if not isinstance(document, dict):
            raise PoiseError(f'Ожидался JSON-объект workflow {path}')
        result[goal_type] = document
    return result


def read_project_workflows(config_path: Path) -> dict:
    """Read the selected project graphs without composing runtime services."""
    from ..common import read_json
    document = read_json(config_path)
    return read_workflows(config_path, document['workflows'])
