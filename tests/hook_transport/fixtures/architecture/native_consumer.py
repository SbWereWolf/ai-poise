"""Actual configured lifecycle consumer invoking the public reader owner.

Its receipt is checked against the real committed transport DB. Its observations
are test witnesses, never a source of native authority or fabricated acceptance.
"""
import io
import json
import os
from pathlib import Path
import sqlite3
import sys

from task_observer import observe

counts = observe()
packet = json.load(sys.stdin)
options = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert packet["schema"] == "poise-hook-lifecycle-1"
receipt = packet["event_receipt"]
with sqlite3.connect(Path(options["database"]).as_uri() + "?mode=ro", uri=True) as db:
    row = db.execute("SELECT binding,event FROM hook_events WHERE id=?", (receipt["event_id"],)).fetchone()
assert row == (receipt["binding_id"], receipt["event"])
assert os.environ["CODEX_THREAD_ID"] == receipt["session_id"]
from poise.interfaces.content_reader import execute
from poise.infrastructure.clock import SystemClock

out, err = io.StringIO(), io.StringIO()
operation = "prepare" if receipt["event"] == "SessionStart" else "recover"
code = execute(options["config"], options["checkout"], options["reader_policy"],
               io.BytesIO(json.dumps({"operation": operation}).encode()), out, err, SystemClock())
value = json.loads(out.getvalue())
sys.setprofile(None)
witness = {"event_receipt": receipt, "actual_exit_code": code, "stdout": out.getvalue(),
           "stderr": err.getvalue(), "sentinel": counts}
folder = Path(options["witnesses"])
folder.mkdir(parents=True, exist_ok=True)
(folder / (str(receipt["event_id"]) + ".json")).write_text(json.dumps(witness), encoding="utf-8")
assert code == 0 and err.getvalue() == "", witness
assert value["status"] in ("ready", "cleaned", "already_absent"), value
# Native response belongs to the real lifecycle owner, not the reader envelope.
sys.stdout.write("{}\n")
