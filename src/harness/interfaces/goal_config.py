"""CLI adapter for a single declarative packet from stdin. No task DB is opened."""
import json
import uuid
from ..composition import goal_config_tools
from ..infrastructure.goal_config import EditorSettings, strict_json, atomic_write, PublicationPending
from ..common import descendant
from ..modules.foundation.errors import HarnessError
from ..modules.goal_config.domain import BatchValidationError


def execute(settings_path, stream, output):
    settings=None
    try:
        settings=EditorSettings(settings_path)
        raw=stream.read(settings.raw['max_input_bytes']+1)
        if len(raw)>settings.raw['max_input_bytes']:
            raise HarnessError('Превышен max_input_bytes; ни одно изменение не принято')
        request=strict_json(raw.decode('utf-8'))
        result=goal_config_tools(settings_path).apply_batch(request)
        category='success'
    except (HarnessError,UnicodeError) as exc:
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
    view={**result,'response_path':str(response)}
    def render(value):
        return json.dumps(value,ensure_ascii=False)+'\n'
    cap=settings.raw['output_chars']
    if len(render(view))>cap:
        if 'issues' in view:
            view['issues']=[]
            for issue in result['issues']:
                candidate={**view,'issues':view['issues']+[issue]}
                if len(render(candidate))>cap: break
                view=candidate
        else:
            view={key:result[key] for key in ('status','goal_type','revision','changed','replayed','change_count','config_path')}
            view['response_path']=str(response)
        if len(render(view))>cap:
            view={'status':result['status'],'response_path':str(response)}
        if len(render(view))>cap:
            # Tool output budget cannot hold even the receipt pointer. Business result
            # remains persisted; this is an explicit output-configuration error.
            output.write('')
            return settings.raw['exit_codes']['pending']
    output.write(render(view))
    return settings.raw['exit_codes'][category]
