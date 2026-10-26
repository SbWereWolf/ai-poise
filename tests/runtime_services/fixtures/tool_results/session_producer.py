"""A harmless actual process fixture; it prepares no result presentation."""
import argparse
from pathlib import Path
import sys

p = argparse.ArgumentParser()
p.add_argument('--stdout', type=Path)
p.add_argument('--stderr', type=Path)
p.add_argument('--counter', type=Path, required=True)
p.add_argument('--exit-code', type=int, required=True)
a = p.parse_args()
a.counter.write_text(str(int(a.counter.read_text()) + 1 if a.counter.exists() else 1))
if a.stdout is not None:
    sys.stdout.buffer.write(a.stdout.read_bytes())
if a.stderr is not None:
    sys.stderr.buffer.write(a.stderr.read_bytes())
raise SystemExit(a.exit_code)
