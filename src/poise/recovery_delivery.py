"""Narrow configured delivery callback: no WorkPoise, bootstrap or stage runner."""
import json
from pathlib import Path
import sys

from .common import PoiseError, digest, encoded
from .infrastructure.goal_config import atomic_write
from .infrastructure.recovery_delivery import RecoveryDelivery
from .infrastructure.sqlite.database import Database
from .infrastructure.sqlite.transfers import SqliteTransferRepository


def main(argv):
    descriptor_path, phase = argv
    if phase not in ('place', 'components', 'validate'):
        raise PoiseError('Unknown delivery phase')
    descriptor = json.loads(Path(descriptor_path).read_text())
    database = Database(Path(descriptor['database']), Path(descriptor['database_lock']),
                        descriptor['lock_seconds'], descriptor['lock_poll_seconds'])
    repository = SqliteTransferRepository(database)
    request = repository.request(descriptor['actor'], descriptor['request_id'], descriptor['identity'])
    if request is None or request.get('descriptor_digest') != digest(descriptor):
        raise PoiseError('Delivery descriptor differs from prepared request')
    receipt = Path(descriptor['directory']) / (phase + '-receipt.json')
    if receipt.exists():
        outcome = json.loads(receipt.read_text())
    else:
        try:
            outcome = getattr(RecoveryDelivery(descriptor), phase)()
        except (PoiseError, OSError, ValueError) as exc:
            outcome = {'status': 'failed', 'reason': str(exc)}
        atomic_write(receipt, (encoded(outcome) + '\n').encode(), descriptor['policy']['file_mode'])
    print(encoded(outcome))
    return 0 if outcome['status'] == 'complete' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
