"""Actual upstream command counter: a repeated execution changes the oracle."""
from pathlib import Path
import sys

path = Path(sys.argv[1])
with path.open("ab") as stream:
    stream.write(b"executed\n")
sys.stdout.write("actual user tool output\n")
