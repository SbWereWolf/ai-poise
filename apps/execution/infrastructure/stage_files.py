"""Finite real observations for an explicit Stage policy, separate from artifact counts."""
from pathlib import Path


class StageFiles:
    def __init__(self, artifact_files):
        self.artifact_files = artifact_files

    @staticmethod
    def _locate(path, roots):
        matches = [(key, root) for key, root in roots.items() if path.is_relative_to(root)]
        if not matches:
            return None
        depth = max(len(root.parts) for _, root in matches)
        deepest = [(key, root) for key, root in matches if len(root.parts) == depth]
        if len(deepest) != 1:
            return None
        key, root = deepest[0]
        return key, path.relative_to(root).as_posix()

    def observe(self, policy, stage_id, known_catalogue, supplied_paths):
        roots = {key: Path(path).resolve() for key, path in policy.roots.items()}
        known = {item['path'] for item in known_catalogue}
        supplied = {str(Path(path).resolve()) for path in supplied_paths}
        catalogue = set(known)
        observed, admissions, real_paths = [], [], []
        for raw in sorted(known | supplied):
            path = Path(raw)
            try:
                exists = path.exists()
                regular = path.is_file()
            except OSError as exc:
                exists, regular = False, False
                failure = 'unavailable_file: ' + str(exc)
            else:
                failure = 'missing_file' if not exists else 'not_regular_file'
            # Historical addresses survive absence, but absent history is not current evidence.
            if not regular and raw not in supplied:
                continue
            location = self._locate(path, roots)
            if location is None:
                admission = {'root': None, 'path': raw, 'allowed': False,
                             'reason': 'outside_roots'}
            else:
                key, relative = location
                root = {'kind': key[0], 'position': key[1]}
                if regular:
                    admission = policy.decide(stage_id, root, relative, 'read')
                else:
                    admission = {'root': root, 'path': relative, 'allowed': False, 'reason': failure}
            admissions.append(admission)
            if regular:
                catalogue.add(raw)
                real_paths.append(raw)
                observed.append({'root': admission['root'], 'path': admission['path']})
        # Artifact facts observe real files even if current Stage admission is negative.
        artifact_paths = [raw for raw in real_paths if any(
            Path(raw).is_relative_to(root) for root in self.artifact_files.roots.values())]
        facts, _, _, errors = self.artifact_files.observe([], artifact_paths)
        return (facts, observed, [{'path': path} for path in sorted(catalogue)], errors,
                {'passed': all(item['allowed'] for item in admissions), 'files': admissions})
