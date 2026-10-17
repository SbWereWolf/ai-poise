"""Semantic summary rules adopted from ERP503c9f1a; supplied lines only.

Raw stream capture and presentation belong to their existing I/O owners.
"""
from dataclasses import dataclass, asdict
import re
from typing import Iterable


def _clean(value: str, limit=None) -> str:
    # Complete semantic text is retained. Agent presentation owns its budget.
    return value.strip()


def _lines(lines: Iterable[str]):
    for line in lines:
        yield line.rstrip('\r\n')


@dataclass(frozen=True)
class FailureItem:
    name: str
    message: str
    source: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"name": self.name, "message": self.message, "source": self.source}


@dataclass(frozen=True)
class ParsedResult:
    name: str
    confirmed: bool
    summary: str
    counts: dict[str, int]
    failures: tuple[FailureItem, ...]


def _source(line: str) -> str | None:
    match = re.search(
        r"(?P<source>(?:[A-Za-z]:)?[^\s:][^\s]*?\.(?:php|py|js|mjs|cjs|ts|tsx|vue|md)):(?P<line>\d+)(?::\d+)?",
        line,
    )
    return f"{match.group('source')}:{match.group('line')}" if match else None


def _generic_parser(path: Iterable[str], maximum: int) -> ParsedResult:
    patterns = (
        re.compile(r"(?i)^\s*(?:FAIL(?:ED|URE)?|ERROR|FATAL|PANIC)\b"),
        re.compile(r"(?i)\b(?:AssertionError|Exception|Traceback)\b"),
    )
    last = ""
    failures: list[FailureItem] = []
    for line in _lines(path):
        if line.strip():
            last = line.strip()
        if len(failures) < maximum and any(pattern.search(line) for pattern in patterns):
            failures.append(FailureItem("command-output", _clean(line), _source(line)))
    return ParsedResult(
        "generic",
        bool(last),
        _clean(last or "Command completed without a textual summary"),
        {},
        tuple(failures),
    )


def _phpunit_parser(path: Iterable[str], maximum: int) -> ParsedResult:
    summary_re = re.compile(
        r"Tests:\s*(?P<tests>\d+)(?:,\s*Assertions:\s*(?P<assertions>\d+))?"
        r"(?:,\s*Errors:\s*(?P<errors>\d+))?(?:,\s*Failures:\s*(?P<failures>\d+))?"
        r"(?:,\s*Skipped:\s*(?P<skipped>\d+))?",
        re.IGNORECASE,
    )
    header_re = re.compile(r"^\s*\d+\)\s+(?P<name>.+)$")
    summary = ""
    counts: dict[str, int] = {}
    failures: list[FailureItem] = []
    pending: str | None = None
    for line in _lines(path):
        match = summary_re.search(line)
        if match:
            summary = line.strip()
            counts = {key: int(value) for key, value in match.groupdict().items() if value is not None}
        header = header_re.match(line)
        if header:
            pending = _clean(header.group("name"), 300)
        elif pending and line.strip() and len(failures) < maximum:
            failures.append(FailureItem(pending, _clean(line), _source(line)))
            pending = None
    return ParsedResult("phpunit", bool(summary), _clean(summary or "PHPUnit summary not found"), counts, tuple(failures))


def _vitest_parser(path: Iterable[str], maximum: int) -> ParsedResult:
    summary_re = re.compile(
        r"Tests\s+(?:(?P<failed>\d+)\s+failed\s*\|\s*)?(?:(?P<passed>\d+)\s+passed)?(?:\s*\|\s*(?P<skipped>\d+)\s+skipped)?",
        re.IGNORECASE,
    )
    failure_re = re.compile(r"^\s*(?:FAIL|×|✗)\s+(?P<name>.+)$", re.IGNORECASE)
    summary = ""
    counts: dict[str, int] = {}
    failures: list[FailureItem] = []
    for line in _lines(path):
        match = summary_re.search(line)
        if match and any(value is not None for value in match.groupdict().values()):
            summary = line.strip()
            counts = {key: int(value) for key, value in match.groupdict().items() if value is not None}
        failure = failure_re.match(line)
        if failure and len(failures) < maximum:
            failures.append(FailureItem(_clean(failure.group("name"), 300), _clean(line), _source(line)))
    return ParsedResult("vitest", bool(summary), _clean(summary or "Vitest summary not found"), counts, tuple(failures))


