"""Real qualified child result; no Poise behavior inside the fixture."""
import sys
sys.stdout.buffer.write(b'OUT\n')
sys.stderr.buffer.write(b'ERR\n')
raise SystemExit(7)
