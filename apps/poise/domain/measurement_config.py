"""Explicit stateless measurement instrument settings."""
from dataclasses import dataclass
from pathlib import Path
from domain.observation_config import fields,finite
from domain.generic import positive_count
from domain.state import invalid_config


@dataclass(frozen=True)
class MeasurementSettings:
    max_content_bytes: int
    max_response_bytes: int
    tokenizer: dict


def parse_settings(raw):
    value=fields(raw.get('measurement'),('schema','max_content_bytes','max_response_bytes','tokenizer'),'/measurement')
    if value['schema']!='poise-measurement-config/v1':invalid_config('/measurement/schema')
    if not all(positive_count(value[k]) for k in ('max_content_bytes','max_response_bytes')):invalid_config('/measurement')
    instrument=fields(value['tokenizer'],('kind','identity','command'),'/measurement/tokenizer')
    if not isinstance(instrument['identity'],str) or not instrument['identity']:invalid_config('/measurement/tokenizer/identity')
    if instrument['kind']=='unavailable':
        if instrument['command'] is not None:invalid_config('/measurement/tokenizer/command')
    elif instrument['kind']=='command':
        c=fields(instrument['command'],('argv','cwd','env','timeout_seconds','max_output_bytes'),'/measurement/tokenizer/command')
        if not isinstance(c['argv'],list) or not c['argv'] or any(not isinstance(a,str) or '\0' in a for a in c['argv']):invalid_config('/measurement/tokenizer/command/argv')
        if not c['argv'][0] or not isinstance(c['cwd'],str) or not Path(c['cwd']).is_absolute():invalid_config('/measurement/tokenizer/command')
        if not isinstance(c['env'],dict) or any(not isinstance(k,str) or not k or '=' in k or '\0' in k or not isinstance(v,str) or '\0' in v for k,v in c['env'].items()):invalid_config('/measurement/tokenizer/command/env')
        if not finite(c['timeout_seconds'],True) or not positive_count(c['max_output_bytes']):invalid_config('/measurement/tokenizer/command')
        instrument['command']=c
    else:invalid_config('/measurement/tokenizer/kind')
    return MeasurementSettings(value['max_content_bytes'],value['max_response_bytes'],instrument)
