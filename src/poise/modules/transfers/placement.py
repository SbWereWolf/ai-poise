"""Pure logical-to-physical locations shared by all saved-work consumers."""
from ..artifact_factory.domain import relative

from ..foundation.errors import DomainError


def relocate_path(value, locations):
    if not isinstance(value, str):
        return value
    for old, new in sorted(locations.items(), key=lambda item: len(item[0]), reverse=True):
        if value == old:
            return new
        if value.startswith(old + '/'):
            return new + value[len(old):]
    return value


def material_source(task_root, material, locations):
    """Resolve explicit source bindings, without inspecting the filesystem."""
    root = task_root.rstrip('/')
    for old, new in locations.items():
        for value in (old, new):
            if not isinstance(value, str) or not value.startswith('/'):
                raise DomainError('Invalid saved absolute location binding')
            relative(value[1:])
    roots = {old for old, new in locations.items() if new == root} | {root}
    candidates = set()
    for original_root in roots:
        prefix = original_root.rstrip('/') + '/' + material['source']
        for old, new in locations.items():
            if old != prefix and not old.startswith(prefix + '/'):
                continue
            suffix = old[len(prefix):]
            if suffix and not new.endswith(suffix):
                raise DomainError('Delivered material suffix differs from explicit source')
            current = new[:-len(suffix)] if suffix else new
            if not current.startswith(root + '/'):
                raise DomainError('Delivered material source is outside its Task owner')
            relative(current[len(root) + 1:])
            candidates.add(current)
    if len(candidates) > 1:
        raise DomainError('Conflicting delivered material source bindings')
    return next(iter(candidates)) if candidates else root + '/' + material['source']
