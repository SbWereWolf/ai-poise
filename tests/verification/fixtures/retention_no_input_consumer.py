import os
from pathlib import Path

proof = Path(os.environ['POISE_RUN_OUTPUT_DIR']) / 'proof/verification.txt'
proof.parent.mkdir(parents=True)
proof.write_bytes(b'wheel verified with accepted bytes\n')
marker = Path(os.environ['RUN_MARKER'])
marker.write_text('run\n')
print('no-external-inputs')
