"""Canonical public result/artifact preparation shared by verify and handoff."""
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..modules.foundation.errors import PoiseError


@dataclass(frozen=True)
class PreparedVerification:
    payload: dict | None
    packet_digest: str | None
    factory: Any
    artifacts: tuple
    outcome: dict | None


class StageResultVerification:
    def __init__(self, runtime):
        self.runtime = runtime

    def prepare(self, args):
        h = self.runtime
        data = h.current_task()
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
        payload = deepcopy(args['result'])
        if not isinstance(payload.get('artifact_paths'), list) or any(
                not isinstance(p, str) or not p for p in payload['artifact_paths']):
            raise PoiseError('artifact_paths must be an explicit list')
        factory = h.work_resources.factory()
        artifacts = tuple(factory.prepare(args['artifacts']))
        payload['artifact_paths'] = list(dict.fromkeys(
            payload['artifact_paths'] + [str(x.path) for x in artifacts]))
        if not h.validate_stage_entry(data)['passed']:
            raise PoiseError('stage entry requirements are no longer satisfied')
        h.validate_stage_result(data['id'], payload)
        h.validate_stage_scope(data)
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
        return self.execute(self.prepare(args))
