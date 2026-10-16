"""Seal the retained archive without including the manifest in its own digest."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).parent
manifest = root / 'FINAL-ARCHIVE-MANIFEST.json'
assert not manifest.exists()
items = [{'path': str(path.relative_to(root)), 'bytes': path.stat().st_size,
          'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
         for path in sorted(root.rglob('*')) if path.is_file() and path != manifest]
manifest.write_text(json.dumps({'classification': 'retained_operational_and_task_evidence',
                                'files': items}, indent=2) + '\n')
for item in items:
    assert hashlib.sha256((root / item['path']).read_bytes()).hexdigest() == item['sha256']
print(json.dumps({'status': 'verified', 'files': len(items)}))
