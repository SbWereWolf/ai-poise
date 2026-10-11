"""Test fault: real lock acquired, readiness announced, termination ignored."""
import fcntl
from pathlib import Path
import signal
import sys


with Path(sys.argv[1]).open("a+b") as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print("locked", flush=True)
    sys.stdin.buffer.read()
    while True:
        signal.pause()
