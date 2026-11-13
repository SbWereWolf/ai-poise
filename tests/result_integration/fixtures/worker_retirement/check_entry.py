"""Literal inline check body matching the fixture's existing reference profile."""
import os
from pathlib import Path
import runpy

owner = runpy.run_path(os.environ["WORKER_FIXTURE_SCRIPT"])
raise SystemExit(owner["check"](Path(os.environ["WORKER_FIXTURE_OWNER"])))
