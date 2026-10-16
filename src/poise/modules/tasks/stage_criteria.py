"""Task-owned immutable criteria revisions and execution bindings. No I/O."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json

from ..foundation.errors import DomainError
from ..goal_config.domain import canonical, fingerprint
from ..workflow.domain import StagePurpose, exact, text


def text_list(raw, where):
    if (not isinstance(raw, list)
            or any(not isinstance(item, str) or not item.strip() for item in raw)
            or len(raw) != len(set(raw))):
        raise DomainError(f"{where}: unique nonempty text list required")
    return tuple(raw)


def _sha256(value, where):
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in '0123456789abcdef' for char in value)):
        raise DomainError(f'{where}: SHA-256 identity required')


def _source(source):
    if not isinstance(source, dict):
        raise DomainError('Stored criteria requires explicit provenance')
    fields = {
        'initial': {'contract_digest', 'requirements_snapshot_digest'},
        'proposal': {'proposal_id', 'proposal_digest', 'decision_id'},
        'historical': {'original_digest', 'migration_receipt'},
    }
    kind = source.get('kind')
    if kind not in fields:
        raise DomainError('Unknown stored criteria source kind')
    exact(source, fields[kind] | {'kind'}, 'stored criteria source')
    for name in fields[kind]:
        _sha256(source[name], 'stored criteria source.' + name)


def _revision(record):
    if type(record['revision']) is not int or record['revision'] < 0:
        raise DomainError('Invalid stored criteria revision')
    StageCriteria.parse(record['criteria'])
    text_list(record['entry_requirements'], 'stored entry requirements')
    text_list(record['exit_requirements'], 'stored exit requirements')
    _source(record['source'])


@dataclass(frozen=True)
class StageCriteria:
    requirements: tuple[str, ...]
    definition_of_ready: tuple[str, ...]
    definition_of_done: tuple[str, ...]

    @classmethod
    def parse(cls, raw):
        fields = ("requirements", "definition_of_ready", "definition_of_done")
        exact(raw, fields, "criteria")
        return cls(*(text_list(raw[field], f"criteria.{field}") for field in fields))

    def to_dict(self):
        return {name: list(getattr(self, name)) for name in
                ("requirements", "definition_of_ready", "definition_of_done")}


def split_work(raw):
    if not isinstance(raw, dict):
        raise DomainError("stage_work requires an object")
    ordinary = {key: value for key, value in raw.items() if key != "criteria_work"}
    return ordinary, CriteriaWork.parse(raw["criteria_work"]) if "criteria_work" in raw else None


@dataclass(frozen=True)
class CriteriaWork:
    document: str

    @property
    def data(self):
        return json.loads(self.document)

    @classmethod
    def parse(cls, raw):
        exact(raw, {"request_id", "expected_version", "proposals", "decisions"}, "criteria_work")
        text(raw["request_id"], "criteria_work.request_id")
        if type(raw["expected_version"]) is not int or raw["expected_version"] < 0:
            raise DomainError("criteria_work expected_version requires nonnegative integer")
        if (not isinstance(raw["proposals"], list) or not isinstance(raw["decisions"], list)
                or bool(raw["proposals"]) == bool(raw["decisions"])):
            raise DomainError("criteria_work requires exactly one nonempty operation list")
        targets = []
        for item in raw["proposals"]:
            exact(item, {"stage_id", "expected_criteria_revision", "criteria",
                         "entry_requirements", "exit_requirements"}, "criteria proposal")
            text(item["stage_id"], "proposal.stage_id")
            if type(item["expected_criteria_revision"]) is not int or item["expected_criteria_revision"] < 0:
                raise DomainError("proposal requires nonnegative criteria revision")
            StageCriteria.parse(item["criteria"])
            text_list(item["entry_requirements"], "proposal.entry_requirements")
            text_list(item["exit_requirements"], "proposal.exit_requirements")
            targets.append(item["stage_id"])
        if len(targets) != len(set(targets)):
            raise DomainError("duplicate_proposal_target")
        ids = []
        for item in raw["decisions"]:
            exact(item, {"proposal_id", "proposal_digest", "decision", "reason"}, "criteria decision")
            for field in ("proposal_id", "proposal_digest", "reason"):
                text(item[field], f"decision.{field}")
            if item["decision"] not in ("accepted", "rejected"):
                raise DomainError("criteria decision requires accepted or rejected")
            ids.append(item["proposal_id"])
        if len(ids) != len(set(ids)):
            raise DomainError("duplicate_proposal_decision")
        try:
            return cls(canonical(raw))
        except (ValueError, UnicodeError, TypeError) as exc:
            raise DomainError("criteria_work cannot be encoded") from exc


@dataclass(frozen=True)
class StageCriteriaBook:
    document: str

    @property
    def data(self):
        return json.loads(self.document)

    @classmethod
    def from_dict(cls, raw):
        exact(raw, {"stages", "requests"}, "stored stage criteria")
        if not isinstance(raw["stages"], dict) or not isinstance(raw["requests"], dict):
            raise DomainError("stored stage criteria requires explicit maps")
        for stage_id, current in raw["stages"].items():
            text(stage_id, "stored stage identity")
            exact(current, {"purpose", "revision", "criteria", "entry_requirements",
                            "exit_requirements", "source", "execution", "proposals",
                            "history", "revisions"}, "stored stage criteria entry")
            try:
                StagePurpose(current["purpose"])
            except (TypeError, ValueError) as exc:
                raise DomainError("Unknown stored stage purpose") from exc
            _revision(current)
            execution = current["execution"]
            exact(execution, {"state", "epoch", "bound_revision"}, "stored execution binding")
            bound = execution["bound_revision"]
            if (execution["state"] not in ("unstarted", "started", "unknown")
                    or type(execution["epoch"]) is not int or execution["epoch"] < 0
                    or (bound is not None and (type(bound) is not int or bound < 0))
                    or (execution["state"] == "unstarted") != (bound is None)
                    or (bound is not None and bound > current["revision"])):
                raise DomainError("Invalid stored execution binding")
            for field in ("proposals", "history", "revisions"):
                if (not isinstance(current[field], list)
                        or any(not isinstance(item, dict) for item in current[field])):
                    raise DomainError(f"Stored criteria {field} requires records")
            if [item.get('revision') for item in current['revisions']] != list(range(current['revision'])):
                raise DomainError('Stored criteria revision history is incomplete')
            for item in current['revisions']:
                _revision(item)
            if bound is not None and bound != current['revision']:
                raise DomainError('Bound criteria cannot have an unbound newer revision')
            identities = []
            for proposal in current['proposals']:
                exact(proposal, {'stage_id', 'expected_criteria_revision', 'criteria',
                    'entry_requirements', 'exit_requirements', 'producer_stage',
                    'producer_iteration', 'producer_submission', 'proposal_id',
                    'proposal_digest', 'decision'}, 'stored criteria proposal')
                if proposal['stage_id'] != stage_id or proposal['producer_stage'] not in raw['stages']:
                    raise DomainError('Stored proposal stage membership mismatch')
                body = {key: proposal[key] for key in ('stage_id', 'expected_criteria_revision',
                    'criteria', 'entry_requirements', 'exit_requirements', 'producer_stage',
                    'producer_iteration', 'producer_submission')}
                if (proposal['proposal_digest'] != fingerprint(body)
                        or type(proposal['expected_criteria_revision']) is not int
                        or not 0 <= proposal['expected_criteria_revision'] <= current['revision']
                        or any(type(proposal[key]) is not int or proposal[key] < 1
                               for key in ('producer_iteration', 'producer_submission'))):
                    raise DomainError('Stored proposal provenance mismatch')
                StageCriteria.parse(proposal['criteria'])
                text_list(proposal['entry_requirements'], 'stored proposal entry requirements')
                text_list(proposal['exit_requirements'], 'stored proposal exit requirements')
                _sha256(proposal['proposal_id'], 'proposal_id')
                identities.append(proposal['proposal_id'])
                decision = proposal['decision']
                if decision is not None:
                    exact(decision, {'proposal_id', 'proposal_digest', 'decision', 'reason',
                        'decision_id', 'producer_stage', 'producer_iteration', 'producer_submission'},
                        'stored criteria decision')
                    if (decision['proposal_id'] != proposal['proposal_id']
                            or decision['proposal_digest'] != proposal['proposal_digest']
                            or decision['decision'] not in ('accepted', 'rejected')
                            or decision['producer_stage'] not in raw['stages']
                            or any(type(decision[key]) is not int or decision[key] < 1
                                   for key in ('producer_iteration', 'producer_submission'))):
                        raise DomainError('Stored decision provenance mismatch')
                    text(decision['reason'], 'decision.reason')
                    _sha256(decision['decision_id'], 'decision_id')
            if len(identities) != len(set(identities)):
                raise DomainError('Duplicate stored criteria proposal identity')
            history_ids = []
            for event in current['history']:
                exact(event, {'id', 'action', 'actor', 'stage', 'iteration', 'old_revision',
                              'new_revision', 'reason', 'source'}, 'stored criteria history')
                _sha256(event['id'], 'criteria history identity')
                _source(event['source'])
                text(event['actor'], 'criteria history actor')
                text(event['reason'], 'criteria history reason')
                if (event['stage'] not in raw['stages']
                        or type(event['iteration']) is not int or event['iteration'] < 1
                        or event['action'] not in ('accepted', 'rejected', 'restart', 'execution_started')
                        or any(type(event[key]) is not int or not 0 <= event[key] <= current['revision']
                               for key in ('old_revision', 'new_revision'))):
                    raise DomainError('Stored criteria history mismatch')
                history_ids.append(event['id'])
            if len(history_ids) != len(set(history_ids)):
                raise DomainError('Duplicate stored criteria history identity')
            for record in [*current['revisions'], current]:
                source = record['source']
                if source['kind'] != 'proposal':
                    continue
                matches = [p for p in current['proposals'] if p['proposal_id'] == source['proposal_id']]
                if len(matches) != 1:
                    raise DomainError('Criteria agreement source is absent')
                proposal = matches[0]
                decision = proposal['decision']
                if (proposal['proposal_digest'] != source['proposal_digest'] or decision is None
                        or decision['decision'] != 'accepted' or decision['decision_id'] != source['decision_id']
                        or record['revision'] != proposal['expected_criteria_revision'] + 1
                        or any(record[key] != proposal[key] for key in
                               ('criteria', 'entry_requirements', 'exit_requirements'))
                        or not any(event['id'] == source['decision_id'] and event['action'] == 'accepted'
                                   and event['new_revision'] == record['revision'] for event in current['history'])):
                    raise DomainError('Criteria agreement provenance is inconsistent')
        for request_id, document in raw["requests"].items():
            text(request_id, "stored criteria request identity")
            if not isinstance(document, str):
                raise DomainError("Stored criteria request requires canonical text")
            parsed = CriteriaWork.parse(json.loads(document))
            if parsed.document != document or parsed.data['request_id'] != request_id:
                raise DomainError("Stored criteria request identity mismatch")
        return cls(canonical(raw))

    @staticmethod
    def _validate_proposal_contract(task, target, item):
        original = task.stage_contracts.stage(target)
        from .domain import TaskStageContract, TaskStageContracts
        replacement = TaskStageContract(target, original.allowed_paths,
            tuple(item['entry_requirements']), tuple(item['exit_requirements']),
            StageCriteria.parse(item['criteria']))
        TaskStageContracts.parse(task.stage_contracts.replace(target, replacement).to_list(),
                                 task.route, task.content_policy)

    @classmethod
    def initialize_initial(cls, route, contracts, contract, requirements_snapshot):
        if not isinstance(requirements_snapshot, dict):
            raise DomainError("Initial criteria requires a genuine Requirements snapshot")
        source = {"kind": "initial", "contract_digest": fingerprint(contract),
                  "requirements_snapshot_digest": fingerprint(requirements_snapshot)}
        stages = {}
        for item in contracts.items:
            stages[item.stage_id] = {
                "purpose": route.node(item.stage_id).purpose.value,
                "revision": 0, "criteria": item.criteria.to_dict(),
                "entry_requirements": list(item.entry_requirements),
                "exit_requirements": list(item.exit_requirements),
                "source": deepcopy(source),
                "execution": {"state": "unstarted", "epoch": 0, "bound_revision": None},
                "proposals": [], "history": [], "revisions": [],
            }
        return cls.from_dict({"stages": stages, "requests": {}})

    def project_stage(self, task_id, stage_id):
        stages = self.data["stages"]
        if stage_id not in stages:
            raise DomainError("Unknown stage criteria target")
        return {"task": task_id, "stage_id": stage_id,
                **{key: value for key, value in stages[stage_id].items() if key != "revisions"}}

    def validate_work(self, work, task, *, entry_version=True):
        value = work.data
        if entry_version and value["expected_version"] != task.state.version:
            raise DomainError("criteria_work task version conflict")
        data = self.data
        if value["request_id"] in data["requests"]:
            raise DomainError("criteria_work request identity already used")
        node = task.route.node(task.stage.stage_id)
        targets = []
        for item in value["proposals"]:
            target = item["stage_id"]
            if (node.purpose not in (StagePurpose.DESIGN, StagePurpose.OTHER)
                    or target not in node.criteria_authority.propose):
                raise DomainError("criteria_authority_denied")
            current = data["stages"][target]
            if current["revision"] != item["expected_criteria_revision"]:
                raise DomainError("criteria revision conflict")
            self._require_future(current)
            # Validate gate references and full graph invariants through their owner.
            self._validate_proposal_contract(task, target, item)
        for item in value["decisions"]:
            target, proposal = self._proposal(data, item)
            targets.append(target)
            if (node.purpose not in (StagePurpose.OBSERVATION, StagePurpose.OTHER)
                    or target not in node.criteria_authority.decide):
                raise DomainError("criteria_authority_denied")
            current = data["stages"][target]
            self._require_future(current)
            if (proposal["decision"] is not None
                    or proposal["expected_criteria_revision"] != current["revision"]):
                raise DomainError("criteria revision conflict")
            self._validate_proposal_contract(task, target, proposal)
        if len(targets) != len(set(targets)):
            raise DomainError("overlapping_decision_targets")

    @staticmethod
    def _require_future(current):
        if current["execution"]["state"] != "unstarted":
            raise DomainError("target_started_requires_restart")

    @staticmethod
    def _proposal(data, decision):
        for target, current in data["stages"].items():
            for proposal in current["proposals"]:
                if proposal["proposal_id"] == decision["proposal_id"]:
                    if proposal["proposal_digest"] != decision["proposal_digest"]:
                        raise DomainError("proposal_identity_mismatch")
                    return target, proposal
        raise DomainError("proposal_identity_mismatch")

    def apply_verified_work(self, work, task, actor, submission_id):
        self.validate_work(work, task, entry_version=False)
        value = work.data
        data = self.data
        producer = {"producer_stage": task.stage.stage_id,
                    "producer_iteration": task.state.iteration,
                    "producer_submission": submission_id}
        for item in value["proposals"]:
            identity = fingerprint({"request_id": value["request_id"], **item, **producer})
            proposed = {**item, **producer, "proposal_id": identity,
                        "proposal_digest": fingerprint({**item, **producer}), "decision": None}
            data["stages"][item["stage_id"]]["proposals"].append(proposed)
        for item in value["decisions"]:
            target, proposal = self._proposal(data, item)
            current = data["stages"][target]
            old = current["revision"]
            decision_id = fingerprint({"request_id": value["request_id"], **item, **producer})
            proposal["decision"] = {**item, "decision_id": decision_id, **producer}
            source = {"kind": "proposal", "proposal_id": proposal["proposal_id"],
                      "proposal_digest": proposal["proposal_digest"], "decision_id": decision_id}
            if item["decision"] == "accepted":
                current["revisions"].append({key: deepcopy(current[key]) for key in
                    ("revision", "criteria", "entry_requirements", "exit_requirements", "source")})
                current.update(revision=old + 1, source=source,
                    **{key: deepcopy(proposal[key]) for key in
                       ("criteria", "entry_requirements", "exit_requirements")})
            current["history"].append({"id": decision_id, "action": item["decision"],
                "actor": actor, "stage": task.stage.stage_id, "iteration": task.state.iteration,
                "old_revision": old, "new_revision": current["revision"],
                "reason": item["reason"], "source": source})
        data["requests"][value["request_id"]] = work.document
        return self.from_dict(data)

    def admit_execution(self, stage_id, actor, iteration):
        data = self.data
        current = data["stages"][stage_id]
        if current["purpose"] != "execution" or current["execution"]["state"] != "unstarted":
            return self
        current["execution"].update(state="started", bound_revision=current["revision"])
        identity = fingerprint({"stage": stage_id, "epoch": current["execution"]["epoch"],
                                "revision": current["revision"], "actor": actor})
        current["history"].append({"id": identity, "action": "execution_started",
            "actor": actor, "stage": stage_id, "iteration": iteration,
            "old_revision": current["revision"], "new_revision": current["revision"],
            "reason": "Actual execution admission", "source": deepcopy(current["source"])})
        return self.from_dict(data)

    def effective_contracts(self, contracts):
        from .domain import TaskStageContract, TaskStageContracts
        stages = self.data["stages"]
        return TaskStageContracts(tuple(TaskStageContract(item.stage_id, item.allowed_paths,
            tuple(stages[item.stage_id]["entry_requirements"]),
            tuple(stages[item.stage_id]["exit_requirements"]),
            StageCriteria.parse(stages[item.stage_id]["criteria"])) for item in contracts.items))

    def revise_after_restart(self, route, contracts, audit, actor, contract, requirements_snapshot):
        """Reopen only explicitly revised targets; retain all other bindings."""
        data = self.data
        if set(data["stages"]) != {item.stage_id for item in contracts.items}:
            raise DomainError("Restart cannot silently replace criteria stage identities")
        for item in contracts.items:
            current = data["stages"][item.stage_id]
            if current["purpose"] != route.node(item.stage_id).purpose.value:
                raise DomainError("Stage purpose change requires explicit workflow migration")
            revised = {"criteria": item.criteria.to_dict(),
                       "entry_requirements": list(item.entry_requirements),
                       "exit_requirements": list(item.exit_requirements)}
            if all(current[key] == value for key, value in revised.items()):
                continue
            old = current["revision"]
            current["revisions"].append({key: deepcopy(current[key]) for key in
                ("revision", "criteria", "entry_requirements", "exit_requirements", "source", "execution")})
            identity = fingerprint({"stage": item.stage_id, "audit": audit,
                                    "old_revision": old, "criteria": revised})
            source = {"kind": "initial", "contract_digest": fingerprint(contract),
                      "requirements_snapshot_digest": fingerprint(requirements_snapshot)}
            current.update(revised, revision=old + 1, source=source,
                execution={"state": "unstarted", "epoch": current["execution"]["epoch"] + 1,
                           "bound_revision": None})
            current["history"].append({"id": identity, "action": "restart",
                "actor": actor, "stage": audit['from_stage'], "iteration": audit['from_iteration'],
                "old_revision": old, "new_revision": old + 1,
                "reason": audit["reason"], "source": source})
        return self.from_dict(data)
