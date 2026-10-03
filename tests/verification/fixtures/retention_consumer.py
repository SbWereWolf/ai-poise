import os
from pathlib import Path

data = Path(os.environ['ACCEPTED_WHEEL']).read_bytes()
assert data == b'\x00\xffaccepted-wheel\x80\n', 'retained input bytes differ'
proof = Path(os.environ['POISE_RUN_OUTPUT_DIR']) / 'proof/verification.txt'
proof.parent.mkdir(parents=True)
proof.write_bytes(b'wheel verified with accepted bytes\n')
marker = Path(os.environ['RUN_MARKER'])
marker.write_text(marker.read_text() + 'run\n' if marker.exists() else 'run\n')
print('retained-bytes=' + data.hex())
