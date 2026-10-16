"""Declarative presentation contracts. They never decide a check's verdict."""
from dataclasses import dataclass
from copy import deepcopy
import math
import json
import re
from ..artifact_factory.domain import exact, relative
from ..foundation.errors import DomainError


def _positive(value, where, integral=True):
    if type(value) not in ((int,) if integral else (int,float)) or value<=0 or not math.isfinite(value):
        raise DomainError(f'{where} requires an explicit finite positive value')


def utf8_json_text(value, *, indent=None, separators=None):
    """Escape unpaired code units as JSON without losing their semantic values."""
    return json.dumps(value, ensure_ascii=False, indent=indent, separators=separators).encode(
        'utf-8', errors='backslashreplace').decode('utf-8')


def _envelope_text(value):
    return utf8_json_text(value, separators=(',', ':')) + '\n'


def native_context_view(result, response_path, budget, notice):
    """Preserve the native context; optional warning text navigates to full data."""
    _positive(budget, 'output budget')
    text = _envelope_text(result)
    if len(text) <= budget and result.get('systemMessage'):
        return text
    view = {**result, 'systemMessage': notice + ' ' + str(response_path)}
    text = _envelope_text(view)
    if len(text) > budget:
        raise DomainError('Configured output budget cannot fit native context and diagnostic address')
    return text


def response_required(result, budget):
    """Every agent result has complete saved data, including tiny replies."""
    _positive(budget, 'output budget')
    return True


def require_result_budget(response_path, budget, status):
    """Admission uses the same exact JSON/LF boundary as presentation."""
    _positive(budget, 'output budget')
    minimal = {'status': status, 'response_path': str(response_path)}
    if len(_envelope_text(minimal)) > budget:
        raise DomainError('Output budget cannot fit the persisted result address; action not started')


def _historical_records(result):
    """Receipt identity is global; an owner is provenance, not another ID."""
    records = {}
    for group in result.get('historical_receipts', []):
        for row in group['checks']:
            identity = row['id']
            if identity in records:
                if records[identity][0] != group['task']:
                    raise DomainError('Historical receipt owner conflict')
                continue
            records[identity] = (group['task'], row)
    return list(records.values())


def result_view(result, response_path, budget, mode, *, metadata=None,
                collections=None, command_views=()):
    """Pure, source-derived projection. Full always means file navigation."""
    _positive(budget, 'output budget')
    if (not isinstance(result, dict) or not isinstance(result.get('status'), str)
            or not result['status']):
        raise DomainError('Saved result requires a nonempty string status')
    if mode not in ('brief', 'expanded', 'full', 'adapter'):
        raise DomainError('Unknown result view')
    if response_path is None or not str(response_path):
        raise DomainError('Result view requires a persisted full result address')
    history = _historical_records(result)
    view = {'status': result['status'], 'response_path': str(response_path)}
    if mode != 'adapter':
        view['view'] = mode
    if len(_envelope_text(view)) > budget:
        raise DomainError('Output budget cannot fit the persisted result address')
    if mode == 'full':
        return _envelope_text(view)

    errors = result.get('presentation_errors', [])
    other = {key: value for key, value in result.items() if isinstance(value, list)
             and key not in ('historical_receipts', 'presentation_errors')}
    other.update({} if collections is None else collections)
    summary = {}
    if 'historical_receipts' in result:
        summary['historical_receipts'] = {
            'total': len(history), 'shown': 0, 'omitted': len(history),
            'failed': sum(row.get('passed') is False for _, row in history)}
    if 'presentation_errors' in result:
        summary['presentation_errors'] = {'total': len(errors), 'shown': 0, 'omitted': len(errors)}
    for field, items in other.items():
        if field not in summary:
            summary[field] = {'total': len(items), 'shown': 0, 'omitted': len(items)}
    has_collections = bool(summary)
    if summary:
        candidate = {**view, 'summary': summary}
        if len(_envelope_text(candidate)) <= budget:
            view = candidate
        else:
            # Counts and details are atomic; do not imply completeness without counts.
            summary = {}

    if mode == 'expanded' and summary:
        def admit(field, details, shown):
            nonlocal view
            counts = {**view['summary'], field: {
                **view['summary'][field], 'shown': shown,
                'omitted': view['summary'][field]['total'] - shown}}
            candidate = {**view, 'summary': counts,
                         'details': {**view.get('details', {}), field: details}}
            if len(_envelope_text(candidate)) > budget:
                return False
            view = candidate
            return True

        groups = []
        shown = 0
        for task, row in history:
            candidate = deepcopy(groups)
            group = next((item for item in candidate if item['task'] == task), None)
            if group is None:
                group = {'task': task, 'checks': []}
                candidate.append(group)
            group['checks'].append(row)
            if not admit('historical_receipts', candidate, shown + 1):
                break
            groups, shown = candidate, shown + 1
        for field, items in [('presentation_errors', errors), *other.items()]:
            if field not in summary:
                continue
            selected = []
            for item in items:
                if not admit(field, [*selected, item], len(selected) + 1):
                    break
                selected.append(item)

    if mode == 'expanded' and (not has_collections or summary):
        # Non-history tools use the same owner: show whole useful source fields
        # when they fit, without inventing another renderer or clipping data.
        for field, value in result.items():
            if field in ('status', 'notice', 'reason', 'next_work',
                         'historical_receipts', 'presentation_errors') or field in other:
                continue
            candidate = {**view, 'details': {**view.get('details', {}), field: value}}
            if len(_envelope_text(candidate)) <= budget:
                view = candidate

    # Optional outcome/navigation fields never displace the required minimum.
    extras = {key: result[key] for key in ('reason', 'notice', 'next_work') if key in result}
    extras.update({} if metadata is None else metadata)
    for field, value in extras.items():
        if field in view or field in ('historical_receipts', 'presentation_errors', 'details', 'summary'):
            continue
        candidate = {**view, field: value}
        if len(_envelope_text(candidate)) <= budget:
            view = candidate
    if mode == 'adapter' and command_views:
        # One configured primary preview, not an exhaustive receipt collection.
        candidate = {**view, 'command_preview': command_views[0]}
        if len(_envelope_text(candidate)) <= budget:
            view = candidate
    return _envelope_text(view)


