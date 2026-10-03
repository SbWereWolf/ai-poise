"""A test-owned descendant remains in the leader's process group."""
import os
import time

if os.fork() == 0:
    os.close(1)
    os.close(2)
    time.sleep(0.35)
    with open('descendant-effect', 'w') as stream:
        stream.write('still running')
    os._exit(0)

print('leader-finished')
