"""Test-owned external action; marker contents are independently asserted."""
from pathlib import Path
import sys

operation, filename, tag = sys.argv[1:]
marker = Path(filename)
if operation in ("apply", "apply-fail"):
    with marker.open("a", encoding="utf-8") as output:
        output.write(tag + "\n")
    if operation == "apply-fail":
        raise SystemExit(2)
elif operation == "probe":
    raise SystemExit(0 if marker.exists() and tag in marker.read_text().splitlines() else 1)
elif operation == "probe-unknown":
    raise SystemExit(3)
else:
    raise SystemExit(2)
