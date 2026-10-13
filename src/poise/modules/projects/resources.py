"""Explicit associations of project configuration and runtime owners."""
from pathlib import PurePath
from ..foundation.errors import DomainError
from ..foundation.validation import configuration_fields


def validate_resources(value):
    configuration_fields(value, {'project_setup', 'hooks'}, 'runtime resources')
    def absolute(path):
        return isinstance(path, str) and bool(path.strip()) and PurePath(path).is_absolute()
    if not absolute(value['project_setup']):
        raise DomainError('Runtime resources project_setup requires an absolute settings path')
    if not isinstance(value['hooks'], list):
        raise DomainError('Runtime resources hooks requires an explicit list')
    identifiers = set()
    paths = set()
    for item in value['hooks']:
        configuration_fields(item, {'installation_id', 'settings_path'}, 'hook association')
        identity = item['installation_id']
        if not isinstance(identity, str) or not identity.strip() or identity in identifiers:
            raise DomainError('Hook association requires a unique installation identity')
        if not absolute(item['settings_path']) or item['settings_path'] in paths:
            raise DomainError('Hook association requires a unique absolute settings path')
        identifiers.add(identity)
        paths.add(item['settings_path'])
    return value
