"""Stable RED evidence for the checkout-local asset CLI contract."""

from __future__ import annotations

import contextlib
import io
import pytest


def main() -> int:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        result = pytest.main([
            "-q",
            "tests/local_assets/test_cli.py::test_restore_is_exact_and_repeat_preserves_bytes_and_mtime",
        ])
    recorded = output.getvalue()
    if result == 1 and "invalid choice: 'local-assets'" in recorded:
        print("RED: selected restore test fails because local-assets command is absent")
        return 1
    print("RED evidence mismatch")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
