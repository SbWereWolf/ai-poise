"""A bounded test-owned escaped writer prevents complete capture."""
import os
import time

ready_read, ready_write = os.pipe()
if os.fork() == 0:
    os.close(ready_read)
    os.setsid()
    os.write(ready_write, b'R')
    os.close(ready_write)
    time.sleep(0.6)
    os._exit(0)

os.close(ready_write)
assert os.read(ready_read, 1) == b'R'
os.close(ready_read)
print('leader-finished', flush=True)
