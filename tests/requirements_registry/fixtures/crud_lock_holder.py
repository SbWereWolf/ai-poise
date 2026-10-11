"""Test-only synchronized external flock holder; stdin EOF releases it."""
import fcntl
from pathlib import Path
import sys


with Path(sys.argv[1]).open("a+b") as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    print("locked", flush=True)
    sys.stdin.buffer.read()
