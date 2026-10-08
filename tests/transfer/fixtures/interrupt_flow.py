"""Produce a real effect, then interrupt only this disposable flow parent."""
from pathlib import Path
import os
import signal
import sys
with Path(sys.argv[1]).open('a') as stream:
    stream.write('effect\n')
os.kill(os.getppid(), signal.SIGKILL)
