"""Replacement plugin fixtures; no dependency on Bootstrap implementation."""


def first(initial, invoke, options, context):
    return {"status": "first-handler", "exit_code": 7,
            "stdout": initial.stdout.decode("utf-8"),
            "stderr": initial.stderr.decode("utf-8"),
            "process_exit": initial.returncode,
            "marker": options["marker"], "operation": context["operation"]}


def second(initial, invoke, options, context):
    return {"status": "second-handler", "exit_code": 0,
            "stdout": initial.stdout.decode("utf-8"),
            "stderr": initial.stderr.decode("utf-8"),
            "process_exit": initial.returncode,
            "marker": options["marker"], "operation": context["operation"]}
