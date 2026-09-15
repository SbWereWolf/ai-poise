"""Structural checks for AI-poise's shipped docs; never a semantic skill evaluator.

The file-link owner extracted from the existing delivery namespace check is shared
by documentation tests and the skill-reference audit. No project/runtime policy
is loaded from the inspected checkout and no referenced executable is run.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import fnmatch
import html
import os
import unicodedata
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from .skill_catalog import load_skill_catalog


def prose(text: str) -> str:
    """Mask code/front matter/comments while preserving list continuation links."""
    result = []
    fence = None
    front = False
    list_indents = []
    for number, line in enumerate(text.splitlines(keepends=True)):
        masked = re.sub(r'[^\n]', ' ', line)
        stripped = line.strip()
        if number == 0 and stripped == '---':
            front = True
            result.append(masked)
            continue
        if front:
            if stripped == '---':
                front = False
            result.append(masked)
            continue
        indent = len(line) - len(line.lstrip(' '))
        if stripped:
            while list_indents and indent < list_indents[-1]:
                list_indents.pop()
        base = list_indents[-1] if list_indents else 0
        bullet = re.match(r'^ *(?:[-+*]|[0-9]+[.)])[ \t]+', line)
        if bullet and fence is None:
            list_indents.append(bullet.end())
            base = bullet.end()
        relative = line[base:] if len(line) >= base else line
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', relative)
        if fence is not None:
            if (marker and marker[1][0] == fence[0]
                    and len(marker[1]) >= len(fence) and not marker[2].strip()):
                fence = None
            result.append(masked)
            continue
        if marker:
            fence = marker[1]
            result.append(masked)
            continue
        indented_code = not bullet and (indent >= base + 4 or line.startswith('\t'))
        result.append(masked if indented_code else line)
    return re.sub(r'<!--[\s\S]*?-->',
                  lambda m: re.sub(r'[^\n]', ' ', m[0]), ''.join(result))


def _unescape(value: str) -> str:
    return re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])', r'\1', value)


def _destination(text: str, offset: int) -> tuple[str, int]:
    i = offset
    while i < len(text) and text[i].isspace(): i += 1
    if i < len(text) and text[i] == '<':
        end = text.find('>', i + 1)
        return (text[i+1:end], end+1) if end >= 0 else ('', i)
    start = i; depth = 0
    while i < len(text):
        char = text[i]
        if char == '\\' and i+1 < len(text): i += 2; continue
        if char == '(':
            depth += 1
        elif char == ')':
            if depth == 0: break
            depth -= 1
        elif char.isspace() and depth == 0: break
        i += 1
    return _unescape(text[start:i]), i


@dataclass(frozen=True)
class Link:
    target: str | None
    line: int
    context: str
    kind: str = 'markdown'


def links(text: str) -> list[Link]:
    visible = prose(text)
    # Code literals are not Markdown links (see explicit reference literals below).
    visible = re.sub(r'(`+)([^`\n]*?)\1', lambda m:' '*len(m[0]), visible)
    definitions = {}
    def_lines = []
    for m in re.finditer(r'^ {0,3}\[([^\]\n]+)\]:[ \t]*(.*)$', visible, re.M):
        target, _ = _destination(m[2], 0)
        definitions[' '.join(m[1].split()).casefold()] = target
        def_lines.append((m.start(), m.end()))
    result = []; consumed = list(def_lines)
    def add(target, offset, kind='markdown'):
        line = visible.count('\n', 0, offset)+1
        context = text.splitlines()[line-1].strip()[:400]
        result.append(Link(target, line, context, kind))
    for m in re.finditer(r'!?\[([^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*)\]\(', visible):
        target, end = _destination(visible, m.end())
        if target:
            add(target, m.start()); consumed.append((m.start(), end))
    def free(offset): return not any(a <= offset < b for a,b in consumed)
    for m in re.finditer(r'!?\[([^\]\n]+)\]\[([^\]\n]*)\]', visible):
        if free(m.start()):
            key = ' '.join((m[2] or m[1]).split()).casefold()
            add(definitions.get(key), m.start(), 'reference_label')
            consumed.append((m.start(), m.end()))
    for m in re.finditer(r'(?<!!)\[([^\]\n]+)\]', visible):
        key = ' '.join(m[1].split()).casefold()
        if free(m.start()) and key in definitions:
            add(definitions[key], m.start(), 'reference_label')
    return sorted(result, key=lambda x:x.line)


def document_anchors(text: str) -> set[str]:
    """IDs for supported ATX/Setext Markdown headings and explicit HTML anchors.

    This is deliberately a structural checker, not a Markdown rendering engine.
    HTML headings need an explicit id; other renderer extensions are not inferred.
    """
    visible = prose(text)
    result = {
        html.unescape(match[2])
        for match in re.finditer(r"<[^>]+?\b(?:id|name)\s*=\s*([\"'])(.*?)\1", visible)
    }
    heading_ids: set[str] = set()
    lines = visible.splitlines()
    for number, line in enumerate(lines):
        atx = re.match(r'^ {0,3}#{1,6}(?:[ \t]+(.*)|$)', line)
        setext = (number + 1 < len(lines)
                  and re.fullmatch(r' {0,3}(?:=+|-+)[ \t]*', lines[number + 1])
                  and bool(line.strip()) and not line.startswith(('    ', '\t')))
        if atx:
            title = re.sub(r'[ \t]+#+[ \t]*$', '', atx[1] or '')
        elif setext:
            title = line.strip()
        else:
            continue
        title = re.sub(r'!?\[([^\]]+)\]\([^)]*\)', r'\1', title)
        title = re.sub(r'<[^>]*>', '', title)
        title = html.unescape(title).strip().lower()
        title = ''.join(char for char in title if char in '-_' or char.isspace()
                        or unicodedata.category(char)[0] in 'LNM')
        slug = re.sub(r'\s', '-', title)
        candidate = slug
        suffix = 1
        while candidate in heading_ids:
            candidate = f'{slug}-{suffix}'
            suffix += 1
        heading_ids.add(candidate)
        result.add(candidate)
    return result


def check_links(checkout: Path, documents: list[Path]) -> dict:
    """Check local files and supported Markdown anchors, read-only and offline."""
    checkout = checkout.resolve()
    errors = []
    edges = []
    skipped = []
    anchor_cache = {}
    for document in sorted(set(documents)):
        document = document.absolute()
        if not document.resolve().is_relative_to(checkout):
            errors.append({'kind': 'outside_checkout', 'source': str(document),
                           'line': 0, 'target': None})
            continue
        source = document.relative_to(checkout).as_posix()
        try:
            text = document.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            errors.append({'kind': 'unreadable_document', 'source': source,
                           'line': 0, 'target': str(exc)})
            continue
        for link in links(text):
            row = {'source': source, 'line': link.line, 'target': link.target}
            if link.target is None:
                errors.append({**row, 'kind': 'undefined_reference'})
                continue
            try:
                url = urlsplit(link.target)
            except ValueError:
                errors.append({**row, 'kind': 'invalid_link_target'})
                continue
            if url.scheme or url.netloc or url.path.startswith('/'):
                skipped.append({**row, 'kind': 'external_link_not_checked'})
                continue
            target = ((document.parent / unquote(url.path)).resolve()
                      if url.path else document.resolve())
            if not target.is_relative_to(checkout):
                errors.append({**row, 'kind': 'outside_checkout'})
                continue
            target_file = target.relative_to(checkout).as_posix()
            row['target_file'] = target_file
            if not target.exists():
                errors.append({**row, 'kind': 'missing_file'})
                continue
            fragment = unquote(url.fragment)
            edges.append({**row, 'target': target_file, 'fragment': fragment,
                          'context': link.context, 'kind': link.kind})
            if not fragment:
                continue
            if target.suffix.lower() != '.md' or not target.is_file():
                skipped.append({**row, 'kind': 'non_markdown_fragment_not_checked'})
                continue
            try:
                if target_file not in anchor_cache:
                    anchor_cache[target_file] = document_anchors(target.read_text(encoding='utf-8'))
                if fragment not in anchor_cache[target_file]:
                    errors.append({**row, 'fragment': fragment, 'kind': 'missing_anchor'})
            except (OSError, UnicodeError) as exc:
                errors.append({**row, 'kind': 'unreadable_target', 'detail': str(exc)})
    return {'errors': errors, 'edges': edges, 'skipped': skipped}


def current_documents(checkout: Path) -> list[Path]:
    """The existing delivery namespace owner's current-doc scope, without caches."""
    checkout = checkout.resolve()
    allowlist = checkout / 'config/legacy-namespace-allowlist.json'
    historical = []
    if allowlist.exists():
        data = json.loads(allowlist.read_text(encoding='utf-8'))
        historical = [rule['path'] for rule in data['rules']
                      if rule['classification'] == 'historical']
    ignored = {'.git', '.venv', 'venv', 'node_modules', '.pytest_cache',
               '__pycache__', '.poise-test-cache'}
    documents = []
    for directory, children, files in os.walk(checkout, followlinks=False):
        children[:] = [name for name in children if name not in ignored
                       and not name.endswith('.egg-info')]
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(checkout).as_posix()
            if (path.suffix.lower() == '.md'
                    and not any(fnmatch.fnmatchcase(relative, pattern) for pattern in historical)):
                documents.append(path)
    return sorted(documents)


