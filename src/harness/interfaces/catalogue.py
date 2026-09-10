"""One bounded stdin batch for independent packs or exact task contracts."""
import json
import uuid
from pathlib import Path
from ..common import HarnessError,exact_keys,load_config,descendant
from ..infrastructure.goal_config import strict_json,atomic_write,PublicationPending
from ..infrastructure.catalogue import FileCatalogue
from ..application.catalogue import CatalogueCommands
from ..composition import goal_config_tools


def execute(path,stream,output):
    repository=None
    try:
        repository=FileCatalogue(path);cfg=repository.editor.raw
        raw=stream.read(cfg['max_input_bytes']+1)
        if len(raw)>cfg['max_input_bytes']:raise HarnessError('Catalogue input limit exceeded')
        request=strict_json(raw.decode('utf-8'))
        exact_keys(request,{'action','items','project_config'},'catalogue packet')
        tools=CatalogueCommands(repository,goal_config_tools(repository.editor.path),repository.raw['max_items'])
        if request['action']=='install':
            if request['project_config'] is not None:raise HarnessError('Install does not open project/task state')
            result=tools.install(request['items'])
        elif request['action']=='tasks':
            if not isinstance(request['project_config'],str) or not Path(request['project_config']).is_absolute():
                raise HarnessError('Select an absolute project config for task creation validation')
            _,project,processes=load_config(Path(request['project_config']))
            result=tools.tasks(request['items'],processes,project['automatic_checks'])
        else:raise HarnessError('Unknown catalogue action')
        category='success'
    except (HarnessError,UnicodeError) as exc:
        category='pending' if isinstance(exc,PublicationPending) else 'rejected'
        result={'status':category,'message':str(exc)}
    if repository is None:
        output.write(json.dumps(result,ensure_ascii=False)+'\n');return 2
    cfg=repository.editor.raw
    response=descendant(repository.root,cfg['responses'])/(str(uuid.uuid4())+'.json')
    try:atomic_write(response,(json.dumps(result,ensure_ascii=False,indent=cfg['json_indent'])+'\n').encode(),cfg['file_mode'])
    except OSError as exc:
        output.write(json.dumps({'status':'response_storage_error','message':str(exc)},ensure_ascii=False)+'\n')
        return cfg['exit_codes']['pending']
    view={**result,'response_path':str(response)}
    if len(json.dumps(view,ensure_ascii=False))+1>cfg['output_chars']:
        view={'status':result['status'],'response_path':str(response)}
    output.write(json.dumps(view,ensure_ascii=False)+'\n')
    return cfg['exit_codes'][category]
