"""ERP selected-text semantics, bound to the selected project checkout."""
import hashlib
from pathlib import Path
from ...modules.document_intake.contracts import ReadFailure
from ...modules.document_intake.domain import PreparedContent, Selection


class ProjectContentSource:
    def select(self, checkout, item, policy):
        key, selected = item['key'], Selection.parse(item['selection'])
        path = Path(item['path'])
        try:
            path = (path if path.is_absolute() else checkout / path).resolve()
            if not path.is_relative_to(checkout):
                raise ReadFailure('path_outside_project', key)
            if not path.exists():
                raise ReadFailure('source_missing', key)
            if not path.is_file():
                raise ReadFailure('source_not_file', key)
            if selected.kind == 'complete':
                limits = policy['complete']
                if path.stat().st_size > limits['max_bytes']:
                    raise ReadFailure('complete_limit', key)
                content = path.read_text(encoding='utf-8')
                lines = len(content.splitlines())
                normalized = 'complete'
                if len(content.encode()) > limits['max_bytes'] or lines > limits['max_lines']:
                    raise ReadFailure('complete_limit', key)
            else:
                limits = policy['bounded']
                if selected.end - selected.start + 1 > limits['max_lines']:
                    raise ReadFailure('bounded_limit', key)
                parts, size = [], 0
                with path.open('r', encoding='utf-8', newline='') as stream:
                    for number, line in enumerate(stream, 1):
                        if number < selected.start:
                            continue
                        if number > selected.end:
                            break
                        size += len(line.encode())
                        if size > limits['max_bytes']:
                            raise ReadFailure('bounded_limit', key)
                        parts.append(line)
                if not parts:
                    raise ReadFailure('range_after_eof', key)
                content, lines = ''.join(parts), len(parts)
                normalized = f"lines:{selected.start}-{selected.start + lines - 1}"
        except UnicodeError as exc:
            raise ReadFailure('source_not_utf8', key) from exc
        except OSError as exc:
            raise ReadFailure('source_unreadable', key) from exc
        raw = content.encode('utf-8')
        return PreparedContent(key, path.relative_to(checkout).as_posix(), content, normalized,
                               hashlib.sha256(raw).hexdigest(), len(raw), lines)
