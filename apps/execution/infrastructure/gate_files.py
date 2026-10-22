"""Finite real file observations; domain owners decide what their counts mean."""
from pathlib import Path
from apps.execution.domain.process.content_policy import ArtifactFact


class GateFiles:
    def __init__(self, roots):
        self.roots = roots

    def _classify(self, path):
        resolved = path.resolve()
        if not resolved.is_file():
            raise ValueError('Provide an existing regular result file')
        matches = [(scope, root) for scope, root in self.roots.items()
                   if resolved.is_relative_to(root)]
        if not matches:
            raise ValueError('Result file needs a configured runtime/task/sprint scope root')
        depth = max(len(root.parts) for _, root in matches)
        deepest = [(scope, root) for scope, root in matches if len(root.parts) == depth]
        if len(deepest) != 1:
            raise ValueError('Result file scope is ambiguous; configure distinct scope roots')
        scope, root = deepest[0]
        return scope, resolved.relative_to(root).as_posix()

    def observe(self, known_catalogue, supplied_paths):
        known = {(item['scope'], item['relative_path']) for item in known_catalogue}
        actual = set()
        errors = []
        for scope, relative in sorted(known):
            try:
                actual.add(self._classify(self.roots[scope] / relative))
            except (OSError, ValueError):
                # A known path is historical; only present regular files count now.
                continue
        for raw in supplied_paths:
            try:
                observed = self._classify(Path(raw))
            except (OSError, ValueError) as exc:
                errors.append({'path': raw, 'reason': str(exc)})
            else:
                actual.add(observed)
                known.add(observed)
        files = [{'scope': scope, 'relative_path': path} for scope, path in sorted(actual)]
        catalogue = [{'scope': scope, 'relative_path': path} for scope, path in sorted(known)]
        facts = tuple(ArtifactFact(f'{scope}:{path}', scope, path) for scope, path in sorted(actual))
        return facts, files, catalogue, errors
