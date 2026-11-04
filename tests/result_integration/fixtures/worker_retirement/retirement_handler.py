"""Test-only interpretation of the fixture preparation subprocess."""


def accept(initial, invoke, options, context):
    return {"exit_code": initial.returncode}
