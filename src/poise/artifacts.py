from __future__ import annotations
import uuid
from pathlib import Path
from .common import PoiseError, file_digest
from .modules.content_requirements.domain import ArtifactFact, count_artifacts


def inspect_paths(paths: list[str], roots: dict[str, Path], owners: dict[str,str]):
    """Вход агента — только пути. Scope, owner, ID и digest вычисляются здесь."""
    if not isinstance(paths, list) or any(not isinstance(p,str) for p in paths):
        raise PoiseError('artifacts: требуется список путей, не деклараций')
    found = {}
    for raw in paths:
        original = Path(raw)
        if not original.is_absolute():
            raise PoiseError('Артефакт требует абсолютный путь из bootstrap')
        try:
            path = original.resolve(strict=True)
        except OSError as exc:
            raise PoiseError(f'Артефакт не существует: {raw}') from exc
        if not path.is_file():
            raise PoiseError(f'Артефакт должен быть обычным файлом: {raw}')
        scopes = [scope for scope, root in roots.items() if path.is_relative_to(root.resolve())]
        if len(scopes) != 1:
            raise PoiseError(f'Путь вне разрешённых областей runtime/task/sprint: {raw}')
        scope = scopes[0]
        key = (scope, str(path))
        found[key] = {'id': str(uuid.uuid5(uuid.NAMESPACE_URL, f'{scope}:{owners[scope]}:{path.relative_to(roots[scope].resolve()).as_posix()}')),
                      'scope': scope, 'owner': owners[scope], 'path': str(path),
                      'relative_path': path.relative_to(roots[scope].resolve()).as_posix(),
                      'digest': file_digest(path)}
    return list(found.values())


def check_counts(records: list[dict], rules: list[dict]) -> None:
    facts = tuple(ArtifactFact(r['id'],r['scope'],r['relative_path']) for r in records)
    for rule in rules:
        count = count_artifacts(facts,rule['scope'],rule['pattern'])
        if not rule['minimum'] <= count <= rule['maximum']:
            raise PoiseError(f"Артефакты: количество {rule['scope']}:{rule['pattern']} = {count}, "
                               f"требуется {rule['minimum']}..{rule['maximum']}")
