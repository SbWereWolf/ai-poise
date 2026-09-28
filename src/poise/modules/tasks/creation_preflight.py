"""Pure immutable-method input contract and Task-creation preflight rules."""
from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import json
from pathlib import PurePosixPath

from ..foundation.errors import DomainError
from ..foundation.paths import matches_allowed_path
from ..verification.domain import exact_keys
from ..workflow.domain import RouteDefinition


PYTEST_POSITIONAL_PATHS_V1 = {
    "runner": "pytest",
    "parser": "positional-paths",
    "version": 1,
}
UNITTEST_DISCOVER_START_DIRECTORY_V1 = {
    "runner": "unittest",
    "parser": "discover-start-directory",
    "version": 1,
}
PYTHON_INLINE_NO_PATH_ARGUMENTS_V1 = {
    "runner": "python",
    "parser": "inline-no-path-arguments",
    "version": 1,
}


def _path(value, method_id):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise DomainError(
            f"method {method_id}: invalid repository path {value!r}; исправьте path и повторите запрос"
        )
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or value in (".", "..") or ".." in parsed.parts or str(parsed) != value:
        raise DomainError(
            f"method {method_id}: invalid repository path {value!r}; исправьте path и повторите запрос"
        )
    return value


def _method_cwd(value, method_id):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise DomainError(
            f"method {method_id}: invalid cwd {value!r}; исправьте cwd и повторите запрос"
        )
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts or str(parsed) != value:
        raise DomainError(
            f"method {method_id}: invalid cwd {value!r}; исправьте cwd и повторите запрос"
        )
    return value


def _pytest_references(method, method_id):
    argv = method["argv"]
    module = next((i for i in range(len(argv) - 1) if argv[i:i + 2] == ["-m", "pytest"]), None)
    if module is not None:
        arguments = argv[module + 2:]
    elif PurePosixPath(argv[0]).name in ("pytest", "py.test"):
        arguments = argv[1:]
    else:
        raise DomainError(
            f"method {method_id}: reference profile pytest does not match argv; исправьте reference_profile"
        )
    references = []
    positional = False
    option_value = False
    value_options = frozenset({
        "-c", "-k", "-m", "--basetemp", "--confcutdir", "--deselect",
        "--ignore", "--ignore-glob", "--maxfail", "--override-ini", "--rootdir", "--tb",
    })
    for argument in arguments:
        if option_value:
            option_value = False
            continue
        if not positional and argument == "--":
            positional = True
            continue
        if not positional and argument.startswith("-"):
            option_value = argument in value_options
            continue
        reference = argument.split("::", 1)[0]
        references.append(_path(reference, method_id))
    # Several callable selectors may name the same repository input.
    return tuple(dict.fromkeys(references))


def _unittest_discover_references(method, method_id):
    argv = method["argv"]
    module = next(
        (
            index
            for index in range(len(argv) - 2)
            if argv[index:index + 3] == ["-m", "unittest", "discover"]
        ),
        None,
    )
    if module is None:
        raise DomainError(
            f"method {method_id}: reference profile unittest does not match argv; "
            "исправьте reference_profile"
        )
    arguments = argv[module + 3:]
    starts = [
        arguments[index + 1]
        for index, argument in enumerate(arguments[:-1])
        if argument in ("-s", "--start-directory")
    ]
    if len(starts) != 1:
        raise DomainError(
            f"method {method_id}: unittest discover requires one start directory; "
            "исправьте argv или reference_profile"
        )
    return (_path(starts[0], method_id),)


def _python_inline_references(method, method_id):
    argv = method["argv"]
    if "-c" not in argv:
        inline = None
    else:
        inline = argv.index("-c")
    if inline is None or inline + 2 != len(argv):
        raise DomainError(
            f"method {method_id}: inline Python reference profile requires no path arguments; "
            "исправьте reference_profile"
        )
    return ()


REFERENCE_PROFILES = {
    tuple(PYTEST_POSITIONAL_PATHS_V1.values()): _pytest_references,
    tuple(UNITTEST_DISCOVER_START_DIRECTORY_V1.values()): _unittest_discover_references,
    tuple(PYTHON_INLINE_NO_PATH_ARGUMENTS_V1.values()): _python_inline_references,
}


@dataclass(frozen=True)
class FutureOutput:
    path: str
    producer_stage: str


