"""A test-owned descendant remains in the leader's process group."""
import os
import socket
import sys

leader = os.getpid()
ready_read, ready_write = os.pipe()
if os.fork() == 0:
    os.close(ready_read)
    os.close(1)
    os.close(2)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as control:
        control.connect(sys.argv[1])
        control.sendall(f'{os.getpid()} {os.getpgrp()} {leader}\n'.encode())
        if control.recv(1) != b'R':
            os._exit(1)
        os.write(ready_write, b'R')
        os.close(ready_write)
        # No timer or natural exit can masquerade as owner cleanup. The test
        # closes the control socket in finally if the actual owner is defective.
        if control.recv(1) == b'E':
            with open('descendant-effect', 'w') as stream:
                stream.write('still running')
            control.sendall(b'E')
    os._exit(0)

os.close(ready_write)
assert os.read(ready_read, 1) == b'R'
os.close(ready_read)
print('leader-finished')
