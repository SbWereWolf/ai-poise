
# Test-owned observation appended to the actual configured stage subject.
import os
from pathlib import Path
if 'CHECK_STAGE_COUNTER' in os.environ:
    with Path(os.environ['CHECK_STAGE_COUNTER']).open('a') as stream:
        stream.write('stage-command\n')
