import copy
import json
from pathlib import Path
from tests.runner.helpers import process


def template():
    return process("writing")


def additions():
    return [
        {"op":"put_requirement", "value":{"id":"rollback-required", "kind":"section", "stages":["draft"], "phase":"pre", "section":"rollback", "states":["populated"]}},
        {"op":"put_section", "value":{"id":"rollback", "template":"Describe rollback.", "normalization":"strip", "write_stages":["draft","amend"]}},
        {"op":"patch_stage", "id":"draft", "set":{"instruction":"Create result and describe rollback."}},
    ]


def request(mode, goal, expected, changes, request_id="req-1", selection=None):
    return {"schema":"goal-config-batch-1", "request_id":request_id, "mode":mode,
            "goal_type":goal, "expected_revision":expected, "template":selection, "changes":changes}


def settings(root, definitions=None):
    from harness.common import digest
    (root/"templates").mkdir(parents=True, exist_ok=True)
    body=template()
    (root/"templates/writing.json").write_text(json.dumps(body), encoding="utf-8")
    cfg={"schema":"goal-config-editor-1", "root":".",
         "database":"state/config-editor.sqlite", "lock":"state/config-editor.lock",
         "responses":"state/config-responses", "file_mode":420, "json_indent":2,
         "lock_seconds":2.0, "lock_poll_seconds":0.01, "max_changes":2000,
         "max_input_bytes":1000000, "output_chars":2000,
         "exit_codes":{"success":0,"rejected":2,"pending":3},
         "processes": {"writing":"config/processes/writing.json", "other":"config/processes/other.json"} if definitions is None else definitions,
         "templates":{"writing-v1":{"path":"templates/writing.json", "version":"1", "digest":digest(body)}}}
    path=root/"goal-editor.json"; path.write_text(json.dumps(cfg), encoding="utf-8")
    return path, {"id":"writing-v1","version":"1","digest":digest(body)}
