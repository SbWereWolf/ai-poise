"""Independent real-executor health marker; not product acceptance evidence."""
from pathlib import Path
import sys

Path(sys.argv[1]).write_bytes(b'healthy2.0-control\n')
print('control-executed')
