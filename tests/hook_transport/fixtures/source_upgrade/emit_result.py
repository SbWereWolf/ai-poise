"""A real process emits independent, fixed protocol bytes."""
from pathlib import Path
import sys

root = Path(__file__).parent
sys.stdout.buffer.write((root / 'ready.stdout').read_bytes())
sys.stderr.buffer.write((root / 'diagnostic.stderr').read_bytes())
