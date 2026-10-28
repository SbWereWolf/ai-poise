"""A real intentional tool execution with independently visible effects."""
from pathlib import Path
import sys


with Path(sys.argv[1]).open("a", encoding="utf-8") as output:
    output.write("effect\n")
sys.stdout.write("А\n")
raise SystemExit(int(sys.argv[2]))
