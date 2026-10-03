"""Test-owned executable for integration output contracts."""
import os
from pathlib import Path

root = Path(os.environ["POISE_RUN_OUTPUT_DIR"])
assert root.is_dir()
mode = os.environ["FIXTURE_OUTPUT_MODE"]
mode_file = Path(os.environ["FIXTURE_OUTPUT_MODE_FILE"])
if mode_file.exists():
    mode = mode_file.read_text()
failure_marker = os.environ["FIXTURE_OUTPUT_FAILURE_MARKER"]
if failure_marker and Path(failure_marker).exists():
    mode = "missing"
if mode in ("required", "optional", "empty-file"):
    (root / "result.txt").write_bytes(b"" if mode == "empty-file" else b"candidate output\n")
elif mode in ("invalid", "optional-invalid"):
    (root / "result.txt").mkdir()
elif mode == "escape":
    outside = root.parent / "outside.txt"
    outside.write_bytes(b"outside must not be captured\n")
    (root / "result.txt").symlink_to(outside)
print("producer-ran")
