"""CLI adapter for a single declarative packet from stdin. No task DB is opened."""
import json
import uuid
from ..composition import goal_config_tools
from ..infrastructure.goal_config import EditorSettings, strict_json, atomic_write, PublicationPending
from ..common import descendant
from ..modules.foundation.errors import PoiseError
from ..modules.goal_config.domain import BatchValidationError
from ..modules.result_views.domain import bounded_envelope


def execute(settings_path, stream, output):
    settings=None
    try:
        settings=EditorSettings(settings_path)
        raw=stream.read(settings.raw['max_input_bytes']+1)
        if len(raw)>settings.raw['max_input_bytes']:
            raise PoiseError('Превышен max_input_bytes; ни одно изменение не принято')
        request=strict_json(raw.decode('utf-8'))
        tools=goal_config_tools(settings_path)
        schema=request.get('schema') if isinstance(request,dict) else None
        if schema == 'goal-config-batch-1':
            result=tools.apply_batch(request)
        elif schema == 'goal-config-status-1':
            result=tools.status(request)
        elif schema == 'goal-config-reconcile-1':
            result=tools.reconcile(request)
        else:
            raise PoiseError('Неподдерживаемая schema goal-config')
        category='success'
    except (PoiseError,UnicodeError) as exc:
        category='pending' if isinstance(exc,PublicationPending) else 'rejected'
        issues=list(exc.issues) if isinstance(exc,BatchValidationError) else [{'path':'request','code':category,'message':str(exc)}]
        result={'status':category,'error_count':len(issues),'issues':issues}
    if settings is None:
        # Bootstrap failure: no valid configuration from which to obtain exit policy.
        output.write(json.dumps(result,ensure_ascii=False)+'\n')
        return 2
    response=descendant(settings.root,settings.raw['responses'])/(str(uuid.uuid4())+'.json')
    try:
        atomic_write(response,(json.dumps(result,ensure_ascii=False,indent=settings.raw['json_indent'])+'\n').encode('utf-8'),settings.raw['file_mode'])
    except OSError as exc:
        output.write(json.dumps({'status':'response_storage_error','business_status':result['status'],'message':str(exc)},ensure_ascii=False)+'\n')
        return settings.raw['exit_codes']['pending']
    fields=('goal_type','revision','managed_revision','live_revision','aligned',
            'replayed','changed','change_count','config_path','error_count')
    metadata={key:result[key] for key in fields if key in result}
    try:
        text=bounded_envelope(result,response,settings.raw['output_chars'],metadata,
                              {'issues':result.get('issues',[])},[])
    except PoiseError:
        return settings.raw['exit_codes']['pending']
    output.write(text)
    return settings.raw['exit_codes'][category]
