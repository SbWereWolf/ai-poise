"""AI-poise Markdown fallback, adapted from ERP ai-assistant/md-fmt.py.

No file discovery, shell wrapper, project configuration or global path changes.
"""
import os
from pathlib import Path
import re
import tempfile

from ..common import PoiseError
from .test_packages import AiPoiseTestPackages

def split_lines(text: str) -> list[str]:
    return re.split(r"\r\n|\n|\r", text)


def is_table_line(line: str) -> bool:
    trimmed = line.strip()
    return bool(trimmed) and (trimmed.startswith("|") or trimmed.count("|") >= 2)


def should_skip_line(line: str) -> bool:
    return (
        line == ""
        or line.strip() == ""
        or re.match(r"^\s{0,3}#{1,6}(?:\s|$)", line) is not None
        or is_table_line(line)
    )


def find_break_index(text: str, limit: int) -> int:
    if len(text) <= limit or limit < 1:
        return -1
    for index in range(min(limit, len(text) - 1), -1, -1):
        if text[index].isspace():
            return index
    for index in range(limit + 1, len(text)):
        if text[index].isspace():
            return index
    return -1


def wrap_with_prefix(content: str, first_prefix: str, next_prefix: str, width: int) -> list[str]:
    lines: list[str] = []
    prefix = first_prefix
    remaining = content
    while True:
        if len(prefix) + len(remaining) <= width:
            lines.append(prefix + remaining)
            return lines
        available = width - len(prefix)
        if available < 1:
            lines.append(prefix + remaining)
            return lines
        break_at = find_break_index(remaining, available)
        if break_at < 0:
            lines.append(prefix + remaining)
            return lines
        head = remaining[:break_at].rstrip()
        tail = remaining[break_at + 1 :].lstrip()
        if head == "":
            lines.append(prefix + remaining)
            return lines
        lines.append(prefix + head)
        remaining = tail
        prefix = next_prefix


def wrap_line(line: str, width: int) -> list[str]:
    if len(line) <= width or should_skip_line(line):
        return [line]
    match = re.match(r"^(\s*)((?:[-+*])\s+|(?:\d+[.)])\s+)(.*)$", line)
    if match:
        first_prefix = match.group(1) + match.group(2)
        return wrap_with_prefix(match.group(3), first_prefix, " " * len(first_prefix), width)
    prefix_match = re.match(r"^[ \t]*", line)
    prefix = prefix_match.group(0) if prefix_match else ""
    return wrap_with_prefix(line[len(prefix) :], prefix, prefix, width)


def format_markdown(text: str, width: int, newline: str) -> str:
    if type(width) is not int or width <= 0 or newline not in ('\n', '\r', '\r\n'):
        raise PoiseError('Explicit positive width and LF/CR/CRLF newline required')
    final = text.endswith(('\n', '\r'))
    lines = split_lines(text)
    if final: lines.pop()
    output, fence, front = [], None, False
    for index, line in enumerate(lines):
        if index == 0 and line.lstrip('\ufeff').strip() == '---':
            front = True; output.append(line); continue
        if front:
            output.append(line)
            if line.strip() in ('---', '...'): front = False
            continue
        if fence is not None:
            output.append(line)
            close = re.fullmatch(r' {0,3}([`~]+)\s*', line)
            if close and set(close[1]) == {fence[0]} and len(close[1]) >= fence[1]: fence = None
            continue
        opened = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if opened:
            fence = (opened[1][0], len(opened[1])); output.append(line); continue
        # Do not reflow indented code or blockquote structures without a parser.
        if line.startswith(('    ', '\t')) or re.match(r'^ {0,3}>', line):
            output.append(line); continue
        hardbreak = len(line) - len(line.rstrip(' ')) >= 2
        content = line.rstrip(' ') if hardbreak else line
        wrapped = wrap_line(content, width)
        if hardbreak: wrapped[-1] += line[len(content):]
        output.extend(wrapped)
    return newline.join(output) + (newline if final else '')


def format_files(checkout, files, width, eol, mode, fallback_reason):
    if type(width) is not int or width <= 0 or eol not in ('LF', 'CR', 'CRLF') or mode not in ('check', 'fix'):
        raise PoiseError('Explicit width, EOL and check/fix mode required')
    if not isinstance(fallback_reason, str) or not fallback_reason.strip():
        raise PoiseError('Record why preferred IDE formatting is unavailable or unsuitable')
    if not isinstance(files, list) or not files or any(not isinstance(v, str) or not v for v in files):
        raise PoiseError('Explicit nonempty file paths required')
    directory = AiPoiseTestPackages._assert_ai_poise_checkout(Path(checkout))
    newline = {'LF': '\n', 'CR': '\r', 'CRLF': '\r\n'}[eol]
    prepared, seen = [], set()
    for value in files:
        original = Path(value)
        path = original.resolve()
        if original.is_symlink() or not path.is_relative_to(directory) or not path.is_file() or path.suffix.lower() != '.md':
            raise PoiseError(f'Expected explicit regular AI-poise Markdown file: {value}')
        if path in seen: continue
        seen.add(path)
        try:
            before = path.read_bytes()
            if len(before) > 8 * 1024 * 1024: raise PoiseError('Markdown file exceeds 8 MiB bound')
            after = format_markdown(before.decode('utf-8'), width, newline).encode('utf-8')
            prepared.append((path, before, after, path.stat()))
        except (OSError, UnicodeError) as exc:
            raise PoiseError(f'Cannot read UTF-8 Markdown: {value}: {exc}') from exc
    drift = [str(path) for path, before, after, _ in prepared if before != after]
    changed = []
    if mode == 'fix':
        for path, before, after, stamp in prepared:
            if before == after: continue
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(prefix='.poise-md-', dir=path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(after); stream.flush(); os.fsync(stream.fileno())
                os.chmod(temporary, stamp.st_mode & 0o777)
                if path.is_symlink() or path.stat().st_ino != stamp.st_ino or path.read_bytes() != before:
                    raise PoiseError(f'Markdown changed before replace: {path}; already changed: {changed}')
                os.replace(temporary, path)
                changed.append(str(path))
            except OSError as exc:
                raise PoiseError(f'Markdown write failed: {path}; already changed: {changed}: {exc}') from exc
            finally:
                if temporary is not None: temporary.unlink(missing_ok=True)
    return {'status': 'drift' if mode == 'check' and drift else 'ok',
            'mode': mode, 'drift': drift, 'changed': changed, 'files': [str(p[0]) for p in prepared],
            'fallback_reason': fallback_reason}