def audit_documentation(checkout: Path, changed_paths: list[str] | None = None) -> dict:
    """Include incoming links to changed/deleted targets, not just edited sources."""
    checkout = checkout.resolve()
    if not checkout.is_dir():
        raise ValueError('Documentation checkout directory does not exist')
    documents = current_documents(checkout)
    report = check_links(checkout, documents)
    changed = None
    if changed_paths is not None:
        if not changed_paths:
            raise ValueError('Explicit changed path selection cannot be empty')
        changed = set()
        for value in changed_paths:
            if not value or '\x00' in value:
                raise ValueError('Changed path must be a concrete filesystem path')
            path = (checkout / value).resolve()
            if not path.is_relative_to(checkout):
                raise ValueError('Changed path escapes inspected checkout')
            changed.add(path.relative_to(checkout).as_posix())
        def selected(relative):
            return any(item == '.' or relative == item or relative.startswith(item + '/')
                       for item in changed)
        def relevant(row):
            return selected(row['source']) or selected(row.get('target_file', ''))
        report = {key: [row for row in rows if relevant(row)]
                  for key, rows in report.items()}
    return {**report, 'scanned_documents': len(documents),
            'changed_paths': sorted(changed) if changed is not None else None,
            'read_only': True, 'network_access': False,
            'status': 'invalid' if report['errors'] else 'valid'}


