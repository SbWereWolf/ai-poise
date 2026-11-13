"""Observe the loaded integration bytes, then invoke the actual public CLI."""
import hashlib
import inspect
import json
from pathlib import Path
import sys

import poise
from poise.__main__ import main
from poise.infrastructure.result_integration import RuntimeResultIntegration

source = Path(inspect.getfile(RuntimeResultIntegration)).resolve()
print(json.dumps({"package_location": str(Path(poise.__file__).resolve()),
                  "integration_source": str(source),
                  "integration_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                  "python": sys.executable}), file=sys.stderr)
raise SystemExit(main())