def _playwright_parser(path: Iterable[str], maximum: int) -> ParsedResult:
    count_re = re.compile(r"(?P<count>\d+)\s+(?P<kind>passed|failed|skipped|flaky)\b", re.IGNORECASE)
    failure_re = re.compile(r"^\s*\d+\)\s+(?P<name>.+)$")
    summary = ""
    counts: dict[str, int] = {}
    failures: list[FailureItem] = []
    for line in _lines(path):
        matches = list(count_re.finditer(line))
        if matches:
            summary = line.strip()
            for match in matches:
                counts[match.group("kind").lower()] = int(match.group("count"))
        failure = failure_re.match(line)
        if failure and len(failures) < maximum:
            failures.append(FailureItem(_clean(failure.group("name"), 300), _clean(line), _source(line)))
    return ParsedResult("playwright", bool(summary), _clean(summary or "Playwright summary not found"), counts, tuple(failures))


def _phpstan_parser(path: Iterable[str], maximum: int) -> ParsedResult:
    ok_re = re.compile(r"\[OK\]\s+No errors", re.IGNORECASE)
    count_re = re.compile(r"Found\s+(?P<errors>\d+)\s+error", re.IGNORECASE)
    item_re = re.compile(r"^\s*(?P<source>[^\s].*?:\d+)\s+(?P<message>.+)$")
    summary = ""
    counts: dict[str, int] = {}
    failures: list[FailureItem] = []
    for line in _lines(path):
        if ok_re.search(line):
            summary, counts = line.strip(), {"errors": 0}
        count = count_re.search(line)
        if count:
            summary, counts = line.strip(), {"errors": int(count.group("errors"))}
        item = item_re.match(line)
        if item and len(failures) < maximum:
            failures.append(FailureItem("phpstan", _clean(item.group("message")), _clean(item.group("source"), 400)))
    return ParsedResult("phpstan", bool(summary), _clean(summary or "PHPStan summary not found"), counts, tuple(failures))


def _deptrac_parser(path: Iterable[str], maximum: int) -> ParsedResult:
    summary_re = re.compile(r"(?P<key>Violations|Skipped|Uncovered|Allowed|Warnings|Errors)\s*[:=]\s*(?P<count>\d+)", re.IGNORECASE)
    counts: dict[str, int] = {}
    summary = ""
    failures: list[FailureItem] = []
    for line in _lines(path):
        matches = list(summary_re.finditer(line))
        if matches:
            summary = line.strip()
            for match in matches:
                counts[match.group("key").lower()] = int(match.group("count"))
        if re.search(r"(?i)\bviolation\b", line) and len(failures) < maximum:
            failures.append(FailureItem("deptrac-violation", _clean(line), _source(line)))
    return ParsedResult("deptrac", bool(summary), _clean(summary or "Deptrac summary not found"), counts, tuple(failures))


def _build_parser(path: Iterable[str], maximum: int) -> ParsedResult:
    success_re = re.compile(r"(?i)\b(?:built|build completed|compiled)\b.*\b(?:success|successfully|in\s+\d)")
    failure_re = re.compile(r"(?i)\b(?:build failed|compilation failed|error)\b")
    summary = ""
    failures: list[FailureItem] = []
    for line in _lines(path):
        if success_re.search(line):
            summary = line.strip()
        if failure_re.search(line) and len(failures) < maximum:
            failures.append(FailureItem("build", _clean(line), _source(line)))
    return ParsedResult("build", bool(summary), _clean(summary or "Build success summary not found"), {}, tuple(failures))

_PARSERS = {
    'generic': _generic_parser,
    'phpunit': _phpunit_parser,
    'vitest': _vitest_parser,
    'playwright': _playwright_parser,
    'phpstan': _phpstan_parser,
    'deptrac': _deptrac_parser,
    'build': _build_parser,
}


def parse_lines(lines, parser_name, maximum):
    result = asdict(_PARSERS[parser_name](lines, maximum))
    result['failures'] = list(result['failures'])
    return result
