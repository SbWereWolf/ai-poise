"""Shell grammar and budget decisions over an explicit file-facts port.

Extracted from the researched ERP reader grammar; no shell execution, filesystem
access, policy loading, storage, native identity or output clipping lives here.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import PurePath as Path
import hashlib
import re
import shlex
from typing import Any, Iterable, Mapping, Sequence

_SHELL_SEPARATORS = {';', '&&', '||', '|', '&', '\n', '(', ')'}
_REDIRECTION_TOKENS = {'>', '>>', '<', '<<', '<<<', '<>', '>&', '<&', '&>', '&>>'}
_SHELL_RESERVED_PREFIXES = {'!', '{', '}', 'do', 'elif', 'else', 'if', 'then', 'time'}
_COMMAND_WRAPPERS = {'builtin', 'command', 'exec', 'nohup'}
_ENV_ASSIGNMENT = re.compile('^[A-Za-z_][A-Za-z0-9_]*=.*$', re.DOTALL)
_GLOB_PATTERN = re.compile('[*?\\[]|\\{[^{}]*,[^{}]*\\}')
_DYNAMIC_PATTERN = re.compile('\\$|`|<\\(|>\\(')
_NUMERIC_SED_RANGE = re.compile('^(\\d+)(?:,(\\d+))?p$')
_NUMERIC_VALUE = re.compile('^\\+?\\d+$')
_NEGATIVE_VALUE = re.compile('^-\\d+$')
_READER_BASENAME = re.compile('^[A-Za-z0-9._+-]+$')
_LINE_PRESERVING_COMPLETE_READERS = {'cat', 'nl', 'tac'}
_DISCOVERY_FIRST_GUIDANCE = 'Use `rg -n` or heading discovery first, then read one explicit file or bounded excerpts within the configured budget. Do not retry the same multi-document bundle with smaller whole-file ranges.'

@dataclass(frozen=True)
class FileOperand:
    """One potential file operand passed to a content reader."""
    classification: str
    label: str
    path: Path | None
    size_bytes: int | None
    line_count: int | None
    line_count_exact: bool | None = None
    relative: bool = False

@dataclass(frozen=True)
class ReaderUse:
    """One detected content-reader invocation."""
    reader: str
    mode: str
    operands: tuple[FileOperand, ...]
    requested_lines_per_file: int | None = None
    requested_bytes_per_file: int | None = None
    output_capped_by_input: bool = False
    source: str = 'direct'
    line_ranges: tuple[tuple[int, int], ...] = ()
    address_space: str = 'file'
    reversed_input: bool = False

    def source_ranges(self, index):
        """Project output addresses onto one observed original input.

        None means unproved coordinates; an empty tuple proves no contribution.
        Keep repeated ranges here: budgeting counts emitted duplicates, while
        the ledger separately unions the same canonical source intervals.
        """
        operand = self.operands[index]
        if self.requested_bytes_per_file is not None:
            return None
        ranges = self.line_ranges
        if not ranges:
            if self.mode == 'complete' and operand.line_count_exact:
                return ((1, operand.line_count),) if operand.line_count else ()
            if self.reader != 'tail' or not operand.line_count_exact:
                return None
            count = self.requested_lines_per_file
            if count is None:
                return None
            ranges = ((max(1, operand.line_count - count + 1), operand.line_count),)
        offset = 0
        if self.address_space == 'stream':
            preceding = self.operands[:index]
            if any(p.line_count is None for p in preceding):
                return None
            offset = sum(p.line_count for p in preceding)
            if any(not p.line_count_exact for p in preceding):
                return () if all(last <= offset for _, last in ranges) else None
        if self.reversed_input and not operand.line_count_exact:
            return None
        projected = []
        for first, last in ranges:
            first, last = max(1, first - offset), last - offset
            if operand.line_count_exact:
                last = min(last, operand.line_count)
            elif operand.line_count is None or last > operand.line_count:
                return None
            if first > last:
                continue
            if self.reversed_input:
                first, last = operand.line_count - last + 1, operand.line_count - first + 1
            projected.append((first, last))
        return tuple(projected)

@dataclass(frozen=True)
class IntakeDecision:
    """Classification result returned to the Codex hook adapter."""
    decision: str
    reason_code: str
    reason: str
    readers: tuple[ReaderUse, ...]
    projected_files: int
    projected_bytes: int | None
    projected_lines: int | None

    def audit_value(self) -> dict[str, Any]:
        value = asdict(self)
        for reader in value['readers']:
            for operand in reader['operands']:
                operand.pop('path', None)
        return value

class CommandClassifier:

    def __init__(self, facts):
        self.facts = facts

    def _reason_labels(self, readers, policy):
        return ', '.join((operand.label for reader in readers for operand in reader.operands if operand.classification != 'stdin'))

    def _decision(self, decision, reason_code, reason, readers, policy, *, projected_files=None, projected_bytes=None, projected_lines=None):
        count = projected_files if projected_files is not None else sum((operand.classification != 'stdin' for reader in readers for operand in reader.operands))
        return IntakeDecision(decision, reason_code, reason, tuple(readers), count, projected_bytes, projected_lines)

    def _mark_unquoted_newlines(self, command: str) -> tuple[str, str]:
        """Replace command-separating newlines without touching quoted data."""
        sentinel = '__CODEX_DOCUMENT_INTAKE_NEWLINE__'
        while sentinel in command:
            sentinel += '_'
        output: list[str] = []
        quote: str | None = None
        index = 0
        while index < len(command):
            character = command[index]
            if quote == 'single':
                output.append(character)
                if character == "'":
                    quote = None
                index += 1
                continue
            if quote == 'double':
                if character == '"':
                    output.append(character)
                    quote = None
                    index += 1
                    continue
                if character == '\\' and index + 1 < len(command):
                    next_character = command[index + 1]
                    if next_character == '\n':
                        index += 2
                        continue
                    if next_character == '\r' and index + 2 < len(command) and (command[index + 2] == '\n'):
                        index += 3
                        continue
                    output.extend((character, next_character))
                    index += 2
                    continue
                output.append(character)
                index += 1
                continue
            if character == "'":
                output.append(character)
                quote = 'single'
                index += 1
                continue
            if character == '"':
                output.append(character)
                quote = 'double'
                index += 1
                continue
            if character == '\\' and index + 1 < len(command):
                next_character = command[index + 1]
                if next_character == '\n':
                    index += 2
                    continue
                if next_character == '\r' and index + 2 < len(command) and (command[index + 2] == '\n'):
                    index += 3
                    continue
                output.extend((character, next_character))
                index += 2
                continue
            if character == '\r':
                if index + 1 < len(command) and command[index + 1] == '\n':
                    index += 1
                output.extend((' ', sentinel, ' '))
                index += 1
                continue
            if character == '\n':
                output.extend((' ', sentinel, ' '))
                index += 1
                continue
            output.append(character)
            index += 1
        return (''.join(output), sentinel)

    def _shell_tokens(self, command: str) -> list[str]:
        marked_command, newline_sentinel = self._mark_unquoted_newlines(command)
        lexer = shlex.shlex(marked_command, posix=True, punctuation_chars=';&|<>()')
        lexer.whitespace = ' \t\r\n'
        lexer.whitespace_split = True
        lexer.commenters = ''
        return ['\n' if token == newline_sentinel else token for token in lexer]

    def _segments_with_separators(self, tokens: Sequence[str]) -> list[tuple[list[str], str | None]]:
        output: list[tuple[list[str], str | None]] = []
        current: list[str] = []
        for token in tokens:
            if token in _SHELL_SEPARATORS:
                if current:
                    output.append((current, token))
                    current = []
                continue
            current.append(token)
        if current:
            output.append((current, None))
        return output

    def _segments(self, tokens: Sequence[str]) -> list[list[str]]:
        return [segment for segment, _ in self._segments_with_separators(tokens)]

    def _basename(self, token: str) -> str:
        candidate = token.replace('\\', '/').rsplit('/', 1)[-1]
        if not _READER_BASENAME.match(candidate):
            return candidate
        return candidate

    def _strip_prefixes(self, tokens: Sequence[str]) -> list[str]:
        output = list(tokens)
        while output and (output[0] in _SHELL_RESERVED_PREFIXES or _ENV_ASSIGNMENT.match(output[0]) is not None):
            output.pop(0)
        while output:
            command = self._basename(output[0])
            if command in _COMMAND_WRAPPERS:
                output.pop(0)
                while output and output[0].startswith('-'):
                    output.pop(0)
                continue
            if command == 'env':
                output.pop(0)
                while output and (output[0].startswith('-') or _ENV_ASSIGNMENT.match(output[0]) is not None):
                    output.pop(0)
                continue
            if command == 'sudo':
                output.pop(0)
                while output and output[0].startswith('-'):
                    option = output.pop(0)
                    if option in {'-C', '-D', '-g', '-h', '-p', '-R', '-T', '-u'} and output:
                        output.pop(0)
                continue
            break
        return output

    def _redirection_parts(self, tokens: Sequence[str], index: int) -> tuple[int | None, str, str | None, int] | None:
        """Return descriptor, operator, target, and token width."""
        token = tokens[index]
        descriptor: int | None = None
        operator_index = index
        if token.isdigit() and index + 1 < len(tokens) and (tokens[index + 1] in _REDIRECTION_TOKENS):
            descriptor = int(token)
            operator_index = index + 1
        elif token not in _REDIRECTION_TOKENS:
            return None
        target_index = operator_index + 1
        target = tokens[target_index] if target_index < len(tokens) else None
        width = min(len(tokens) - index, target_index - index + 1)
        return (descriptor, tokens[operator_index], target, width)

    def _redirection_width(self, tokens: Sequence[str], index: int) -> int:
        """Return the number of shell tokens occupied by one redirection."""
        parts = self._redirection_parts(tokens, index)
        return 0 if parts is None else parts[3]

    def _redirection_operands(self, tokens: Sequence[str]) -> list[str]:
        operands: list[str] = []
        index = 0
        while index < len(tokens):
            parts = self._redirection_parts(tokens, index)
            if parts is None:
                index += 1
                continue
            descriptor, operator, target, width = parts
            if operator == '<' and descriptor in {None, 0} and (target is not None):
                operands.append(target)
            index += width
        return operands

    def _non_option_operands(self, tokens: Sequence[str], *, option_values: Iterable[str]=()) -> list[str]:
        value_options = set(option_values)
        output: list[str] = []
        options_done = False
        index = 0
        while index < len(tokens):
            token = tokens[index]
            redirection_width = self._redirection_width(tokens, index)
            if redirection_width:
                index += redirection_width
                continue
            if not options_done and token == '--':
                options_done = True
                index += 1
                continue
            if not options_done and token in value_options:
                index += 2
                continue
            if not options_done and token.startswith('-') and (token != '-'):
                index += 1
                continue
            output.append(token)
            index += 1
        return output

    def _operands(self, tokens: Sequence[str], *, cwd: Path, repository_root: Path, line_limit: int, option_values: Iterable[str]=()) -> tuple[FileOperand, ...]:
        values = self._non_option_operands(tokens, option_values=option_values)
        values.extend(self._redirection_operands(tokens))
        return tuple((self.facts.operand(token, cwd=cwd, repository_root=repository_root, line_limit=line_limit) for token in values))

    def _parse_count(self, reader: str, value: str) -> tuple[str, int | None]:
        normalized = value.strip()
        if reader == 'tail':
            if normalized.startswith('+') and normalized[1:].isdigit():
                return ('complete', None)
            if normalized.startswith('-') and normalized[1:].isdigit():
                return ('bounded', int(normalized[1:]))
        if _NUMERIC_VALUE.match(normalized):
            return ('bounded', int(normalized.lstrip('+')))
        if _NEGATIVE_VALUE.match(normalized):
            return ('complete', None)
        return ('complete', None)

    def _head_tail_use(self, reader: str, tokens: Sequence[str], *, cwd: Path, repository_root: Path, line_limit: int) -> ReaderUse:
        mode = 'bounded'
        requested_lines: int | None = 10
        requested_bytes: int | None = None
        option_values = {'-n', '--lines', '-c', '--bytes'}
        index = 0
        while index < len(tokens):
            token = tokens[index]
            next_value = tokens[index + 1] if index + 1 < len(tokens) else None
            if re.fullmatch(r'-[nc][+-]?\d+', token):
                mode, count = self._parse_count(reader, token[2:])
                requested_lines = count if token[1] == 'n' else None
                requested_bytes = count if token[1] == 'c' else None
                index += 1
                continue
            if token in {'-n', '--lines'} and next_value is not None:
                mode, requested_lines = self._parse_count(reader, next_value)
                requested_bytes = None
                index += 2
                continue
            if token.startswith('--lines='):
                mode, requested_lines = self._parse_count(reader, token.split('=', 1)[1])
                requested_bytes = None
                index += 1
                continue
            if token in {'-c', '--bytes'} and next_value is not None:
                mode, requested_bytes = self._parse_count(reader, next_value)
                requested_lines = None
                index += 2
                continue
            if token.startswith('--bytes='):
                mode, requested_bytes = self._parse_count(reader, token.split('=', 1)[1])
                requested_lines = None
                index += 1
                continue
            if re.match('^-\\d+$', token):
                if reader == 'head':
                    mode = 'bounded'
                    requested_lines = int(token[1:])
                else:
                    mode = 'bounded'
                    requested_lines = int(token[1:])
                requested_bytes = None
            index += 1
        line_ranges: tuple[tuple[int, int], ...] = ()
        if reader == 'head' and mode == 'bounded' and (requested_lines is not None) and (requested_lines > 0):
            line_ranges = ((1, requested_lines),)
        return ReaderUse(reader=reader, mode=mode, operands=self._operands(tokens, cwd=cwd, repository_root=repository_root, line_limit=line_limit, option_values=option_values), requested_lines_per_file=requested_lines, requested_bytes_per_file=requested_bytes, output_capped_by_input=True, line_ranges=line_ranges)

    def _sed_ranges(self, script: str) -> tuple[tuple[int, int], ...] | None:
        ranges: list[tuple[int, int]] = []
        commands = [part.strip() for part in script.split(';') if part.strip()]
        if not commands:
            return None
        for command in commands:
            compact = re.sub('\\s+', '', command)
            match = _NUMERIC_SED_RANGE.fullmatch(compact)
            if match is None:
                return None
            start = int(match.group(1))
            end = int(match.group(2) or match.group(1))
            if end < start:
                return None
            ranges.append((start, end))
        return tuple(ranges)

    def _ranges_do_not_overlap(self, ranges: Sequence[tuple[int, int]]) -> bool:
        ordered = sorted(ranges)
        return all((current_start > previous_end for (_, previous_end), (current_start, _) in zip(ordered, ordered[1:])))

    def _sed_use(self, tokens: Sequence[str], *, cwd: Path, repository_root: Path, line_limit: int) -> ReaderUse | None:
        if any((token == '-i' or token.startswith('-i.') or token.startswith('--in-place') for token in tokens)):
            return None
        quiet = any((token in {'-n', '--quiet', '--silent'} for token in tokens))
        separate = False
        scripts: list[str] = []
        file_tokens: list[str] = []
        program_declared = False
        external_program = False
        index = 0
        options_done = False
        while index < len(tokens):
            token = tokens[index]
            redirection_width = self._redirection_width(tokens, index)
            if redirection_width:
                index += redirection_width
                continue
            if not options_done and token == '--':
                options_done = True
                index += 1
                continue
            if not options_done and token in {'-n', '--quiet', '--silent'}:
                quiet = True
                index += 1
                continue
            if not options_done and token == '--separate':
                separate = True
                index += 1
                continue
            if not options_done and token in {'-e', '--expression'}:
                if index + 1 < len(tokens):
                    scripts.append(tokens[index + 1])
                    program_declared = True
                index += 2
                continue
            if not options_done and token.startswith('--expression='):
                scripts.append(token.split('=', 1)[1])
                program_declared = True
                index += 1
                continue
            if not options_done and token in {'-f', '--file'}:
                external_program = True
                program_declared = True
                index += 2
                continue
            if not options_done and token.startswith('--file='):
                external_program = True
                program_declared = True
                index += 1
                continue
            if not options_done and token.startswith('-') and (not token.startswith('--')) and (token != '-'):
                flags = token[1:]
                marker_positions = [(flags.find(marker), marker) for marker in ('e', 'f') if marker in flags]
                marker_position, marker = min(marker_positions, default=(-1, ''))
                flag_prefix = flags if marker_position < 0 else flags[:marker_position + 1]
                if 'n' in flag_prefix:
                    quiet = True
                if 's' in flag_prefix:
                    separate = True
                if marker_position >= 0:
                    program_declared = True
                    remainder = flags[marker_position + 1:]
                    if marker == 'f':
                        external_program = True
                    elif remainder:
                        scripts.append(remainder)
                    if remainder == '' and index + 1 < len(tokens):
                        if marker == 'e':
                            scripts.append(tokens[index + 1])
                        index += 2
                    else:
                        index += 1
                    continue
                index += 1
                continue
            if not options_done and token.startswith('-'):
                index += 1
                continue
            if not program_declared:
                scripts.append(token)
                program_declared = True
            else:
                file_tokens.append(token)
            index += 1
        file_tokens.extend(self._redirection_operands(tokens))
        operands = tuple((self.facts.operand(token, cwd=cwd, repository_root=repository_root, line_limit=line_limit) for token in file_tokens))
        if external_program:
            return ReaderUse('sed', 'unknown', operands)
        if not quiet:
            return ReaderUse('sed', 'complete', operands)
        parsed_ranges = [self._sed_ranges(script) for script in scripts]
        if not scripts or any((ranges is None for ranges in parsed_ranges)):
            return ReaderUse('sed', 'unknown', operands)
        ranges = tuple((numeric_range for script_ranges in parsed_ranges if script_ranges is not None for numeric_range in script_ranges))
        requested = sum((end - start + 1 for start, end in ranges))
        return ReaderUse('sed', 'bounded', operands, requested_lines_per_file=requested, output_capped_by_input=self._ranges_do_not_overlap(ranges), line_ranges=ranges, address_space='file' if separate else 'stream')

    def _awk_use(self, tokens: Sequence[str], *, cwd: Path, repository_root: Path, line_limit: int) -> ReaderUse:
        script = ''
        file_tokens: list[str] = []
        program_declared = False
        external_program = False
        index = 0
        while index < len(tokens):
            token = tokens[index]
            redirection_width = self._redirection_width(tokens, index)
            if redirection_width:
                index += redirection_width
                continue
            if token == '-f':
                external_program = True
                program_declared = True
                index += 2
                continue
            if token.startswith('-f') and token != '-f':
                external_program = True
                program_declared = True
                index += 1
                continue
            if token in {'-F', '-v'}:
                index += 2
                continue
            if token.startswith('-F') or token.startswith('-v'):
                index += 1
                continue
            if token.startswith('-'):
                index += 1
                continue
            if not program_declared:
                script = token
                program_declared = True
            else:
                file_tokens.append(token)
            index += 1
        file_tokens.extend(self._redirection_operands(tokens))
        operands = tuple((self.facts.operand(token, cwd=cwd, repository_root=repository_root, line_limit=line_limit) for token in file_tokens))
        mode = 'complete' if not external_program and re.sub('\\s+', '', script) in {'1', '{print}'} else 'unknown'
        return ReaderUse('awk', mode, operands)

    def _perl_use(self, tokens: Sequence[str], *, cwd: Path, repository_root: Path, line_limit: int) -> ReaderUse:
        file_tokens: list[str] = []
        print_every_record = False
        explicit_print = False
        index = 0
        while index < len(tokens):
            token = tokens[index]
            redirection_width = self._redirection_width(tokens, index)
            if redirection_width:
                index += redirection_width
                continue
            if token.startswith('-') and token != '-':
                flags = token[1:]
                if 'p' in flags:
                    print_every_record = True
                if 'e' in flags or 'E' in flags:
                    if index + 1 < len(tokens):
                        explicit_print = bool(re.search('\\bprint\\b', tokens[index + 1]))
                    index += 2
                    continue
                if token in {'-F', '-I', '-M', '-m', '-x'}:
                    index += 2
                    continue
                index += 1
                continue
            file_tokens.append(token)
            index += 1
        file_tokens.extend(self._redirection_operands(tokens))
        operands = tuple((self.facts.operand(token, cwd=cwd, repository_root=repository_root, line_limit=line_limit) for token in file_tokens))
        mode = 'complete' if print_every_record or explicit_print else 'unknown'
        return ReaderUse('perl', mode, operands)

    def _conservative_use(self, reader: str, tokens: Sequence[str], *, cwd: Path, repository_root: Path, line_limit: int) -> ReaderUse:
        if reader == 'awk':
            return self._awk_use(tokens, cwd=cwd, repository_root=repository_root, line_limit=line_limit)
        return self._perl_use(tokens, cwd=cwd, repository_root=repository_root, line_limit=line_limit)

    def _reader_use(self, reader: str, tokens: Sequence[str], *, cwd: Path, repository_root: Path, policy: Mapping[str, Any]) -> ReaderUse | None:
        line_limit = max(policy['complete_reads']['small_bundle_max_lines'], policy['bounded_reads']['max_total_lines'])
        if reader in policy['readers']['complete']:
            option_values: set[str] = set()
            if reader == 'nl':
                option_values = {'-b', '-d', '-f', '-h', '-i', '-l', '-n', '-s', '-v', '-w'}
            elif reader in {'bat', 'batcat'}:
                option_values = {'--language', '--paging', '--style', '--tabs', '--theme', '--wrap', '-l'}
            return ReaderUse(reader, 'complete', self._operands(tokens, cwd=cwd, repository_root=repository_root, line_limit=line_limit, option_values=option_values))
        if reader in {'head', 'tail'}:
            return self._head_tail_use(reader, tokens, cwd=cwd, repository_root=repository_root, line_limit=line_limit)
        if reader == 'sed':
            return self._sed_use(tokens, cwd=cwd, repository_root=repository_root, line_limit=line_limit)
        return self._conservative_use(reader, tokens, cwd=cwd, repository_root=repository_root, line_limit=line_limit)

    def _reader_names(self, policy: Mapping[str, Any]) -> set[str]:
        return {*policy['readers']['complete'], *policy['readers']['range_capable'], *policy['readers']['conservative']}

    def _collapse_bounded_pipelines(self, segments: Sequence[tuple[ReaderUse | None, str | None]]) -> list[ReaderUse]:
        """Collapse one-file complete readers followed by bounded stdin filters."""
        output: list[ReaderUse] = []
        index = 0
        while index < len(segments):
            upstream, separator = segments[index]
            downstream = segments[index + 1][0] if index + 1 < len(segments) else None
            if upstream is not None and downstream is not None and (separator == '|') and (upstream.mode == 'complete') and (len(upstream.operands) == 1) and (upstream.operands[0].classification != 'stdin') and (downstream.mode == 'bounded') and (not tuple((operand for operand in downstream.operands if operand.classification != 'stdin'))):
                output.append(ReaderUse(reader=downstream.reader, mode='bounded', operands=upstream.operands, requested_lines_per_file=downstream.requested_lines_per_file, requested_bytes_per_file=downstream.requested_bytes_per_file, output_capped_by_input=downstream.output_capped_by_input and upstream.reader in _LINE_PRESERVING_COMPLETE_READERS, source='pipeline:' + upstream.reader + '|' + downstream.reader, line_ranges=downstream.line_ranges, address_space=downstream.address_space, reversed_input=upstream.reader == 'tac'))
                index += 2
                continue
            if upstream is not None:
                output.append(upstream)
            index += 1
        return output

    def _unclassified(self, reason_code: str, reason: str, readers: Sequence[ReaderUse], policy: Mapping[str, Any]) -> IntakeDecision:
        behavior = policy['unknown_expansion']['unclassified_behavior']
        if behavior == 'deny':
            return self._decision('deny', reason_code, reason + ' The explicit policy denies unclassified reads.', readers, policy)
        return self._decision('audit', reason_code, reason + ' The call is allowed with audit and remains protected by the PostToolUse output bound.', readers, policy)

    def _detect_structural_denial(self, command: str, tokens: Sequence[str], policy: Mapping[str, Any]) -> tuple[str, str] | None:
        reader_names = self._reader_names(policy)
        basenames = [self._basename(token) for token in tokens]
        has_reader = any((token in reader_names for token in basenames)) or any(
            re.search(r'\b' + re.escape(name) + r'\b', command) for name in reader_names)
        if policy['unknown_expansion']['deny_obvious_interpreter_bypass']:
            if any((re.search(pattern, command) for pattern in policy['readers']['interpreter_bypass_patterns'])) and ('interpreter_metadata_patterns' not in policy['readers'] or not any((re.fullmatch(pattern, command) for pattern in policy['readers']['interpreter_metadata_patterns']))):
                return ('unsupported_interpreter_read_output', 'Unsupported interpreter read/output shape; use content-read.py ranges or the documented HTTP API.')
        segments = self._segments(tokens)
        if policy['unknown_expansion']['deny_eval_readers']:
            for segment in segments:
                stripped = self._strip_prefixes(segment)
                if not stripped or self._basename(stripped[0]) != 'eval':
                    continue
                nested_text = ' '.join(stripped[1:])
                if any((re.search(f'\\b{re.escape(name)}\\b', nested_text) for name in reader_names)):
                    return ('eval_reader', 'Blocked repository document intake: eval hides content-reader arguments from direct classification. Run the reader directly.')
        if not has_reader:
            return None
        if policy['unknown_expansion']['deny_command_substitutions']:
            reader_pattern = '|'.join((re.escape(name) for name in sorted(reader_names)))
            if any(marker in command for marker in ('$(', '`', '<(', '>(')):
                return ('reader_command_substitution', 'Blocked repository document intake: a content reader uses command or process substitution that can expand to an unknown file set. Resolve paths first, then read one explicit file or bounded excerpts.')
        if policy['unknown_expansion']['deny_reader_loops']:
            has_loop = any((stripped and self._basename(stripped[0]) in {'for', 'while', 'until'} for segment in segments if (stripped := self._strip_prefixes(segment))))
            if has_loop:
                return ('reader_loop', 'Blocked repository document intake: a shell loop can concatenate an unknown number of files. Discover paths first, then read one complete file or explicit bounded excerpts.')
        if policy['unknown_expansion']['deny_find_exec_readers']:
            for segment in segments:
                stripped = self._strip_prefixes(segment)
                if not stripped or self._basename(stripped[0]) != 'find':
                    continue
                names = [self._basename(token) for token in stripped[1:]]
                if any((token in {'-exec', '-execdir'} for token in stripped[1:])) and any((name in reader_names for name in names)):
                    return ('find_exec_reader', 'Blocked repository document intake: find -exec can concatenate an unbounded file set. Use find or rg only for discovery, then read selected files separately.')
        if policy['unknown_expansion']['deny_xargs_readers']:
            for segment in segments:
                stripped = self._strip_prefixes(segment)
                if not stripped or self._basename(stripped[0]) != 'xargs':
                    continue
                names = [self._basename(token) for token in stripped[1:]]
                if any((name in reader_names for name in names)):
                    return ('xargs_reader', 'Blocked repository document intake: xargs can feed an unbounded file set to a content reader. Use discovery output to select one file or bounded excerpts.')
        if policy['unknown_expansion']['deny_globs'] and re.search('\\$\\(\\s*find\\b|`\\s*find\\b', command):
            return ('unbounded_file_expansion', 'Blocked repository document intake: command substitution based on find can expand to an unbounded file set. Discover paths first and read selected files explicitly.')
        return None

    def _collect_reader_uses(self, command: str, *, cwd: Path, repository_root: Path, policy: Mapping[str, Any], depth: int, cwd_invalidated: bool = False) -> tuple[list[ReaderUse], IntakeDecision | None]:
        try:
            tokens = self._shell_tokens(command)
        except ValueError as error:
            return ([], self._unclassified('shell_parse_failed', f'Repository document intake could not classify the Bash command safely: {error}.', (), policy))
        structural = self._detect_structural_denial(command, tokens, policy)
        if structural is not None:
            code, reason = structural
            return ([], self._decision('deny', code, reason, (), policy))
        reader_names = self._reader_names(policy)
        nested_shells = set(policy['readers']['nested_shells'])
        readers: list[ReaderUse] = []
        classified_segments: list[tuple[ReaderUse | None, str | None]] = []
        changed_cwd = cwd_invalidated
        for position, (segment, separator_after) in enumerate(self._segments_with_separators(tokens)):
            stripped = self._strip_prefixes(segment)
            if not stripped:
                classified_segments.append((None, separator_after))
                continue
            executable = self._basename(stripped[0])
            arguments = stripped[1:]
            if executable == 'cd' and position > 0:
                changed_cwd = True
            if executable in nested_shells:
                command_index = next((index for index, token in enumerate(arguments) if token in {'-c', '-lc', '-cl'}), None)
                if command_index is None or command_index + 1 >= len(arguments):
                    classified_segments.append((None, separator_after))
                    continue
                if depth >= policy['parser']['max_nested_shell_depth']:
                    return (readers, self._decision('deny', 'nested_shell_depth_exceeded', 'Nested shell classification budget exceeded.', readers, policy))
                if not policy['unknown_expansion']['deny_nested_shell_readers']:
                    nested_readers, nested_decision = self._collect_reader_uses(
                        arguments[command_index + 1], cwd=cwd, repository_root=repository_root,
                        policy=policy, depth=depth + 1, cwd_invalidated=changed_cwd)
                    if nested_decision is not None:
                        return (readers, nested_decision)
                    readers.extend(nested_readers)
                    classified_segments.extend((use, '&&') for use in nested_readers)
                    continue
                if policy['unknown_expansion']['deny_nested_shell_readers']:
                    nested_command = arguments[command_index + 1]
                    if depth >= policy['parser']['max_nested_shell_depth']:
                        return (readers, self._decision('deny', 'nested_shell_depth_exceeded', 'Blocked repository document intake: nested shell depth exceeds the configured classifier budget. Run the content reader directly.', readers, policy))
                    nested_readers, nested_decision = self._collect_reader_uses(nested_command, cwd=cwd, repository_root=repository_root, policy=policy, depth=depth + 1, cwd_invalidated=changed_cwd)
                    if nested_decision is not None and nested_decision.reason_code == 'nested_shell_depth_exceeded':
                        return (readers, nested_decision)
                    if nested_decision is not None and nested_decision.decision == 'deny':
                        return (readers, self._decision('deny', 'nested_shell_reader', 'Blocked repository document intake: a nested shell contains a disallowed content-read bundle. Run the reader directly so the repository policy can classify it precisely. ' + nested_decision.reason, (*readers, *nested_readers), policy))
                    if nested_readers:
                        return (readers, self._decision('deny', 'nested_shell_reader', 'Blocked repository document intake: a nested shell hides content readers from direct classification. Run the reader directly and use one complete file or explicit bounded excerpts.', (*readers, *nested_readers), policy))
                classified_segments.append((None, separator_after))
                continue
            if executable not in reader_names:
                classified_segments.append((None, separator_after))
                continue
            if len(arguments) > policy['parser']['max_reader_arguments_per_invocation']:
                return (readers, self._decision('deny', 'reader_argument_budget_exceeded', 'Blocked repository document intake: one content-reader invocation exceeds the configured argument-classification budget. Split the read into one explicit file or bounded excerpts.', readers, policy))
            if len(readers) >= policy['parser']['max_reader_invocations_per_call']:
                return (readers, self._decision('deny', 'reader_invocation_budget_exceeded', 'Blocked repository document intake: the Bash call exceeds the configured number of content-reader invocations. Narrow the read to one decision at a time.', readers, policy))
            use = self._reader_use(executable, arguments, cwd=cwd, repository_root=repository_root, policy=policy)
            if use is not None:
                if changed_cwd and any(operand.relative for operand in use.operands):
                    return ([], self._decision('deny', 'unresolved_execution_cwd',
                        'A later directory change invalidates the proven relative source base.', (), policy))
                readers.append(use)
                classified_segments.append((use, separator_after))
            else:
                classified_segments.append((None, separator_after))
        return (self._collapse_bounded_pipelines(classified_segments), None)

    def _sum_known(self, values: Sequence[int | None]) -> int | None:
        if any((value is None for value in values)):
            return None
        return sum((value for value in values if value is not None))

    def _complete_metrics(self, readers: Sequence[ReaderUse]) -> tuple[list[FileOperand], int | None, int | None]:
        operands = [operand for reader in readers if reader.mode in {'complete', 'unknown'} for operand in reader.operands if operand.classification != 'stdin']
        return (operands, self._sum_known([operand.size_bytes for operand in operands]), self._sum_known([operand.line_count if operand.line_count_exact is True else None for operand in operands]))

    def _effective_bounded_lines(self, reader: ReaderUse, operand: FileOperand, index: int) -> int | None:
        ranges = reader.source_ranges(index)
        if ranges is not None:
            return sum(last - first + 1 for first, last in ranges)
        requested = reader.requested_lines_per_file
        if requested is None:
            return None
        if reader.output_capped_by_input and operand.line_count is not None and (operand.line_count_exact is True):
            return min(requested, operand.line_count)
        return requested

    def _effective_bounded_bytes(self, reader: ReaderUse, operand: FileOperand, index: int) -> int | None:
        requested = reader.requested_bytes_per_file
        if requested is None:
            return self.facts.projected_bytes(reader, operand, index)
        if operand.size_bytes is not None:
            return min(requested, operand.size_bytes)
        return requested

    def _bounded_projections(self, readers: Sequence[ReaderUse]) -> list[tuple[ReaderUse, FileOperand, int | None, int | None]]:
        return [(reader, operand, self._effective_bounded_lines(reader, operand, index), self._effective_bounded_bytes(reader, operand, index)) for reader in readers if reader.mode == 'bounded' for index, operand in enumerate(reader.operands) if operand.classification != 'stdin']

    def _bounded_metrics(self, readers: Sequence[ReaderUse]) -> tuple[list[FileOperand], int | None, int | None, list[tuple[ReaderUse, FileOperand, int | None, int | None]]]:
        projections = self._bounded_projections(readers)
        return ([operand for _, operand, _, _ in projections], self._sum_known([byte_count for _, _, _, byte_count in projections]), self._sum_known([line_count for _, _, line_count, _ in projections]), projections)

    def classify_command(self, command: str, *, cwd: Path, repository_root: Path, policy: Mapping[str, Any]) -> IntakeDecision:
        """Classify one Bash command without executing it."""
        if not isinstance(command, str) or command.strip() == '':
            raise ValueError('Bash tool_input.command must be a non-empty string')
        readers, immediate = self._collect_reader_uses(command, cwd=cwd, repository_root=repository_root, policy=policy, depth=0)
        if immediate is not None:
            return immediate
        if not readers:
            return self._decision('allow', 'no_content_reader', 'No repository content reader was detected.', (), policy, projected_files=0, projected_bytes=0, projected_lines=0)
        all_operands = [operand for reader in readers for operand in reader.operands if operand.classification != 'stdin']
        if not all_operands:
            return self._decision('allow', 'stdin_only_reader', 'The content reader consumes standard input and does not load a repository file.', readers, policy, projected_files=0, projected_bytes=0, projected_lines=0)
        glob_operands = [operand for operand in all_operands if operand.classification == 'glob']
        if glob_operands and policy['unknown_expansion']['deny_globs']:
            return self._decision('deny', 'unbounded_file_expansion', 'Blocked repository document intake: a content reader uses a glob or brace expansion that can concatenate an unknown file set. Discover matching paths first, then read selected files explicitly.', readers, policy)
        dynamic_operands = [operand for operand in all_operands if operand.classification in {'dynamic', 'missing', 'unreadable'}]
        if dynamic_operands:
            if len(all_operands) > 1 or len(readers) > 1:
                unresolved_kinds = {operand.classification for operand in dynamic_operands}
                recovery = (('dynamic', 'dynamic path; use an explicit path or split this read'), ('missing', 'missing file; check path and working directory'), ('unreadable', 'unreadable file; check access or contact an administrator'))
                details = '; '.join((advice for kind, advice in recovery if kind in unresolved_kinds))
                return self._decision('deny', 'unresolved_multi_file_read', f'Blocked repository document intake: {details}.', readers, policy)
            return self._unclassified('dynamic_single_file_read', 'One reader operand could not be resolved before execution.', readers, policy)
        if any(reader.mode == 'bounded' and reader.requested_bytes_per_file is None
               and (reader.reversed_input or reader.address_space == 'stream')
               and reader.source_ranges(index) is None
               for reader in readers for index, operand in enumerate(reader.operands)
               if operand.classification != 'stdin'):
            return self._unclassified('unresolved_source_projection',
                'The observed source does not prove transformed output coordinates.', (), policy)
        complete_operands, complete_bytes, complete_lines = self._complete_metrics(readers)
        bounded_operands, bounded_bytes, bounded_lines, bounded_projections = self._bounded_metrics(readers)
        if complete_operands:
            total_operations = len(complete_operands) + len(bounded_operands)
            if total_operations <= policy['complete_reads']['max_files_per_call']:
                return self._decision('allow', 'single_complete_file', 'One complete file read is within the repository intake policy.', readers, policy, projected_files=total_operations, projected_bytes=complete_bytes, projected_lines=complete_lines)
            small = policy['complete_reads']
            all_complete_known = complete_bytes is not None and complete_lines is not None
            no_bounded_unknown = not bounded_operands
            if no_bounded_unknown and all_complete_known and (total_operations <= small['small_bundle_max_files']) and (complete_bytes <= small['small_bundle_max_bytes']) and (complete_lines <= small['small_bundle_max_lines']):
                return self._decision('allow', 'small_complete_bundle', 'The complete-file bundle is within the configured small-file budget.', readers, policy, projected_files=total_operations, projected_bytes=complete_bytes, projected_lines=complete_lines)
            labels = self._reason_labels(readers, policy)
            return self._decision('deny', 'multiple_complete_documents', f'Blocked repository document intake: this Bash call would read {total_operations} complete or mixed file inputs ({labels}). ' + _DISCOVERY_FIRST_GUIDANCE, readers, policy, projected_files=total_operations, projected_bytes=complete_bytes, projected_lines=complete_lines)
        bounded = policy['bounded_reads']
        unique_labels = {operand.label for operand in bounded_operands if operand.classification != 'stdin'}
        if len(unique_labels) > bounded['max_files_per_call']:
            return self._decision('deny', 'bounded_file_budget_exceeded', f'Blocked repository document intake: bounded excerpts target {len(unique_labels)} files, exceeding the configured maximum of {bounded['max_files_per_call']} files per Bash call. ' + _DISCOVERY_FIRST_GUIDANCE, readers, policy, projected_files=len(unique_labels), projected_bytes=bounded_bytes, projected_lines=bounded_lines)
        for reader, operand, effective_lines, effective_bytes in bounded_projections:
            if effective_lines is not None and effective_lines > bounded['max_lines_per_file']:
                return self._decision('deny', 'bounded_per_file_line_budget_exceeded', f'Blocked repository document intake: the excerpt for {operand.label} has an effective upper bound of {effective_lines} lines, exceeding the configured maximum of {bounded['max_lines_per_file']}. ' + _DISCOVERY_FIRST_GUIDANCE, readers, policy, projected_files=len(unique_labels), projected_bytes=bounded_bytes, projected_lines=bounded_lines)
            if effective_bytes is not None and effective_bytes > bounded['max_bytes_per_file']:
                return self._decision('deny', 'bounded_per_file_byte_budget_exceeded', f'Blocked repository document intake: the excerpt for {operand.label} has an effective upper bound of {effective_bytes} bytes, exceeding the configured maximum of {bounded['max_bytes_per_file']}. ' + _DISCOVERY_FIRST_GUIDANCE, readers, policy, projected_files=len(unique_labels), projected_bytes=bounded_bytes, projected_lines=bounded_lines)
        if bounded_lines is not None and bounded_lines > bounded['max_total_lines']:
            return self._decision('deny', 'bounded_total_line_budget_exceeded', f'Blocked repository document intake: bounded excerpts request {bounded_lines} total lines, exceeding the configured maximum of {bounded['max_total_lines']}. ' + _DISCOVERY_FIRST_GUIDANCE, readers, policy, projected_files=len(unique_labels), projected_bytes=bounded_bytes, projected_lines=bounded_lines)
        if bounded_bytes is not None and bounded_bytes > bounded['max_total_bytes']:
            return self._decision('deny', 'bounded_total_byte_budget_exceeded', f'Blocked repository document intake: bounded excerpts request {bounded_bytes} total bytes, exceeding the configured maximum of {bounded['max_total_bytes']}. ' + _DISCOVERY_FIRST_GUIDANCE, readers, policy, projected_files=len(unique_labels), projected_bytes=bounded_bytes, projected_lines=bounded_lines)
        return self._decision('allow', 'bounded_excerpts_within_budget', 'Explicit bounded excerpts are within the repository intake budget.', readers, policy, projected_files=len(unique_labels), projected_bytes=bounded_bytes, projected_lines=bounded_lines)
