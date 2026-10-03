import os
from pathlib import Path

data = Path(os.environ['ACCEPTED_WHEEL']).read_bytes()
assert data == b'\x00\xffaccepted-wheel\x80\n', 'retained wheel bytes differ'
assert Path(os.environ['ACCEPTED_POLICY']).read_bytes() == b'accepted sprint policy\n'
marker = Path(os.environ['RUN_MARKER'])
marker.write_text(marker.read_text() + 'run\n' if marker.exists() else 'run\n')
print('retained-bytes=' + data.hex())
