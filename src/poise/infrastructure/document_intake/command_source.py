"""Explicit filesystem observations for the pure shell classifier and ledger."""
import hashlib
from pathlib import Path, PurePath
import re
import shlex

from ...modules.document_intake.classification import CommandClassifier, FileOperand
from ...modules.document_intake.command import CommandFailure, validate_plan
from ...modules.document_intake.coverage import union_ranges
from ...modules.document_intake.domain import identity


def execution_cwd(command):
    """Only a literal successful leading cd proves a relative execution base."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>()')
        lexer.whitespace_split = True
        lexer.commenters = ''
        tokens = list(lexer)
    except ValueError:
        return None
    if tokens and tokens[0] == 'cd':
        offset = 2 if len(tokens) > 1 and tokens[1] == '--' else 1
        if (len(tokens) > offset + 1 and tokens[offset + 1] == '&&'
                and Path(tokens[offset]).is_absolute()
                and not re.search(r'[$`*?\[{}]', tokens[offset])):
            return Path(tokens[offset])
    return None


class OperandSnapshot:
    """Pure lookups. Discovery records lexical inputs, never touches the filesystem."""
    def __init__(self):
        self.requests, self.observed, self.lines = {}, {}, {}

    def operand(self, token, *, cwd, repository_root, line_limit):
        key = (token, str(cwd), str(repository_root), line_limit)
        if key in self.observed:
            return self.observed[key]
        self.requests[key] = key
        if token in ('-', '/dev/null'):
            return FileOperand('stdin', '<stdin>', None, None, None)
        if re.search(r'[*?\[]|\{[^{}]*,[^{}]*\}|[$`]|[<>]\(', token):
            return FileOperand('glob', '<unresolved:' + identity(token) + '>', None, None, None)
        path = PurePath(token) if PurePath(token).is_absolute() else PurePath(cwd) / token
        return FileOperand('explicit', str(path), path, None, None,
                           relative=not PurePath(token).is_absolute())

    def projected_bytes(self, reader, operand, index):
        lines = self.lines.get(operand.path)
        if lines is None:
            return None
        projection = reader.source_projection(index)
        if projection is None:
            return None
        ranges = projection.line_ranges
        if any(last > len(lines) for _, last in ranges) and not operand.line_count_exact:
            return None
        return sum(len(line) for first, last in ranges for line in lines[first - 1:last])


class CommandSource:
    def __init__(self, checkout, policy, *, cwd_proved=True):
        self.checkout, self.policy = Path(checkout), policy
        self.cwd_proved = cwd_proved
        self.snapshot = OperandSnapshot()
        self.versions = {}

    def version(self, path):
        path = Path(path)
        first = path.stat()
        if first.st_size <= self.policy['registry']['versioning']['hash_max_bytes']:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            version = dict(size=first.st_size, sha256=digest)
        else:
            version = dict(size=first.st_size, mtime_ns=first.st_mtime_ns)
        last = path.stat()
        if (first.st_size, first.st_mtime_ns) != (last.st_size, last.st_mtime_ns):
            raise CommandFailure('unstable_version')
        return version

    def _observe(self, key):
        token, cwd, root, limit = key
        symbolic = self.snapshot.operand(token, cwd=cwd, repository_root=root, line_limit=limit)
        if symbolic.path is None:
            return symbolic
        path = Path(symbolic.path).resolve()
        if not path.is_relative_to(self.checkout.resolve()):
            return FileOperand('unreadable', '<outside-checkout>', None, None, None)
        label = path.relative_to(self.checkout.resolve()).as_posix()
        try:
            version = self.version(path)
            lines = []
            with path.open('rb') as stream:
                for line in stream:
                    lines.append(line)
                    if len(lines) > limit:
                        break
            if self.version(path) != version:
                raise CommandFailure('unstable_version')
        except FileNotFoundError:
            return FileOperand('missing', label, PurePath(path), None, None)
        except (OSError, CommandFailure):
            return FileOperand('unreadable', label, PurePath(path), None, None)
        exact = len(lines) <= limit
        terminated = (not lines or lines[-1].endswith(b'\n')) if exact else None
        operand = FileOperand('explicit', label, PurePath(path), version['size'],
                              len(lines), exact, symbolic.relative, terminated)
        self.snapshot.lines[operand.path] = lines
        self.versions[operand.path] = version
        return operand

    def classify(self, command, cwd):
        classifier = CommandClassifier(self.snapshot)
        _, immediate = classifier._collect_reader_uses(command, cwd=PurePath(cwd),
                                                      repository_root=PurePath(self.checkout),
                                                      policy=self.policy['classifier'], depth=0)
        if immediate is not None:
            return immediate
        if not self.cwd_proved and any(not PurePath(key[0]).is_absolute()
                                      and key[0] != '-' for key in self.snapshot.requests):
            return classifier._decision('deny', 'unresolved_execution_cwd',
                                        'A literal successful absolute cd is required for relative reader paths.',
                                        (), self.policy['classifier'])
        for key in list(self.snapshot.requests):
            self.snapshot.observed[key] = self._observe(key)
        return classifier.classify_command(command, cwd=PurePath(cwd),
                                           repository_root=PurePath(self.checkout),
                                           policy=self.policy['classifier'])

    def plans(self, readers, command_digest, generation, *, origin='native'):
        plans = []
        for reader in readers:
            for index, operand in enumerate(reader.operands):
                if operand.classification != 'explicit' or operand.path not in self.versions:
                    continue
                projected = reader.source_projection(index)
                if reader.mode == 'bounded' and projected is not None and not projected.line_ranges:
                    continue
                ranges = [list(r) for r in projected.line_ranges] if projected is not None else []
                kind = 'complete' if reader.mode == 'complete' else 'operation'
                if ranges and kind != 'complete':
                    kind = 'lines'
                version = self.versions[operand.path]
                operation = identity([reader.reader, reader.mode, reader.source,
                                      ranges, reader.requested_bytes_per_file,
                                      command_digest if kind == 'operation' else None])
                plan = dict(schema='poise-read-plan-1', origin=origin, reader_kind=reader.reader,
                            coverage_kind=kind, line_ranges=union_ranges(ranges), version=version,
                            operation_digest=operation, generation=generation,
                            path=operand.label, version_key=identity(version))
                plans.append(validate_plan(plan))
        return plans

    def current(self, plan):
        try:
            path = self.checkout / plan['path']
            first = path.stat()
            if 'sha256' in plan['version']:
                version = dict(size=first.st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                version = dict(size=first.st_size, mtime_ns=first.st_mtime_ns)
            last = path.stat()
            return ((first.st_size, first.st_mtime_ns) == (last.st_size, last.st_mtime_ns)
                    and version == plan['version'])
        except (OSError, CommandFailure):
            return False

    def direct_version(self, operand):
        path = (self.checkout / operand).resolve()
        if not path.is_relative_to(self.checkout.resolve()):
            raise CommandFailure('path_outside_project')
        return self.version(path)

    def direct_plan(self, prepared, generation, version):
        complete = prepared.normalized_range == 'complete'
        if complete:
            ranges = [[1, prepared.content_lines]] if prepared.content_lines else []
        else:
            first, last = prepared.normalized_range.removeprefix('lines:').split('-')
            ranges = [[int(first), int(last)]]
        return validate_plan(dict(schema='poise-read-plan-1', origin='direct',
                                  reader_kind='repository-content-read', coverage_kind='complete' if complete else 'lines',
                                  line_ranges=ranges, version=version,
                                  operation_digest=identity(['repository-content-read', prepared.normalized_range]),
                                  generation=generation, path=prepared.path, version_key=identity(version)))
