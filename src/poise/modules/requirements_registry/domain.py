from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from ..foundation.errors import DomainError


LEVELS = frozenset(("system", "application"))
STATUSES = frozenset(("current", "future", "obsolete"))


def _exact(value, keys, where):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise DomainError(f"{where}: required exact fields {sorted(keys)}")


def _identifier(value, where):
    if (
        not isinstance(value, str)
        or not value.strip()
        or value in (".", "..")
        or any(character in value for character in ("/", "\\", "\x00"))
    ):
        raise DomainError(f"{where}: required one nonempty identifier")
    return value


def _requirement(value):
    _exact(value, {"id", "level", "status", "text"}, "requirement")
    result = deepcopy(value)
    _identifier(result["id"], "requirement id")
    if result["level"] not in LEVELS:
        raise DomainError("requirement level must be system or application")
    if result["status"] not in STATUSES:
        raise DomainError("requirement status must be current, future or obsolete")
    if not isinstance(result["text"], str) or not result["text"].strip():
        raise DomainError("requirement text must be nonempty")
    return result


@dataclass(frozen=True)
class RequirementsRegistry:
    _requirements: dict
    _links: frozenset

    @classmethod
    def empty(cls):
        return cls({}, frozenset())

    @classmethod
    def restore(cls, requirements, links):
        if not requirements:
            if links:
                raise DomainError("Requirements links cannot exist without requirements")
            return cls.empty()
        registry = cls.empty().apply(
            [
                {"kind": "put_requirement", "requirement": requirement}
                for requirement in requirements
            ],
            max_items=max(1, len(requirements)),
        )
        if not links:
            return registry
        return registry.apply(
            [
                {
                    "kind": "link",
                    "system": link["system"],
                    "application": link["application"],
                }
                for link in links
            ],
            max_items=len(links),
        )

    def apply(self, operations, max_items):
        if type(max_items) is not int or max_items <= 0:
            raise DomainError("Requirements batch max_items must be a positive integer")
        if not isinstance(operations, list) or not operations or len(operations) > max_items:
            raise DomainError("Requirements batch must be bounded and nonempty")
        requirements = deepcopy(self._requirements)
        links = set(self._links)
        for operation in operations:
            if not isinstance(operation, dict):
                raise DomainError("Requirements operation must be an object")
            kind = operation.get("kind")
            if kind == "put_requirement":
                _exact(operation, {"kind", "requirement"}, "put_requirement")
                requirement = _requirement(operation["requirement"])
                previous = requirements.get(requirement["id"])
                if previous is not None and previous["level"] != requirement["level"]:
                    raise DomainError("requirement level is immutable")
                requirements[requirement["id"]] = requirement
            elif kind == "link":
                _exact(operation, {"kind", "system", "application"}, "link")
                system = _identifier(operation["system"], "system link")
                application = _identifier(operation["application"], "application link")
                if system not in requirements or requirements[system]["level"] != "system":
                    raise DomainError(f"unknown system requirement in link: {system}")
                if application not in requirements or requirements[application]["level"] != "application":
                    raise DomainError(f"unknown application requirement in link: {application}")
                links.add((system, application))
            else:
                raise DomainError(f"unknown Requirements operation: {kind}")
        return RequirementsRegistry(requirements, frozenset(links))

    def requirement(self, identifier):
        _identifier(identifier, "requirement id")
        if identifier not in self._requirements:
            raise DomainError(f"unknown requirement: {identifier}")
        return deepcopy(self._requirements[identifier])

    @staticmethod
    def _included(requirement, mode):
        if mode not in (None, "analysis", "current"):
            raise DomainError("coverage mode must be analysis or current")
        if mode == "current":
            return requirement["status"] == "current"
        return requirement["status"] != "obsolete"

    def coverage(self, mode=None):
        included = {
            identifier
            for identifier, requirement in self._requirements.items()
            if self._included(requirement, mode)
        }
        linked_systems = {
            system for system, application in self._links
            if system in included and application in included
        }
        linked_applications = {
            application for system, application in self._links
            if system in included and application in included
        }
        applications = sorted(
            identifier for identifier in included
            if self._requirements[identifier]["level"] == "application"
            and identifier not in linked_applications
        )
        systems = sorted(
            identifier for identifier in included
            if self._requirements[identifier]["level"] == "system"
            and identifier not in linked_systems
        )
        return {
            "application_without_system": applications,
            "system_without_application": systems,
        }

    def chain(self, identifier):
        requirement = self.requirement(identifier)
        if requirement["level"] == "application":
            systems = [
                deepcopy(self._requirements[system])
                for system, application in sorted(self._links)
                if application == identifier
                and self._requirements[system]["status"] != "obsolete"
            ]
            return {"application": requirement, "systems": systems}
        applications = [
            deepcopy(self._requirements[application])
            for system, application in sorted(self._links)
            if system == identifier
            and self._requirements[application]["status"] != "obsolete"
        ]
        return {"system": requirement, "applications": applications}

    def gaps(self):
        coverage = self.coverage()
        return [
            {
                "kind": "application_without_system",
                "requirement_id": identifier,
                "text": self._requirements[identifier]["text"],
            }
            for identifier in coverage["application_without_system"]
        ] + [
            {
                "kind": "system_without_application",
                "requirement_id": identifier,
                "text": self._requirements[identifier]["text"],
            }
            for identifier in coverage["system_without_application"]
        ]

    def plan_task(self, task_requirements):
        if not isinstance(task_requirements, list) or not task_requirements:
            raise DomainError("Task requirements plan must be a nonempty list")
        gaps = []
        chains = []
        selected_requirements = {}
        selected_links = set()
        normalized_tasks = []
        for index, item in enumerate(task_requirements):
            _exact(item, {"text", "applications"}, f"task requirement {index}")
            text = item["text"]
            applications = item["applications"]
            if not isinstance(text, str) or not text.strip():
                raise DomainError("Task requirement text must be nonempty")
            if (
                not isinstance(applications, list)
                or any(not isinstance(value, str) or not value for value in applications)
                or len(applications) != len(set(applications))
            ):
                raise DomainError("Task applications must be unique requirement identifiers")
            normalized_tasks.append({"text": text, "applications": list(applications)})
            if not applications:
                gaps.append({
                    "kind": "missing_application",
                    "task_text": text,
                    "proposal": {"level": "application", "text": text},
                })
                continue
            chain_applications = []
            chain_system_ids = set()
            for application_id in applications:
                application = self._requirements.get(application_id)
                if application is None or application["level"] != "application":
                    gaps.append({
                        "kind": "missing_application",
                        "task_text": text,
                        "proposal": {"level": "application", "text": text},
                    })
                    continue
                if application["status"] == "obsolete":
                    raise DomainError(f"obsolete application requirement cannot plan a Task: {application_id}")
                chain_applications.append(deepcopy(application))
                selected_requirements[application_id] = deepcopy(application)
                systems = [
                    system for system, linked_application in self._links
                    if linked_application == application_id
                    and self._requirements[system]["status"] != "obsolete"
                ]
                if not systems:
                    gaps.append({
                        "kind": "missing_system",
                        "task_text": text,
                        "application_text": application["text"],
                        "proposal": {"level": "system", "text": application["text"]},
                    })
                for system_id in systems:
                    chain_system_ids.add(system_id)
                    selected_requirements[system_id] = deepcopy(self._requirements[system_id])
                    selected_links.add((system_id, application_id))
            chains.append({
                "task_text": text,
                "applications": chain_applications,
                "systems": [
                    deepcopy(self._requirements[system_id])
                    for system_id in sorted(chain_system_ids)
                ],
            })
        snapshot = {
            "requirements": {
                identifier: selected_requirements[identifier]
                for identifier in sorted(selected_requirements)
            },
            "links": [
                {"system": system, "application": application}
                for system, application in sorted(selected_links)
            ],
            "task_requirements": normalized_tasks,
        }
        return {
            "status": "ready" if not gaps else "gaps",
            "gaps": gaps,
            "chains": chains,
            "snapshot": snapshot,
        }

    def to_dict(self):
        return {
            "requirements": {
                identifier: deepcopy(self._requirements[identifier])
                for identifier in sorted(self._requirements)
            },
            "links": [
                {"system": system, "application": application}
                for system, application in sorted(self._links)
            ],
        }
