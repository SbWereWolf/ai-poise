"""Complete file-backed streams and declared child evidence manifest."""
import hashlib
from pathlib import Path

from ..goal_config import atomic_write
from .inputs import canonical


def file_reference(path):
    raw = Path(path).read_bytes()
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


class EvidenceCollector:
    def __init__(self, file_mode):
        self.file_mode = file_mode

    def collect(self, root, contract):
        if contract is None:
            return {'status': 'not_declared', 'manifest_path': None, 'files': []}
        files, missing = {}, []
        for pattern in contract['required_globs']:
            matches = [v for v in sorted(root.glob(pattern)) if v.is_file()]
            if not matches:
                missing.append(pattern)
            for path in matches:
                files[str(path)] = file_reference(path)
        for pattern in contract['optional_globs']:
            for path in sorted(root.glob(pattern)):
                if path.is_file():
                    files[str(path)] = file_reference(path)
        selected = [files[v] for v in sorted(files)]
        status = 'missing' if missing else 'complete'
        if len(selected) > contract['max_files'] or sum(v['bytes'] for v in selected) > contract['max_total_bytes']:
            status = 'incomplete'
        manifest = root / 'manifest.json'
        value = {'schema_version': 1, 'evidence_id': contract['evidence_id'],
                 'retention_class': contract['retention_class'], 'status': status,
                 'missing': missing, 'files': selected}
        atomic_write(manifest, canonical(value) + b'\n', self.file_mode)
        return {'status': status, 'manifest_path': str(manifest), 'files': selected}
