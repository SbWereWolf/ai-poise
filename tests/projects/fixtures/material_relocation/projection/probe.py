"""Disposable subprocess entry for the repository's existing pytest projection."""
import json
from pathlib import Path
import sys

from tests.projects.material_relocation_pytest import run

code, summary = run([sys.argv[1]], json.loads(sys.argv[3]), Path(sys.argv[2]))
print(json.dumps(summary, sort_keys=True))
raise SystemExit(code)
