"""Safe test-owned already-completed Bash/MCP response producer."""
import argparse
from pathlib import Path
import sys

p = argparse.ArgumentParser()
p.add_argument('--kind', choices=['bash', 'mcp'], required=True)
p.add_argument('--size', choices=['short', 'large'], required=True)
p.add_argument('--counter', type=Path, required=True)
p.add_argument('--exit-code', type=int, required=True)
a = p.parse_args()
a.counter.write_text(str(int(a.counter.read_text()) + 1 if a.counter.exists() else 1))
root = Path(__file__).parent
if a.kind == 'mcp':
    sys.stdout.buffer.write((root / ('mcp-large-response.json' if a.size == 'large' else 'mcp-response.json')).read_bytes())
else:
    sys.stdout.buffer.write((root / (a.size + '.stdout.log')).read_bytes())
    sys.stderr.buffer.write((root / (a.size + '.stderr.log')).read_bytes())
raise SystemExit(a.exit_code)
