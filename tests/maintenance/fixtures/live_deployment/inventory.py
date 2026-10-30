"""Return a test-owned external inventory response without deriving its truth."""
import json
from pathlib import Path
import sys

print(json.dumps(json.loads(Path(sys.argv[1]).read_text()), ensure_ascii=False))
