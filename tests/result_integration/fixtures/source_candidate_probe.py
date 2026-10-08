"""Run in a fresh subject interpreter, independently of the controller."""
import fractions
import json
import os
from pathlib import Path

output = os.environ.get("POISE_RUN_OUTPUT_DIR")
if output:
    Path(output, "started.txt").write_text("started\n")
print(json.dumps({
    "marker": getattr(fractions, "SOURCE_MARKER", "stdlib"),
    "file": fractions.__file__,
    "cwd": str(Path.cwd()),
}, sort_keys=True))
assert not Path("target-marker").exists()
