"""A task-owned compatibility check; never installs shared dependencies."""
from pathlib import Path
import sys
source = Path(sys.argv[1])
raise SystemExit(0 if source.is_file() and source.read_bytes() == b'Compatible task component\n' else 13)