def primary_text(text, parser, selection_pattern, chars):
    """One command display selection; never used to interpret raw execution."""
    if parser == 'lines':
        text = '\n'.join(line for line in text.splitlines() if re.search(selection_pattern, line))
    elif parser != 'tail':
        raise DomainError('Unknown output parser, no fallback')
    return text[-chars:]


def command_receipts(result):
    """Collect distinct captured commands from a public historical result."""
    receipts = {}
    def collect(value):
        if isinstance(value, dict):
            if {'id', 'argv', 'stdout', 'stderr', 'stdout_digest', 'stderr_digest'}.issubset(value):
                receipts.setdefault(value['id'], value)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(result)
    return list(receipts.values())


def terminal_materials_notice():
    """The agreed public message for terminal historical receipts."""
    return 'Служебные материалы задачи удалены. Служебные журналы уничтожены'


def bounded_envelope(result, response_path, budget, metadata, collections, command_views,
                     required_fields=None):
    """Ordinary brief transport over the shared representation owner."""
    source = dict(result)
    if required_fields is not None:
        source.update({key: value for key, value in required_fields.items()
                       if key in ('notice', 'historical_receipts', 'presentation_errors')})
    return result_view(source, response_path, budget, 'adapter', metadata=metadata,
                       collections=collections, command_views=command_views)


@dataclass(frozen=True)
class OutputPolicy:
    data: dict

    @classmethod
    def parse(cls,value):
        from ..foundation.validation import configuration_fields
        value = deepcopy(configuration_fields(value,{'known','unknown','directory','manifest','job','worker_stdout','worker_stderr',
                     'chunk_bytes','worker_timeout_seconds','file_mode'},'output policy', DomainError))
        for k in ('directory','manifest','job','worker_stdout','worker_stderr'):relative(value[k])
        names=[value[k] for k in ('manifest','job','worker_stdout','worker_stderr')]
        if len(names)!=len(set(names)):raise DomainError('Output control filenames must be distinct')
        _positive(value['chunk_bytes'],'chunk_bytes')
        _positive(value['worker_timeout_seconds'],'worker_timeout_seconds',False)
        if type(value['file_mode']) is not int or not 0<=value['file_mode']<=0o777:
            raise DomainError('Explicit file mode required')
        if not isinstance(value['known'],list):raise DomainError('known profiles must be a list')
        ids=[]
        def profile_options(profile):
            profile = configuration_fields(profile,{'id','argv_tokens','parser','selection_pattern','primary_chars','extended_bytes','selected_bytes','files'},'output profile', DomainError)
            profile['files'] = configuration_fields(profile['files'],{'extended','full','selected'},'view files', DomainError)
            return profile
        value['known'] = [profile_options(profile) for profile in value['known']]
        value['unknown'] = profile_options(value['unknown'])
        for profile in [*value['known'],value['unknown']]:
            if not isinstance(profile['id'],str) or not profile['id']:raise DomainError('Profile ID required')
            ids.append(profile['id'])
            tokens=profile['argv_tokens']
            if not isinstance(tokens,list) or any(not isinstance(x,str) or not x for x in tokens):raise DomainError('argv_tokens is a list of exact tokens')
            if profile['parser'] not in ('tail','lines'):raise DomainError('Unknown parser, no fallback')
            pat=profile['selection_pattern']
            if profile['parser']=='tail' and pat is not None:raise DomainError('Tail parser requires explicit null pattern')
            if profile['parser']=='lines':
                if not isinstance(pat,str) or not pat:raise DomainError('Line parser requires explicit pattern')
                try:re.compile(pat)
                except re.error as exc:raise DomainError('Invalid output selection pattern') from exc
            for k in ('primary_chars','extended_bytes','selected_bytes'):_positive(profile[k],k)
            exact(profile['files'],{'extended','full','selected'},'view files')
            for k in ('extended','full'):relative(profile['files'][k])
            if profile['files']['selected'] is not None:relative(profile['files']['selected'])
            if profile['parser']=='lines' and profile['files']['selected'] is None:raise DomainError('Line parser requires a selected view filename')
            filenames=[v for v in profile['files'].values() if v is not None]
            if len(set(filenames+names))!=len(filenames+names):raise DomainError('Duplicate result/control filenames')
        if len(ids)!=len(set(ids)):raise DomainError('Duplicate profile ID')
        if value['unknown']['argv_tokens']:raise DomainError('Unknown profile is explicit catch-all without a matcher')
        if any(not p['argv_tokens'] for p in value['known']):raise DomainError('Known profiles require a matcher')
        return cls(deepcopy(value))

    def select(self,argv):
        matches=[]
        for p in self.data['known']:
            tokens=p['argv_tokens']
            if any(argv[i:i+len(tokens)]==tokens for i in range(len(argv)-len(tokens)+1)):
                matches.append(p)
        if len(matches)>1:raise DomainError('Ambiguous known command profiles')
        return deepcopy(matches[0] if matches else self.data['unknown'])
