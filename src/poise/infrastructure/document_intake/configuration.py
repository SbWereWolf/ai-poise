"""Explicit project-local reader configuration and complete policy provenance."""
import hashlib
from pathlib import Path
from ...common import PoiseError
from ..goal_config import strict_json
from ...modules.document_intake.policy import ContentReaderPolicy
from ...modules.document_intake.contracts import ReadFailure


class ReaderConfiguration:
    def __init__(self, bound, policy, policy_sha256):
        self.bound, self.policy, self.policy_sha256 = bound, policy, policy_sha256

    @classmethod
    def load(cls, bound, policy_path):
        try:
            if not Path(policy_path).is_absolute():
                raise ReadFailure('invalid_policy', field='policy')
            raw = Path(policy_path).read_bytes()
        except OSError as exc:
            raise ReadFailure('policy_missing') from exc
        try:
            policy = ContentReaderPolicy.from_configuration(strict_json(raw.decode('utf-8')))
        except (PoiseError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise ReadFailure('invalid_policy', field='policy') from exc
        temp = Path(policy['session']['temporary_root']).resolve()
        protected = [policy['session'][k] for k in ('coordination_root', 'diagnostic_file', 'registry_template')]
        protected.append(policy['storage']['control_response_root'])
        if any(Path(p).resolve().is_relative_to(temp) for p in protected):
            raise ReadFailure('lock_resources_conflict')
        return cls(bound, policy, hashlib.sha256(raw).hexdigest())
