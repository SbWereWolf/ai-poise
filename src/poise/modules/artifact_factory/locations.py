"""Pure resolution of confirmed historical material locations."""

def relocate_path(value, locations):
    if not isinstance(value, str):
        return value
    for old, new in sorted(locations.items(), key=lambda item: len(item[0]), reverse=True):
        if value == old:
            return new
        if value.startswith(old + '/'):
            return new + value[len(old):]
    return value

