"""Read the explicit settings → reader profile → command policy chain."""
import hashlib
from pathlib import Path
from ...common import PoiseError
from ...modules.document_intake.command import CommandFailure
from ...modules.document_intake.command_policy import CommandPolicy
from ...modules.document_intake.contracts import ReadFailure
from ...modules.document_intake.policy import ContentReaderPolicy
from ..goal_config import strict_json
from .configuration import policy_bytes


def _absolute_link(owner, group, field):
    if (not isinstance(owner, dict) or not isinstance(owner.get(group), dict)
            or not isinstance(owner[group].get(field), str)
            or not Path(owner[group][field]).is_absolute()):
        raise CommandFailure('invalid_policy', field=group + '.' + field)
    return Path(owner[group][field])


def load_command_policy(profile, *, snapshots=None):
    path = _absolute_link(profile, 'command_intake', 'policy')
    try:
        raw = policy_bytes(path, snapshots)
        policy = CommandPolicy.parse(strict_json(raw.decode('utf-8')))
    except OSError as exc:
        raise CommandFailure('policy_missing', field='command_intake.policy') from exc
    except (PoiseError, ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise CommandFailure('invalid_policy', field='command_intake.policy') from exc
    return policy, hashlib.sha256(raw).hexdigest()


def load_command_profile(settings, *, policy_path=None, snapshots=None):
    path = (_absolute_link(settings.raw, 'tool_intake', 'reader_policy')
            if policy_path is None else Path(policy_path))
    if not path.is_absolute():
        raise CommandFailure('invalid_policy', field='policy_path')
    try:
        profile = ContentReaderPolicy.from_configuration(strict_json(policy_bytes(path, snapshots).decode('utf-8')))
    except ReadFailure as exc:
        raise CommandFailure('invalid_policy', field=exc.field) from exc
    except OSError as exc:
        raise CommandFailure('policy_missing', field='tool_intake.reader_policy') from exc
    except (PoiseError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise CommandFailure('invalid_policy', field='tool_intake.reader_policy') from exc
    command, digest = load_command_policy(profile, snapshots=snapshots)
    return path, profile, command, digest
