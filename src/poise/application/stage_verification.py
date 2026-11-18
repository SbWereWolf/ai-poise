"""Canonical public result/artifact preparation shared by verify and handoff."""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common import digest
from ..modules.foundation.errors import PoiseError
from .check_attempts import PendingVerificationRetry, pending_retry, retry_continuation


@dataclass(frozen=True)
class PreparedVerification:
    payload: dict | None
    packet_digest: str | None
    factory: Any
    artifacts: tuple
    outcome: dict | None


def prepare_candidate(factory, args):
    payload = deepcopy(args['result'])
    if not isinstance(payload.get('artifact_paths'), list) or any(
            not isinstance(p, str) or not p for p in payload['artifact_paths']):
        raise PoiseError('artifact_paths must be an explicit list')
    artifacts = tuple(factory.prepare(args['artifacts']))
    payload['artifact_paths'] = list(dict.fromkeys(
        payload['artifact_paths'] + [str(item.path) for item in artifacts]))
    return payload, artifacts


def candidate_artifact_digest(packet, paths, artifacts):
    if not paths:
        return None
    by_path = {item['path']: item['digest'] for item in artifacts}
    submitted = {str(Path(path).resolve()) for path in paths}
    if not submitted <= by_path.keys():
        raise PoiseError('Submitted verification artifact missing or changed')
    return digest({'packet': packet, 'artifacts': {path: by_path[path] for path in submitted}})


class StageResultVerification:
    def __init__(self, runtime):
        self.runtime = runtime

    def prepare(self, args):
        h = self.runtime
        data = h.current_task()
        retry = pending_retry(data.get('pending')) if data else None
        if retry is not None:
            raise PendingVerificationRetry(retry_continuation(retry))
        packet_digest = h.packet_digest(args)
        if data is None:
            if args['result'] is not None or args['artifacts']:
                raise PoiseError('Taskless read-only verify takes explicit null result and no artifacts')
            return PreparedVerification(None, None, None, (), None)
        if data['status'] == 'verified':
            if not (args['result'] is None and not args['artifacts']) and h.work_resources.packet(data) != packet_digest:
                raise PoiseError('Different result after delivery requires rework')
            return PreparedVerification(None, None, None, (), None)
        if data['status'] != 'active' or args['result'] is None:
            raise PoiseError('Active verify requires an explicit result object')
        factory = h.work_resources.factory()
        payload, artifacts = prepare_candidate(factory, args)
        if not h.validate_stage_entry(data)['passed']:
            raise PoiseError('stage entry requirements are no longer satisfied')
        h.validate_stage_result(data['id'], payload)
        h.validate_stage_scope(data)
        refusal = h.requirements_commands.prepare_task_migrations()
        if refusal is not None:
            return PreparedVerification(payload, packet_digest, factory, artifacts, refusal)
        continuation = h.precheck_observation_continuation(data, payload)
        if continuation is not None:
            return PreparedVerification(payload, packet_digest, factory, artifacts, continuation)
        h.validate_verification_artifacts(args['result']['artifact_paths'], data)
        return PreparedVerification(payload, packet_digest, factory, artifacts, None)

    def execute(self, prepared):
        if prepared.outcome is not None:
            return prepared.outcome
        if prepared.factory is not None:
            prepared.factory.materialize(prepared.artifacts)
        return self.runtime.verify(prepared.payload, packet_digest=prepared.packet_digest)

    def verify(self, args):
        if 'retry' in args:
            from .verification_retry import VerificationRetry
            return VerificationRetry(self.runtime).verify(args)
        return self.execute(self.prepare(args))
