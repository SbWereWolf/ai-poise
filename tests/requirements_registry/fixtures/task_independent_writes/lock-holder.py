"""Hold the real standard lock in an independent test-owned process."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(sys.argv[1]) / '.runtime/dependencies'))
from yoyo import get_backend

backend = get_backend('sqlite:///' + sys.argv[2], migration_table='_poise_requirements_data')
backend.log_table = '_poise_requirements_data_log'
backend.version_table = '_poise_requirements_data_version'
try:
    with backend.lock(timeout=2):
        print('LOCK_READY', flush=True)
        sys.stdin.readline()
finally:
    backend.connection.close()
