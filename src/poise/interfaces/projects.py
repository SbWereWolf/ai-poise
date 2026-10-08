"""One batch CLI or a bounded questionnaire; neither edits task storage."""
from __future__ import annotations
import json
from copy import deepcopy
from ..modules.foundation.errors import PoiseError
from ..infrastructure.projects import ProjectSettings
from ..infrastructure.goal_config import strict_json
from ..composition import project_tools, compose_project_tools


def emit(settings,result,category,output):
    output.write(json.dumps(result,ensure_ascii=False)+'\n')
    return settings.raw['exit_codes'][category] if settings is not None else 2


def execute(settings_path,stream,output,action=None):
    settings=None
    try:
        settings=ProjectSettings(settings_path)
        if action=='list':
            return emit(settings,project_tools(settings_path).list(),'success',output)
        if action == 'check':
            tools = compose_project_tools(settings)
            raw = None
            try:
                content = stream.read(settings.raw['max_input_bytes'] + 1)
                if len(content) > settings.raw['max_input_bytes']:
                    raise PoiseError('Project input limit exceeded')
                raw = strict_json(content.decode('utf-8'))
            except (PoiseError, UnicodeError, RecursionError) as exc:
                result = tools.input_failure(raw, str(exc))
            else:
                result = tools.check(raw)
            return emit(settings, result, 'success' if result['ready'] else 'rejected', output)
        raw=stream.read(settings.raw['max_input_bytes']+1)
        if len(raw)>settings.raw['max_input_bytes']:raise PoiseError('Project input limit exceeded')
        result=project_tools(settings_path).apply(strict_json(raw.decode('utf-8')))
        return emit(settings,result,'success',output)
    except (PoiseError,UnicodeError,RecursionError) as exc:
        return emit(settings,{'status':'rejected','reason':str(exc)},'rejected',output)


def interactive(settings_path,request,stream,output,prompts):
    settings=None
    try:
        settings=ProjectSettings(settings_path);tools=project_tools(settings_path)
        survey=tools.questionnaire(request)
        for _ in range(settings.raw['max_survey_steps']):
            if survey.complete:
                prompts.write('Review complete candidate (publish / back / abort):\n')
                prompts.write(json.dumps(survey.candidate(settings.raw['max_edits']),ensure_ascii=False,indent=settings.raw['json_indent'])+'\n')
            else:
                q=survey.current()
                prompts.write(f'{survey.position+1}/{len(survey.questions)} {q["prompt"]} [{q["type"]}]\n')
                prompts.write('Candidate: '+json.dumps(survey.value(),ensure_ascii=False)+'\nset <JSON> / keep / back / abort\n')
            prompts.flush()
            line=stream.readline(settings.raw['max_input_bytes']+1)
            if len(line.encode('utf-8'))>settings.raw['max_input_bytes']:raise PoiseError('Answer input limit exceeded')
            if not line or line.strip()=='abort':return emit(settings,{'status':'aborted'},'aborted',output)
            instruction=line.strip()
            try:
                if instruction=='back':survey.back()
                elif instruction=='publish' and survey.complete:
                    value=deepcopy(request);value['edits']=survey.edits()
                    return emit(settings,tools.apply(value),'success',output)
                elif not survey.complete and instruction=='keep':survey.keep()
                elif not survey.complete and instruction.startswith('set '):survey.answer(strict_json(instruction[4:]))
                else:raise PoiseError('Choose an explicit action; blank is not an answer')
            except PoiseError as exc:
                prompts.write(str(exc)+'\n')
        raise PoiseError('Questionnaire step limit reached; project not published')
    except (PoiseError,UnicodeError,RecursionError) as exc:
        return emit(settings,{'status':'rejected','reason':str(exc)},'rejected',output)


def next_tasks(settings_path, project, output):
    settings = None
    try:
        settings = ProjectSettings(settings_path)
        result = project_tools(settings_path).next(project)
        return emit(settings, result, 'rejected' if result['errors'] else 'success', output)
    except (PoiseError, OSError, UnicodeError, RecursionError) as exc:
        return emit(settings, {'status': 'rejected', 'reason': str(exc)}, 'rejected', output)
