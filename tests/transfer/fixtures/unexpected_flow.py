"""A real test-owned tripwire; generic checks must never invoke delivery."""
from pathlib import Path

with Path(__file__).with_name('unexpected-flow.txt').open('a') as output:
    output.write('unexpected delivery invocation\n')
raise SystemExit(97)