@dataclass(frozen=True)
class MethodInputs:
    method_id: str
    repository_inputs: tuple[str, ...]
    future_outputs: tuple[FutureOutput, ...]
    references: tuple[str, ...]

    @classmethod
    def parse(cls, raw, method):
        method_id = method["id"]
        _method_cwd(method["cwd"], method_id)
        exact_keys(
            raw,
            {"method_id", "repository_inputs", "future_outputs", "reference_profile"},
            f"method_inputs for {method_id}",
        )
        if raw["method_id"] != method_id:
            raise DomainError(
                f"method_inputs binding mismatch: {raw['method_id']!r} does not bind method {method_id}; "
                "исправьте binding"
            )
        baseline = raw["repository_inputs"]
        future = raw["future_outputs"]
        if not isinstance(baseline, list) or not isinstance(future, list):
            raise DomainError(f"method {method_id}: repository_inputs/future_outputs must be explicit lists; declare both")
        baseline = tuple(_path(value, method_id) for value in baseline)
        outputs = []
        for value in future:
            exact_keys(value, {"path", "producer_stage"}, f"future output for {method_id}")
            if not isinstance(value["producer_stage"], str) or not value["producer_stage"]:
                raise DomainError(f"method {method_id}: future output producer_stage is required; исправьте declaration")
            outputs.append(FutureOutput(_path(value["path"], method_id), value["producer_stage"]))
        future_paths = tuple(output.path for output in outputs)
        combined = baseline + future_paths
        if len(combined) != len(set(combined)):
            conflict = next(path for path in combined if combined.count(path) > 1)
            kind = "baseline/future classification conflict" if conflict in baseline and conflict in future_paths else "duplicate path"
            raise DomainError(f"method {method_id}: {kind} for {conflict}; исправьте declaration")
        profile = raw["reference_profile"]
        exact_keys(profile, {"runner", "parser", "version"}, f"reference profile for {method_id}")
        profile_key = (profile["runner"], profile["parser"], profile["version"])
        parser = REFERENCE_PROFILES.get(profile_key)
        if parser is None:
            raise DomainError(
                f"method {method_id}: unsupported reference_profile {profile!r}; "
                "укажите поддержанный versioned profile"
            )
        references = parser(method, method_id)
        if set(references) != set(combined) or len(references) != len(combined):
            missing = sorted(set(references) - set(combined))
            extra = sorted(set(combined) - set(references))
            raise DomainError(
                f"method {method_id}: reference declaration mismatch; undeclared={missing}, unreferenced={extra}; "
                "исправьте repository_inputs/future_outputs"
            )
        return cls(method_id, baseline, tuple(outputs), references)


@dataclass(frozen=True)
class CreationPreflight:
    methods: tuple[MethodInputs, ...]

    @classmethod
    def parse(cls, contract, process):
        methods = contract["methods"]
        declared = contract["method_inputs"]
        if not isinstance(declared, list):
            raise DomainError("method_inputs must be an explicit list; declare one entry for every method")
        method_ids = [method["id"] for method in methods]
        declared_ids = [value.get("method_id") for value in declared if isinstance(value, dict)]
        if (len(declared_ids) != len(declared) or len(declared) != len(methods)
                or any(not isinstance(value,str) for value in declared_ids)
                or set(declared_ids) != set(method_ids) or len(set(declared_ids)) != len(declared_ids)):
            raise DomainError(
                f"method_inputs binding mismatch: declared={declared_ids}, methods={method_ids}; исправьте binding"
            )
        by_id = {value["method_id"]: value for value in declared}
        parsed = tuple(MethodInputs.parse(by_id[method["id"]], method) for method in methods)
        route = RouteDefinition.from_process(process).with_stage_scopes(contract["stage_contracts"])
        stages = {node.stage_id: node for node in route.nodes}
        for inputs in parsed:
            execution_stages = tuple(
                stage["id"] for stage in process["stages"]
                if inputs.method_id in contract["checks"][stage["id"]]
            )
            for output in inputs.future_outputs:
                if output.producer_stage not in stages:
                    raise DomainError(
                        f"method {inputs.method_id}: path {output.path} has unknown producer {output.producer_stage}; "
                        "укажите существующий producer_stage"
                    )
                if not any(
                    matches_allowed_path(output.path, pattern)
                    for pattern in stages[output.producer_stage].allowed_paths
                ):
                    raise DomainError(
                        f"method {inputs.method_id}: path {output.path} is outside allowed_paths of producer "
                        f"{output.producer_stage}; исправьте allowed_paths или declaration"
                    )
                if not execution_stages or any(
                    not _dominates(route, output.producer_stage, execution_stage)
                    for execution_stage in execution_stages
                ):
                    raise DomainError(
                        f"method {inputs.method_id}: producer {output.producer_stage} for path {output.path} "
                        "does not precede every execution stage; исправьте route или producer_stage"
                    )
        return cls(parsed)

    @property
    def repository_inputs(self):
        return tuple(path for method in self.methods for path in method.repository_inputs)

    def validate_base(self, existing_paths):
        existing = set(existing_paths)
        for method in self.methods:
            for path in method.repository_inputs:
                if path not in existing:
                    raise DomainError(
                        f"method {method.method_id}: path {path} отсутствует в base tree; "
                        "исправьте repository_inputs или используйте новый request_id"
                    )

    def validate_immutable_artifact_writes(self, route, policy, stage_contracts):
        """Reject planned writes that occur after an ordinary stage artifact is frozen."""
        active_ids = {
            requirement_id
            for contract in stage_contracts.items
            for requirement_id in contract.entry_requirements + contract.exit_requirements
        }
        immutable_artifacts = []
        planned_writes = [
            (output.path, output.producer_stage, f"method {method.method_id}")
            for method in self.methods
            for output in method.future_outputs
        ]
        for requirement in policy.requirements:
            if (
                requirement.id not in active_ids
                or requirement.kind != "artifact"
            ):
                continue
            details = json.loads(requirement.details)
            if details["maximum"] == 0:
                continue
            source = details.get("source")
            kind = "preexisting" if source is None else source.get("kind")
            if kind == "stage_output":
                if requirement.phase != "post":
                    continue
                freeze_stage = source["producer_stage"]
                freeze_timing = "after_stage"
                planned_writes.append((
                    details["pattern"],
                    freeze_stage,
                    f"artifact requirement {requirement.id}",
                ))
            elif kind == "declared_arrival":
                freeze_stage = source["arrival_stage"]
                freeze_timing = "at_stage_entry"
            elif kind == "preexisting":
                freeze_stage = None
                freeze_timing = "before_task"
            else:
                continue
            immutable_artifacts.append((
                details["pattern"],
                freeze_stage,
                freeze_timing,
                requirement.id,
            ))
        for write_path, writer, write_owner in planned_writes:
            for pattern, freeze_stage, freeze_timing, requirement_id in immutable_artifacts:
                if not _artifact_paths_overlap(write_path, pattern):
                    continue
                if freeze_timing == "before_task":
                    later = True
                elif freeze_timing == "at_stage_entry":
                    later = writer == freeze_stage or _reachable_after(route, freeze_stage, writer)
                else:
                    later = writer != freeze_stage and _reachable_after(route, freeze_stage, writer)
                if later:
                    raise DomainError(
                        f"{write_owner}: output {write_path} at {writer} rewrites immutable "
                        f"artifact {requirement_id}; remove the later write or use the "
                        "explicit editable artifact-draft lifecycle"
                    )


