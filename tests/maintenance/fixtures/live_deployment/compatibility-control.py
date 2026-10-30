"""Independent configured diagnostic, including a working-version bad route."""
import json
from pathlib import Path
import sys

declaration = json.loads(Path(sys.argv[1]).read_text())
Path(declaration['marker']).write_text(declaration['project'] + '\n')
print(json.dumps({'project': declaration['project'],
                  'compatible': declaration['exit_code'] == 0}))
raise SystemExit(declaration['exit_code'])
