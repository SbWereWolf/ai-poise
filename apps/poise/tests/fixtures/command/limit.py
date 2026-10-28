"""Test-owned real POSIX capture-size limit; no product exception replacement."""
import os
import resource
import signal
import sys

resource.setrlimit(resource.RLIMIT_FSIZE, (1048576,1048576))
signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
os.execv(sys.argv[1],sys.argv[1:])
