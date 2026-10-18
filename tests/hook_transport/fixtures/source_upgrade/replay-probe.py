"""Emit exactly the explicitly selected observation input."""
from pathlib import Path
import sys

sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes())
