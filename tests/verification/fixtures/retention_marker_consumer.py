import os
from pathlib import Path

marker = Path(os.environ['RUN_MARKER'])
marker.write_text(marker.read_text() + 'run\n' if marker.exists() else 'run\n')
print('consumer-completed')