def audit_skills(checkout: Path, selected_ids: list[str] | None = None) -> dict:
    checkout=checkout.resolve()
    catalog=load_skill_catalog(checkout/'.agents/skill-catalog.json', checkout)
    available={skill.id:skill.path for skill in catalog.skills}
    explicit_selection = selected_ids is not None
    if selected_ids is None: selected_ids=list(available)
    if not selected_ids or len(set(selected_ids))!=len(selected_ids) or set(selected_ids)-set(available):
        raise ValueError('Unknown selected skill, empty selection or duplicate skill id')
    roots={available[key] for key in selected_ids}
    all_docs=sorted((checkout/'.agents').rglob('*.md'))
    graph=check_links(checkout, all_docs)
    errors=graph['errors']; edges=graph['edges']; notes=[]
    # Backtick reference paths and explicitly named local NDJSON indexes are data,
    # not executable config. Only bare file tokens in prose are recognized.
    for document in all_docs:
        if not document.resolve().is_relative_to(checkout): continue
        source=document.relative_to(checkout).as_posix()
        for line_no,line in enumerate(prose(document.read_text()).splitlines(),1):
            if source in roots and re.search(r'\balways\s+(?:read|load)\s+all\b',line,re.I):
                notes.append({'source':source,'line':line_no,'kind':'review_loading_language',
                              'context':line.strip(),'semantic_verdict':None})
            for match in re.finditer(r'`([^`\s]+)`',line):
                value=match[1]
                if not (value.endswith('.ndjson') or (value.endswith('.md') and 'references/' in value)):
                    continue
                path=(document.parent/value).resolve()
                if not path.is_relative_to(checkout):
                    errors.append({'kind':'outside_checkout','source':source,'line':line_no,'target':value}); continue
                if not path.is_file():
                    if source in roots:
                        errors.append({'kind':'missing_file','source':source,'line':line_no,'target':value})
                    continue
                edges.append({'source':source,'target':path.relative_to(checkout).as_posix(),'fragment':'',
                              'line':line_no,'context':line.strip(),'kind':'explicit_reference_path'})
    # Only indexes reached from an explicit textual reference are consulted.
    indexes={e['target'] for e in edges if e['target'].endswith('.ndjson')}
    for relative in sorted(indexes):
        path=checkout/relative
        for line_no,line in enumerate(path.read_text().splitlines(),1):
            try: item=json.loads(line)
            except ValueError:
                errors.append({'kind':'invalid_index','source':relative,'line':line_no,'target':None}); continue
            if not isinstance(item,dict) or 'path' not in item: continue
            value=item['path']
            if not isinstance(value,str) or not value:
                errors.append({'kind':'invalid_index_path','source':relative,'line':line_no,'target':value}); continue
            target=(path.parent/value).resolve()
            if not target.is_relative_to(checkout):
                errors.append({'kind':'outside_checkout','source':relative,'line':line_no,'target':value}); continue
            if not target.is_file():
                errors.append({'kind':'missing_file','source':relative,'line':line_no,'target':value}); continue
            edges.append({'source':relative,'target':target.relative_to(checkout).as_posix(),'fragment':'',
                          'line':line_no,'context':str(item.get('description',item.get('title','')))[:400],
                          'kind':'index_entry'})
    adjacency={}
    for edge in edges: adjacency.setdefault(edge['source'],set()).add(edge['target'])
    reachable=set(); pending=deque(roots)
    while pending:
        node=pending.popleft()
        if node in reachable:continue
        reachable.add(node);pending.extend(adjacency.get(node,()))
    expected={p.relative_to(checkout).as_posix() for p in all_docs}
    if set(selected_ids)!=set(available):
        prefixes=tuple(str(Path(available[key]).parent)+'/' for key in selected_ids)
        expected={p for p in expected if p.startswith(prefixes)}
    if explicit_selection:
        inspected = expected | reachable
        errors = [error for error in errors if error['source'] in inspected]
        edges = [edge for edge in edges if edge['source'] in inspected]
    return {'skills':len(selected_ids),'documents':len(expected),'errors':errors,
            'unreachable':sorted(expected-reachable),'reachable':sorted(reachable),
            'edges':edges,'review_notes':notes,'semantic_validation':'not_performed',
            'selection_source':'explicit_ids' if explicit_selection else 'shipped_catalog',
            'automatic_routing':'not_claimed'}
