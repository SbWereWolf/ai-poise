"""An actual fixture subject check, not a prewritten command receipt."""
import os
from pathlib import Path
import sys


def test_delivered_value():
    from delivered import VALUE
    assert VALUE == "agreed result"
    print("executed material proof 491b3d")
    print("executed material stderr 491b3d", file=sys.stderr)
    Path(os.environ["POISE_RUN_OUTPUT_DIR"], "raw-proof.bin").write_bytes(
        b"declared material proof 491b3d\x00\xff\n")
