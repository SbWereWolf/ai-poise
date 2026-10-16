"""Independent registered-command profile contracts and atomic text output."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import tomllib
import textwrap
from typing import Iterable, Mapping, Sequence
import uuid


class ProfileError(ValueError):
    """The registered command-profile contract is invalid."""


_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")


_SUPPORTED_PLATFORMS = frozenset({"linux", "darwin", "windows"})


_SUPPORTED_INVOCATIONS = frozenset({"exec", "windows_cmd"})


_SUPPORTED_PARSERS = frozenset(
    {"generic", "phpunit", "vitest", "playwright", "phpstan", "deptrac", "build"}
)


_SUPPORTED_EVIDENCE_RETENTION_CLASSES = frozenset({"task", "release", "diagnostic"})


_INPUT_SELECTION_FILESYSTEM_GLOB = "filesystem-glob"


_INPUT_SELECTION_GIT_TRACKED_MARKDOWN = "git-tracked-markdown"


_SUPPORTED_INPUT_SELECTIONS = frozenset(
    {_INPUT_SELECTION_FILESYSTEM_GLOB, _INPUT_SELECTION_GIT_TRACKED_MARKDOWN}
)


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{label} must be a non-empty string")
    return value.strip()


def _timestamp_with_timezone(value: object, label: str) -> str:
    result = _required_string(value, label)
    normalized = result[:-1] + "+00:00" if result.endswith("Z") else result
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ProfileError(f"{label} must be an ISO-8601 timestamp with time and timezone") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProfileError(f"{label} must include a timezone")
    return result


def _identifier(value: object, label: str) -> str:
    result = _required_string(value, label)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ProfileError(f"{label} must match {_IDENTIFIER_RE.pattern}: {result!r}")
    return result


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProfileError(f"{label} must be a positive integer")
    return value


def _non_negative_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ProfileError(f"{label} must be a non-negative number")
    result = float(value)
    if not math.isfinite(result):
        raise ProfileError(f"{label} must be finite")
    return result


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ProfileError(f"{label} must be boolean")
    return value


def _string_list(value: object, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "" if allow_empty else " and non-empty"
        raise ProfileError(f"{label} must be a string list{qualifier}")
    return tuple(_required_string(item, f"{label}[{index}]") for index, item in enumerate(value))


def _relative_path(value: object, label: str) -> str:
    result = _required_string(value, label)
    path = Path(result)
    if path.is_absolute() or ".." in path.parts:
        raise ProfileError(f"{label} must remain inside the repository: {result!r}")
    return result.replace("\\", "/")


def _relative_glob(value: object, label: str) -> str:
    result = _required_string(value, label).replace("\\", "/")
    path = Path(result)
    if path.is_absolute() or ".." in path.parts or result.startswith("/"):
        raise ProfileError(f"{label} must remain inside the evidence directory: {result!r}")
    if result in {".", "./"} or result.endswith("/"):
        raise ProfileError(f"{label} must identify files, not a directory: {result!r}")
    return result


@dataclass(frozen=True)
class EvidenceContract:
    evidence_id: str
    required_globs: tuple[str, ...]
    optional_globs: tuple[str, ...]
    max_files: int
    max_total_bytes: int
    retention_class: str

    @classmethod
    def from_raw(cls, raw: object, index: int) -> "EvidenceContract":
        label = f"evidence_contracts[{index}]"
        if not isinstance(raw, Mapping):
            raise ProfileError(f"{label} must be a table")
        required = _string_list(
            raw.get("required_globs", []),
            f"{label}.required_globs",
            allow_empty=True,
        )
        optional = _string_list(
            raw.get("optional_globs", []),
            f"{label}.optional_globs",
            allow_empty=True,
        )
        required_globs = tuple(
            _relative_glob(item, f"{label}.required_globs[{position}]")
            for position, item in enumerate(required)
        )
        optional_globs = tuple(
            _relative_glob(item, f"{label}.optional_globs[{position}]")
            for position, item in enumerate(optional)
        )
        if len(set(required_globs)) != len(required_globs):
            raise ProfileError(f"{label}.required_globs must not contain duplicates")
        if len(set(optional_globs)) != len(optional_globs):
            raise ProfileError(f"{label}.optional_globs must not contain duplicates")
        overlap = set(required_globs) & set(optional_globs)
        if overlap:
            raise ProfileError(
                f"{label} globs cannot be both required and optional: {sorted(overlap)}"
            )
        retention_class = _required_string(
            raw.get("retention_class"), f"{label}.retention_class"
        )
        if retention_class not in _SUPPORTED_EVIDENCE_RETENTION_CLASSES:
            raise ProfileError(
                f"{label}.retention_class must be one of "
                f"{sorted(_SUPPORTED_EVIDENCE_RETENTION_CLASSES)}"
            )
        return cls(
            evidence_id=_identifier(raw.get("evidence_id"), f"{label}.evidence_id"),
            required_globs=required_globs,
            optional_globs=optional_globs,
            max_files=_positive_int(raw.get("max_files"), f"{label}.max_files"),
            max_total_bytes=_positive_int(
                raw.get("max_total_bytes"), f"{label}.max_total_bytes"
            ),
            retention_class=retention_class,
        )


@dataclass(frozen=True)
class ProgressProfile:
    mode: str
    pattern: str | None = None
    completed_group: str | None = None
    total_group: str | None = None
    stage_group: str | None = None

    @classmethod
    def from_raw(cls, raw: object, label: str) -> "ProgressProfile":
        if not isinstance(raw, Mapping):
            raise ProfileError(f"{label} must be an inline table")
        mode = _required_string(raw.get("mode"), f"{label}.mode")
        if mode == "unavailable":
            if any(key in raw for key in ("pattern", "completed_group", "total_group", "stage_group")):
                raise ProfileError(f"{label} mode unavailable accepts only the mode key")
            return cls(mode=mode)
        if mode != "regex":
            raise ProfileError(f"{label}.mode must be unavailable or regex")
        pattern = _required_string(raw.get("pattern"), f"{label}.pattern")
        completed_group = _required_string(raw.get("completed_group"), f"{label}.completed_group")
        total_raw = raw.get("total_group")
        stage_raw = raw.get("stage_group")
        total_group = _required_string(total_raw, f"{label}.total_group") if total_raw is not None else None
        stage_group = _required_string(stage_raw, f"{label}.stage_group") if stage_raw is not None else None
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise ProfileError(f"{label}.pattern is invalid: {error}") from error
        for key, group in (
            ("completed_group", completed_group),
            ("total_group", total_group),
            ("stage_group", stage_group),
        ):
            if group is not None and group not in compiled.groupindex:
                raise ProfileError(f"{label}.{key} references missing group {group!r}")
        return cls(mode, pattern, completed_group, total_group, stage_group)


@dataclass(frozen=True)
class OutputBudget:
    success_bytes: int
    failure_bytes: int
    timeout_bytes: int
    max_failures: int

    @classmethod
    def from_raw(cls, raw: object, label: str) -> "OutputBudget":
        if not isinstance(raw, Mapping):
            raise ProfileError(f"{label} must be an inline table")
        return cls(
            _positive_int(raw.get("success_bytes"), f"{label}.success_bytes"),
            _positive_int(raw.get("failure_bytes"), f"{label}.failure_bytes"),
            _positive_int(raw.get("timeout_bytes"), f"{label}.timeout_bytes"),
            _positive_int(raw.get("max_failures"), f"{label}.max_failures"),
        )


@dataclass(frozen=True)
class Prerequisites:
    executables: tuple[str, ...]
    files: tuple[str, ...]
    environment: tuple[str, ...]

    @classmethod
    def from_raw(cls, raw: object, label: str) -> "Prerequisites":
        if not isinstance(raw, Mapping):
            raise ProfileError(f"{label} must be an inline table")
        files = _string_list(raw.get("files"), f"{label}.files", allow_empty=True)
        return cls(
            _string_list(raw.get("executables"), f"{label}.executables", allow_empty=True),
            tuple(_relative_path(item, f"{label}.files[{index}]") for index, item in enumerate(files)),
            _string_list(raw.get("environment"), f"{label}.environment", allow_empty=True),
        )


@dataclass(frozen=True)
class CacheProfile:
    enabled: bool
    input_paths: tuple[str, ...]
    environment: tuple[str, ...]
    input_selection: str = _INPUT_SELECTION_FILESYSTEM_GLOB
    max_input_files: int | None = None
    max_input_bytes: int | None = None

    @classmethod
    def from_raw(cls, raw: object, label: str) -> "CacheProfile":
        if not isinstance(raw, Mapping):
            raise ProfileError(f"{label} must be an inline table")
        input_paths = _string_list(
            raw.get("input_paths"),
            f"{label}.input_paths",
            allow_empty=True,
        )
        normalized_paths = tuple(
            _relative_path(item, f"{label}.input_paths[{index}]")
            for index, item in enumerate(input_paths)
        )
        environment = _string_list(
            raw.get("environment"),
            f"{label}.environment",
            allow_empty=True,
        )
        input_selection = _required_string(
            raw.get("input_selection", _INPUT_SELECTION_FILESYSTEM_GLOB),
            f"{label}.input_selection",
        )
        if input_selection not in _SUPPORTED_INPUT_SELECTIONS:
            raise ProfileError(
                f"{label}.input_selection must be one of "
                f"{sorted(_SUPPORTED_INPUT_SELECTIONS)}"
            )
        has_limits = "max_input_files" in raw or "max_input_bytes" in raw
        if input_selection == _INPUT_SELECTION_GIT_TRACKED_MARKDOWN:
            if normalized_paths:
                raise ProfileError(
                    f"{label}.input_paths must be empty for "
                    "git-tracked-markdown selection"
                )
            if "max_input_files" not in raw or "max_input_bytes" not in raw:
                raise ProfileError(
                    f"{label} git-tracked-markdown selection requires "
                    "max_input_files and max_input_bytes"
                )
            max_input_files = _positive_int(
                raw["max_input_files"], f"{label}.max_input_files"
            )
            max_input_bytes = _positive_int(
                raw["max_input_bytes"], f"{label}.max_input_bytes"
            )
        else:
            if has_limits:
                raise ProfileError(
                    f"{label} filesystem-glob selection does not accept "
                    "max_input_files or max_input_bytes"
                )
            max_input_files = None
            max_input_bytes = None
        return cls(
            _boolean(raw.get("enabled"), f"{label}.enabled"),
            normalized_paths,
            environment,
            input_selection,
            max_input_files,
            max_input_bytes,
        )


@dataclass(frozen=True)
class CommandProfile:
    command_id: str
    workload_class: str
    runtime_class: str
    platforms: tuple[str, ...]
    invocation: str
    argv: tuple[str, ...]
    cwd: str
    parser: str
    require_parser_confirmation: bool
    execution_timeout_seconds: int
    observed_max_success_seconds: float
    observed_max_progress_gap_seconds: float
    stall_timeout_seconds: int | None
    progress: ProgressProfile
    output: OutputBudget
    prerequisites: Prerequisites
    initial_limit_reason: str
    last_calibrated_at: str
    cache: CacheProfile = CacheProfile(False, (), ())
    evidence_id: str | None = None
    evidence: EvidenceContract | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return self.command_id, self.workload_class, self.runtime_class

    @classmethod
    def from_raw(cls, raw: object, index: int) -> "CommandProfile":
        label = f"commands[{index}]"
        if not isinstance(raw, Mapping):
            raise ProfileError(f"{label} must be a table")
        platforms = _string_list(raw.get("platforms"), f"{label}.platforms")
        unsupported = set(platforms) - _SUPPORTED_PLATFORMS
        if unsupported:
            raise ProfileError(f"{label}.platforms contains unsupported values: {sorted(unsupported)}")
        invocation = _required_string(raw.get("invocation"), f"{label}.invocation")
        if invocation not in _SUPPORTED_INVOCATIONS:
            raise ProfileError(f"{label}.invocation must be one of {sorted(_SUPPORTED_INVOCATIONS)}")
        parser_name = _required_string(raw.get("parser"), f"{label}.parser")
        if parser_name not in _SUPPORTED_PARSERS:
            raise ProfileError(f"{label}.parser must be one of {sorted(_SUPPORTED_PARSERS)}")
        stall_raw = raw.get("stall_timeout_seconds")
        if isinstance(stall_raw, bool) or not isinstance(stall_raw, int) or stall_raw < 0:
            raise ProfileError(f"{label}.stall_timeout_seconds must be a non-negative integer")
        progress = ProgressProfile.from_raw(raw.get("progress"), f"{label}.progress")
        if stall_raw and progress.mode == "unavailable":
            raise ProfileError(f"{label}.stall_timeout_seconds requires a progress probe")
        result = cls(
            _identifier(raw.get("command_id"), f"{label}.command_id"),
            _identifier(raw.get("workload_class"), f"{label}.workload_class"),
            _identifier(raw.get("runtime_class"), f"{label}.runtime_class"),
            platforms,
            invocation,
            _string_list(raw.get("argv"), f"{label}.argv"),
            _relative_path(raw.get("cwd"), f"{label}.cwd"),
            parser_name,
            _boolean(raw.get("require_parser_confirmation"), f"{label}.require_parser_confirmation"),
            _positive_int(raw.get("execution_timeout_seconds"), f"{label}.execution_timeout_seconds"),
            _non_negative_number(raw.get("observed_max_success_seconds"), f"{label}.observed_max_success_seconds"),
            _non_negative_number(
                raw.get("observed_max_progress_gap_seconds"),
                f"{label}.observed_max_progress_gap_seconds",
            ),
            stall_raw or None,
            progress,
            OutputBudget.from_raw(raw.get("output"), f"{label}.output"),
            Prerequisites.from_raw(raw.get("prerequisites"), f"{label}.prerequisites"),
            _required_string(raw.get("initial_limit_reason"), f"{label}.initial_limit_reason"),
            _timestamp_with_timezone(raw.get("last_calibrated_at"), f"{label}.last_calibrated_at"),
            CacheProfile.from_raw(raw.get("cache"), f"{label}.cache"),
            (
                _identifier(raw.get("evidence_id"), f"{label}.evidence_id")
                if raw.get("evidence_id") is not None
                else None
            ),
            None,
        )
        if result.observed_max_success_seconds > 0:
            expected = math.ceil(result.observed_max_success_seconds * 1.10)
            if result.execution_timeout_seconds != expected:
                raise ProfileError(
                    f"{label}.execution_timeout_seconds must equal ceil(1.10 * observed maximum) = {expected}"
                )
        if result.progress.mode == "unavailable":
            if result.observed_max_progress_gap_seconds > 0:
                raise ProfileError(
                    f"{label}.observed_max_progress_gap_seconds requires a progress probe"
                )
        elif result.observed_max_progress_gap_seconds > 0:
            expected_stall = math.ceil(result.observed_max_progress_gap_seconds * 1.10)
            if result.stall_timeout_seconds != expected_stall:
                raise ProfileError(
                    f"{label}.stall_timeout_seconds must equal "
                    f"ceil(1.10 * observed progress gap) = {expected_stall}"
                )
        return result

    def calibrated(
        self,
        maximum_seconds: float,
        calibrated_at: str,
        *,
        maximum_progress_gap_seconds: float = 0.0,
    ) -> "CommandProfile":
        if maximum_seconds <= 0:
            raise ProfileError("maximum_seconds must be positive")
        _timestamp_with_timezone(calibrated_at, "calibrated_at")
        maximum = max(maximum_seconds, self.observed_max_success_seconds)
        gap = self.observed_max_progress_gap_seconds
        stall = self.stall_timeout_seconds
        if self.progress.mode == "regex" and maximum_progress_gap_seconds > 0:
            gap = max(maximum_progress_gap_seconds, gap)
            stall = math.ceil(gap * 1.10)
        return replace(
            self,
            observed_max_success_seconds=maximum,
            observed_max_progress_gap_seconds=gap,
            execution_timeout_seconds=math.ceil(maximum * 1.10),
            stall_timeout_seconds=stall,
            last_calibrated_at=calibrated_at,
        )


@dataclass(frozen=True)
class ProfileSet:
    profiles: tuple[CommandProfile, ...]
    evidence_contracts: tuple[EvidenceContract, ...] = ()

    def select(
        self,
        command_id: str,
        *,
        platform_name: str,
        workload_class: str | None = None,
        runtime_class: str | None = None,
    ) -> CommandProfile:
        matches = [
            item
            for item in self.profiles
            if item.command_id == command_id
            and platform_name in item.platforms
            and (workload_class is None or item.workload_class == workload_class)
            and (runtime_class is None or item.runtime_class == runtime_class)
        ]
        if not matches:
            raise ProfileError(
                "no registered profile matches "
                f"command_id={command_id!r}, platform={platform_name!r}, "
                f"workload_class={workload_class!r}, runtime_class={runtime_class!r}"
            )
        if len(matches) != 1:
            variants = ", ".join(f"{item.workload_class}/{item.runtime_class}" for item in matches)
            raise ProfileError(f"command profile selection is ambiguous: {variants}")
        return matches[0]

    def replace_profile(self, updated: CommandProfile) -> "ProfileSet":
        found = False
        values: list[CommandProfile] = []
        for item in self.profiles:
            if item.key == updated.key:
                values.append(updated)
                found = True
            else:
                values.append(item)
        if not found:
            raise ProfileError(f"profile not found for calibration: {updated.key}")
        return ProfileSet(tuple(values), self.evidence_contracts)


def load_profiles(path: Path) -> ProfileSet:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ProfileError("command profile schema_version must equal 1")
    evidence_raw = raw.get("evidence_contracts", [])
    if not isinstance(evidence_raw, list):
        raise ProfileError("evidence_contracts must be an array of tables")
    evidence_contracts = tuple(
        EvidenceContract.from_raw(item, index)
        for index, item in enumerate(evidence_raw)
    )
    evidence_by_id: dict[str, EvidenceContract] = {}
    for item in evidence_contracts:
        if item.evidence_id in evidence_by_id:
            raise ProfileError(f"duplicate evidence contract ID: {item.evidence_id}")
        evidence_by_id[item.evidence_id] = item

    commands = raw.get("commands")
    if not isinstance(commands, list) or not commands:
        raise ProfileError("command profile file must contain [[commands]] tables")
    parsed_profiles = tuple(
        CommandProfile.from_raw(item, index) for index, item in enumerate(commands)
    )
    profiles: list[CommandProfile] = []
    seen: set[tuple[str, str, str]] = set()
    for item in parsed_profiles:
        if item.key in seen:
            raise ProfileError(f"duplicate command profile key: {item.key}")
        seen.add(item.key)
        evidence = None
        if item.evidence_id is not None:
            evidence = evidence_by_id.get(item.evidence_id)
            if evidence is None:
                raise ProfileError(
                    f"command profile {item.key} references unknown evidence_id "
                    f"{item.evidence_id!r}"
                )
        profiles.append(replace(item, evidence=evidence))
    return ProfileSet(tuple(profiles), evidence_contracts)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _inline(values: Iterable[tuple[str, str]]) -> str:
    return "{ " + ", ".join(f"{key} = {value}" for key, value in values) + " }"


def render_profiles(profile_set: ProfileSet) -> str:
    lines = ["schema_version = 1", ""]
    for contract in profile_set.evidence_contracts:
        lines.extend(
            [
                "[[evidence_contracts]]",
                f"evidence_id = {_toml_string(contract.evidence_id)}",
                f"required_globs = {_toml_list(contract.required_globs)}",
                f"optional_globs = {_toml_list(contract.optional_globs)}",
                f"max_files = {contract.max_files}",
                f"max_total_bytes = {contract.max_total_bytes}",
                f"retention_class = {_toml_string(contract.retention_class)}",
                "",
            ]
        )
    for item in profile_set.profiles:
        progress_pairs: list[tuple[str, str]] = [("mode", _toml_string(item.progress.mode))]
        if item.progress.mode == "regex":
            progress_pairs.extend(
                [
                    ("pattern", _toml_string(item.progress.pattern or "")),
                    ("completed_group", _toml_string(item.progress.completed_group or "")),
                ]
            )
            if item.progress.total_group is not None:
                progress_pairs.append(("total_group", _toml_string(item.progress.total_group)))
            if item.progress.stage_group is not None:
                progress_pairs.append(("stage_group", _toml_string(item.progress.stage_group)))
        maximum = (
            str(int(item.observed_max_success_seconds))
            if item.observed_max_success_seconds.is_integer()
            else repr(item.observed_max_success_seconds)
        )
        lines.extend(
            [
                "[[commands]]",
                f"command_id = {_toml_string(item.command_id)}",
                f"workload_class = {_toml_string(item.workload_class)}",
                f"runtime_class = {_toml_string(item.runtime_class)}",
                f"platforms = {_toml_list(item.platforms)}",
                f"invocation = {_toml_string(item.invocation)}",
                f"argv = {_toml_list(item.argv)}",
                f"cwd = {_toml_string(item.cwd)}",
                *(
                    [f"evidence_id = {_toml_string(item.evidence_id)}"]
                    if item.evidence_id is not None
                    else []
                ),
                f"parser = {_toml_string(item.parser)}",
                f"require_parser_confirmation = {'true' if item.require_parser_confirmation else 'false'}",
                f"execution_timeout_seconds = {item.execution_timeout_seconds}",
                f"observed_max_success_seconds = {maximum}",
                "observed_max_progress_gap_seconds = "
                + (
                    str(int(item.observed_max_progress_gap_seconds))
                    if item.observed_max_progress_gap_seconds.is_integer()
                    else repr(item.observed_max_progress_gap_seconds)
                ),
                f"stall_timeout_seconds = {item.stall_timeout_seconds or 0}",
                f"progress = {_inline(progress_pairs)}",
                "output = "
                + _inline(
                    [
                        ("success_bytes", str(item.output.success_bytes)),
                        ("failure_bytes", str(item.output.failure_bytes)),
                        ("timeout_bytes", str(item.output.timeout_bytes)),
                        ("max_failures", str(item.output.max_failures)),
                    ]
                ),
                "prerequisites = "
                + _inline(
                    [
                        ("executables", _toml_list(item.prerequisites.executables)),
                        ("files", _toml_list(item.prerequisites.files)),
                        ("environment", _toml_list(item.prerequisites.environment)),
                    ]
                ),
                "cache = "
                + _inline(
                    [
                        ("enabled", "true" if item.cache.enabled else "false"),
                        *(
                            [("input_selection", _toml_string(item.cache.input_selection))]
                            if item.cache.input_selection != _INPUT_SELECTION_FILESYSTEM_GLOB
                            else []
                        ),
                        ("input_paths", _toml_list(item.cache.input_paths)),
                        ("environment", _toml_list(item.cache.environment)),
                        *(
                            [("max_input_files", str(item.cache.max_input_files))]
                            if item.cache.max_input_files is not None
                            else []
                        ),
                        *(
                            [("max_input_bytes", str(item.cache.max_input_bytes))]
                            if item.cache.max_input_bytes is not None
                            else []
                        ),
                    ]
                ),
                f"initial_limit_reason = {_toml_string(item.initial_limit_reason)}",
                f"last_calibrated_at = {_toml_string(item.last_calibrated_at)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_profiles(path: Path, profile_set: ProfileSet) -> None:
    write_text_atomically(path, render_profiles(profile_set))


def write_text_atomically(path: Path, content: str, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        try:
            temporary.chmod(mode)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def render_runtime_limits(profile_set: ProfileSet) -> str:
    lines = [
        "# Лимиты выполнения зарегистрированных команд",
        "",
        "## Назначение",
        "",
        "Документ генерируется из",
        "`ai-assistant/checks/command-profiles.toml` и не является вторым",
        "источником истины. Изменять таблицу вручную нельзя.",
        "",
        "Штатный лимит равен 110% максимального подтверждённого успешного",
        "времени для той же комбинации `command_id`, `workload_class` и",
        "`runtime_class`. Пока история отсутствует, действует явно",
        "обоснованный начальный лимит.",
        "",
        "## Зарегистрированные профили",
        "",
        "| Команда | Нагрузка | Runtime | Платформы | Timeout, с | Максимум, с | Stall, с | Макс. progress gap, с | Progress | Последняя калибровка |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for item in profile_set.profiles:
        maximum = f"{item.observed_max_success_seconds:g}" if item.observed_max_success_seconds else "нет истории"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item.command_id}`",
                    f"`{item.workload_class}`",
                    f"`{item.runtime_class}`",
                    ", ".join(f"`{value}`" for value in item.platforms),
                    str(item.execution_timeout_seconds),
                    maximum,
                    str(item.stall_timeout_seconds) if item.stall_timeout_seconds else "—",
                    (
                        f"{item.observed_max_progress_gap_seconds:g}"
                        if item.observed_max_progress_gap_seconds
                        else "нет истории"
                    ),
                    f"`{item.progress.mode}`",
                    item.last_calibrated_at,
                ]
            )
            + " |"
        )
    lines.extend(["", "## Начальные лимиты", ""])
    for item in profile_set.profiles:
        lines.extend(
            [
                f"### `{item.command_id}` / `{item.workload_class}` / `{item.runtime_class}`",
                "",
                textwrap.fill(item.initial_limit_reason, width=72),
                "",
            ]
        )
    lines.extend(
        [
            "## Автоматическое обновление лимита",
            "",
            "Перед каждым запуском runner автоматически читает durable",
            "ledger и применяет `ceil(1.10 × longest verified success)`",
            "для той же комбинации command/workload/runtime. Новый",
            "подтверждённый successful result записывается в ledger и",
            "автоматически становится лимитом следующего запуска; действий",
            "AI-агента не требуется. `calibrate_timeout.py` остаётся только",
            "maintenance-командой для явной материализации ledger history в",
            "version-controlled profile snapshot и этот generated document.",
            "",
            "Подтверждённые интервалы между последовательными progress",
            "events калибруют optional stall timeout по тому же правилу +10%;",
            "подготовка до первого event в этот baseline не входит.",
            "Timeout, stall, отмена, ошибка среды, hang или loop не могут",
            "увеличить лимит. После timeout сначала исследуются сохранённые",
            "log, progress и process-tree evidence. Один диагностический",
            "rerun с увеличенным лимитом допускается только по ссылке на",
            "предыдущий timed-out check; новый максимум устанавливается",
            "только после успешного завершения rerun.",
            "",
            "## Ограничение наблюдаемости",
            "",
            "`regex` probe учитывает только монотонное предметное",
            "продвижение. Stall clock запускается только после первого",
            "подтверждённого progress event, поэтому подготовительная стадия",
            "ограничивается общим empirical timeout, а не stall timeout.",
            "Heartbeat, повторяющаяся строка и загрузка CPU не доказывают",
            "полезный прогресс. При `progress = unavailable`",
            "автоматически отличить медленную работу от бесконечного цикла",
            "нельзя; empirical timeout остаётся safety boundary.",
            "",
        ]
    )
    return "\n".join(lines)


def write_runtime_limits(path: Path, profile_set: ProfileSet) -> None:
    write_text_atomically(path, render_runtime_limits(profile_set))
