"""Substitute explicitly declared data with the standard template grammar."""
from string import Template


def render_data(value, parameters):
    if isinstance(value, str):
        return Template(value).substitute(parameters)
    if isinstance(value, list):
        return [render_data(item, parameters) for item in value]
    if isinstance(value, dict):
        return {key: render_data(item, parameters) for key, item in
                value.items()}
    return value
