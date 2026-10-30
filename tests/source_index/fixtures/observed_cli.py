"""Read-only C-call observer; never grants admission or changes a product call."""
import os
from pathlib import Path
import runpy
import sys
import threading

marker, selected_lock = Path(sys.argv[1]), sys.argv[2]
sys.argv = ['poise', *sys.argv[3:]]


def observe(frame, event, function):
    if (event == 'c_exception' and getattr(function, '__module__', None) == 'fcntl'
            and getattr(function, '__name__', None) == 'flock'
            and str(frame.f_locals.get('path')) == selected_lock):
        marker.write_text(str(os.getpid()))


sys.setprofile(observe)
threading.setprofile(observe)
runpy.run_module('poise', run_name='__main__')
