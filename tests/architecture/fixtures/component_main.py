"""Independent component process fixture for Bootstrap transport tests."""

import json
import os
from pathlib import Path
import signal
import sys


with Path(os.environ["BOOTSTRAP_TEST_CALLS"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\n")

if "signal" in sys.argv:
    os.kill(os.getpid(), signal.SIGTERM)
if "exit-seven" in sys.argv:
    sys.stdout.buffer.write(b"external stdout\n")
    sys.stderr.buffer.write(b"external stderr\n")
    sys.exit(7)

sys.stdout.buffer.write(b'{"schema":"environment-maintenance/result/v2","status":"ready"}\n')