def _dominates(route, producer, execution):
    if producer == execution or producer == route.entry:
        return True
    reached = set()
    pending = [route.entry]
    while pending:
        current = pending.pop()
        if current == producer or current in reached:
            continue
        reached.add(current)
        pending.extend(target for _, target in route.node(current).transitions if target is not None)
    return execution not in reached


def _reachable_after(route, start, target):
    reached = {start}
    node = route.node(start)
    pending = [value for _, value in node.transitions if value is not None]
    pending.extend(node.rework_targets)
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in reached:
            continue
        reached.add(current)
        node = route.node(current)
        pending.extend(value for _, value in node.transitions if value is not None)
        pending.extend(node.rework_targets)
    return False


def _artifact_paths_overlap(left, right):
    return (
        left == right
        or matches_allowed_path(left, right)
        or matches_allowed_path(right, left)
        or _glob_languages_overlap(left, right)
    )


def _glob_languages_overlap(left, right):
    left_tokens = _glob_tokens(left)
    right_tokens = _glob_tokens(right)
    candidates = _glob_candidate_characters(left, right)
    pending = [(0, 0)]
    reached = set()
    while pending:
        left_index, right_index = pending.pop()
        state = (left_index, right_index)
        if state in reached:
            continue
        reached.add(state)
        if left_index == len(left_tokens) and right_index == len(right_tokens):
            return True
        left_token = left_tokens[left_index] if left_index < len(left_tokens) else None
        right_token = right_tokens[right_index] if right_index < len(right_tokens) else None
        if left_token == "*":
            pending.append((left_index + 1, right_index))
        if right_token == "*":
            pending.append((left_index, right_index + 1))
        if (
            left_token is not None
            and right_token is not None
            and _glob_tokens_share_character(left_token, right_token, candidates)
        ):
            consumed = (
                left_index if left_token == "*" else left_index + 1,
                right_index if right_token == "*" else right_index + 1,
            )
            if consumed != state:
                pending.append(consumed)
    return False


def _glob_candidate_characters(left, right):
    """One representative for every Unicode interval where either glob can change."""
    codepoints = {1, 0x10FFFF}
    for character in left + right:
        point = ord(character)
        codepoints.update(
            candidate
            for candidate in (point - 1, point, point + 1)
            if 0 < candidate <= 0x10FFFF and candidate != ord("\\")
        )
    return tuple(chr(point) for point in sorted(codepoints))


def _glob_tokens(pattern):
    tokens = []
    index = 0
    while index < len(pattern):
        current = pattern[index]
        if current == "*":
            while index < len(pattern) and pattern[index] == "*":
                index += 1
            tokens.append("*")
            continue
        if current == "?":
            tokens.append("?")
            index += 1
            continue
        if current == "[":
            end = index + 1
            if end < len(pattern) and pattern[end] == "!":
                end += 1
            if end < len(pattern) and pattern[end] == "]":
                end += 1
            end = pattern.find("]", end)
            if end >= 0:
                tokens.append(pattern[index:end + 1])
                index = end + 1
                continue
        tokens.append(current)
        index += 1
    return tuple(tokens)


def _glob_tokens_share_character(left, right, candidates):
    return any(
        _glob_token_matches(left, candidate)
        and _glob_token_matches(right, candidate)
        for candidate in candidates
    )


def _glob_token_matches(token, candidate):
    if token in ("*", "?"):
        return True
    if token.startswith("["):
        return fnmatch.fnmatchcase(candidate, token)
    return token == candidate
