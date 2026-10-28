"""Test-owned subprocess effect; unrelated to production handoff implementation."""
from pathlib import Path
marker = Path(COUNTER_PATH_LITERAL)
marker.write_text((marker.read_text(encoding="utf-8") if marker.exists() else "") + "x", encoding="utf-8")
print("CHECK_ONCE")
size = OUTPUT_SIZE_LITERAL
if size:
    print("X" * size)
raise SystemExit(EXIT_CODE_LITERAL)
