#!/usr/bin/env python3
"""Test-owned public CLI; SQLite ownership stays exclusively in this fixture."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


def main():
    settings = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    mode = sys.argv[2]
    database = Path(settings["database"])
    if mode != "seed":
        audit = Path(settings["audit"])
        audit.parent.mkdir(parents=True, exist_ok=True)
        event = {"mode": mode, "args": sys.argv[2:], "cwd": os.getcwd(),
                 "stdin": sys.stdin.buffer.read().hex(),
                 "env": {key: value for key, value in os.environ.items() if key.startswith("T028_")}}
        with audit.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    if mode == "check":
        if not database.exists():
            print("owner missing")
            return 3
        try:
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                assert db.execute("SELECT version FROM fixture_owner_state").fetchone() == (1,)
        except (sqlite3.DatabaseError, AssertionError):
            print("owner refuses corrupt state")
            return 11
        if settings["final_fail"]:
            print("owner final failure")
            return 9
        print("owner ready" + settings["echo"])
        return 0
    if mode not in ("apply", "seed"):
        raise ValueError("unknown fixture owner mode")
    time.sleep(settings["apply_sleep"])
    if settings["apply_exit"]:
        print("owner apply failure")
        return settings["apply_exit"]
    if database.exists():
        # No unlink/reset/replacement of unknown existing state.
        try:
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                assert db.execute("SELECT version FROM fixture_owner_state").fetchone() == (1,)
        except (sqlite3.DatabaseError, AssertionError):
            print("owner refuses corrupt state")
            return 12
    else:
        database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE fixture_owner_state(version INTEGER NOT NULL)")
            db.execute("INSERT INTO fixture_owner_state VALUES (1)")
    if settings["rewrite"] and mode != "seed":
        target, text = settings["rewrite"]
        Path(target).write_text(text, encoding="utf-8")
    print("owner prepared" + settings["echo"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
