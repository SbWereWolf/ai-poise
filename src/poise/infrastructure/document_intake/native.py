"""Read-only native session observations.

The mandatory service is the public Poise composition owner. Observations do
not acquire its effect fence and never grant permission to delete resources.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path

from ...modules.session_establishment.domain import CallerIdentity
from ...modules.hook_transport.domain import HookDefinition
from ...common import PoiseError


_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")
_START_SOURCES = ("startup", "resume", "clear", "compact", "fork")
_EPISODE_FIELDS = {"project_id", "session_id", "binding_id", "start_event_id"}


class _ObservationFailure(Exception):
    """An explicitly invalid or unavailable public owner observation."""


def _identifier(value) -> bool:
    try:
        CallerIdentity.native('document-intake', value)
        return True
    except (TypeError, ValueError, PoiseError):
        return False


def _positive_id(value) -> bool:
    return type(value) is int and value > 0


def _snapshot(state, episode=None, end_event_id=None, cause=None) -> dict:
    return {
        "schema_version": 1, "state": state, "episode": episode,
        "end_event_id": end_event_id, "cause": cause,
    }


class HookNativeAuthority:
    def __init__(self, *, service, project_id: str, definition_path: Path, authority_identity: dict) -> None:
        self.identity = dict(authority_identity)
        if not _identifier(project_id):
            raise ValueError("Supply an explicit native project identifier")
        self.service = service
        self.project_id = project_id
        self.definition_path = definition_path.resolve()
        self.definition = HookDefinition.parse(json.loads(self.definition_path.read_bytes()))

    def _valid_episode(self, episode) -> bool:
        return (
            isinstance(episode, Mapping) and set(episode) == _EPISODE_FIELDS
            and all(_identifier(episode[field]) for field in ("project_id", "session_id", "binding_id"))
            and episode["project_id"] == self.project_id
            and _positive_id(episode["start_event_id"])
        )

    def _binding(self, session_id):
        binding = self.service.latest_binding(session_id, self.definition.data['agent_id'])
        if (not binding or binding.get('project') != self.project_id
                or binding.get('external_session') != session_id
                or binding.get('definition_path') != str(self.definition_path)):
            raise _ObservationFailure('Native authority association differs')
        return binding['session_id']

    def _packet(self, binding_id, event, event_id=None):
        return {
            "definition_path": str(self.definition_path), "session_id": binding_id,
            "event": event, "event_id": event_id, "run_ids": [], "probe_cwd": None,
        }

    def _event(self, diagnostic, binding_id, kind):
        if not isinstance(diagnostic, dict) or diagnostic.get("schema") != "poise-hook-diagnostics-1":
            raise _ObservationFailure("Unsupported native diagnostics schema")
        if diagnostic.get("status") not in ("diagnosed", "inconclusive"):
            raise _ObservationFailure("Unsupported native diagnostics status")
        if diagnostic.get("consistency") != "independent_owner_snapshots":
            raise _ObservationFailure("Unsupported native diagnostics consistency")
        installation = diagnostic.get("installation")
        if not isinstance(installation, dict) or installation.get("status") != "configured" or installation.get("definition_path") != str(self.definition_path):
            raise _ObservationFailure("Native installation differs from the configured definition")
        binding = diagnostic.get("binding")
        if not isinstance(binding, dict) or binding.get("status") != "valid" or binding.get("session_id") != binding_id:
            raise _ObservationFailure("Native session binding is not valid")
        event = diagnostic.get("native_event")
        if isinstance(event, dict) and event.get("status") == "not_observed":
            return None
        if not isinstance(event, dict) or event.get("status") != "observed":
            raise _ObservationFailure(f"Native {kind} observation is unavailable")
        if event.get("session_id") != binding["session_id"]:
            raise _ObservationFailure("Native event identifies another session")
        if event.get("event") != kind:
            raise _ObservationFailure("Native event kind differs from the requested event")
        if not _positive_id(event.get("id")):
            raise _ObservationFailure("Native event ID must be a positive integer")
        if kind == "SessionStart" and event.get("source") not in _START_SOURCES:
            raise _ObservationFailure("Unsupported native SessionStart source")
        return event

    def observe(self, episode) -> dict:
        if not self._valid_episode(episode):
            return _snapshot("unknown", cause="Episode identity is invalid")
        return self._inspect_episode(episode["session_id"], episode, observe_owned=True)

    def resolve_episode(self, session_id, owned_episode) -> dict:
        return self._inspect_episode(session_id, owned_episode, observe_owned=False)

    def _inspect_episode(self, session_id, owned_episode, *, observe_owned):
        if not _identifier(session_id) or (owned_episode is not None and (
            not self._valid_episode(owned_episode) or owned_episode["session_id"] != session_id
        )):
            return _snapshot("unknown", cause="Episode identity is invalid")
        try:
            # One public association lookup per observation, then the SDK batch.
            # Native session names and internal binding IDs remain distinct.
            binding_id = self._binding(session_id)
            queries = [dict(id=kind, request=self._packet(binding_id, kind)) for kind in _EVENTS]
            if owned_episode is not None:
                queries.append(dict(id='anchor', request=self._packet(binding_id, 'SessionStart', owned_episode['start_event_id'])))
            response = self.service.diagnose({'queries': queries})
            snapshots = {entry['id']: entry['result'] for entry in response['results']}
            diagnostics = {kind: snapshots[kind] for kind in _EVENTS}
            anchor_diagnostic = None
            if owned_episode is not None:
                anchor_diagnostic = snapshots['anchor']
            events = {kind: self._event(value, binding_id, kind) for kind, value in diagnostics.items()}
            start = events["SessionStart"]
            if start is None:
                raise _ObservationFailure("Native SessionStart is not observed")
            binding_id = diagnostics["SessionStart"]["binding"]["session_id"]
            observed_events = list(events.values())
            if owned_episode is not None:
                if owned_episode["binding_id"] != binding_id:
                    raise _ObservationFailure("Native episode binding differs from observed binding")
                anchor = self._event(anchor_diagnostic, binding_id, "SessionStart")
                if anchor is None:
                    raise _ObservationFailure("Native episode anchor is not observed")
                if anchor["id"] != owned_episode["start_event_id"]:
                    raise _ObservationFailure("Native episode anchor does not match the requested event")
                if start["id"] < anchor["id"]:
                    raise _ObservationFailure("Native latest SessionStart precedes the owned episode anchor")
                observed_events.append(anchor)
            event_kinds_by_id = {}
            for event in observed_events:
                if event is None:
                    continue
                event_id = event["id"]
                if event_id in event_kinds_by_id and event_kinds_by_id[event_id] != event["event"]:
                    raise _ObservationFailure("Native event IDs identify contradictory event kinds")
                event_kinds_by_id[event_id] = event["event"]
            end = events["SessionEnd"]
            if end is not None and end["id"] > start["id"]:
                if any(events[kind] is not None and events[kind]["id"] > end["id"] for kind in ("UserPromptSubmit", "Stop")):
                    raise _ObservationFailure("Native activity follows SessionEnd without a later SessionStart")
            if observe_owned and end is not None and end["id"] > owned_episode["start_event_id"]:
                return _snapshot("ended", dict(owned_episode), end["id"])
            if end is not None and end["id"] > start["id"]:
                episode = dict(owned_episode) if owned_episode is not None else self._episode(session_id, binding_id, start["id"])
                return _snapshot("ended", episode, end["id"])
            if owned_episode is not None and (end is None or end["id"] < owned_episode["start_event_id"]):
                episode = dict(owned_episode)
            else:
                episode = self._episode(session_id, binding_id, start["id"])
            return _snapshot("active", episode)
        except _ObservationFailure as error:
            return _snapshot("unknown", cause=str(error))
        except Exception as error:
            return _snapshot("unknown", cause=f"Native diagnostics failed: {type(error).__name__}")

    def _episode(self, session_id, binding_id, start_event_id):
        return {
            "project_id": self.project_id, "session_id": session_id,
            "binding_id": binding_id, "start_event_id": start_event_id,
        }

    def verify_end_receipt(self, receipt) -> bool:
        if not isinstance(receipt, dict) or set(receipt) != {"schema_version", "event", "event_id", "episode"}:
            return False
        if type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1 or receipt["event"] != "SessionEnd" or not _positive_id(receipt["event_id"]):
            return False
        observed = self.observe(receipt["episode"])
        if observed["state"] != "ended" or observed["episode"] != receipt["episode"] or receipt["event_id"] <= receipt["episode"]["start_event_id"]:
            return False
        try:
            binding_id = self._binding(receipt['episode']['session_id'])
            exact = self._event(self.service.diagnose(self._packet(binding_id, 'SessionEnd', receipt['event_id'])), binding_id, 'SessionEnd')
            return exact is not None and exact["id"] == receipt["event_id"]
        except Exception:
            return False

