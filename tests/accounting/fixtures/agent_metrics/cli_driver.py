"""Observe one real isolated CLI call; never synthesize its result or exit."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time


def observe_gate(configuration):
    """Pause before a real shared processor; never replace its return/effects."""
    if configuration is None:
        return
    previous = sys.getprofile()
    root = Path(configuration["directory"])
    root.mkdir(parents=True, exist_ok=True)
    sequence = 0

    def observe(frame, event, argument):
        nonlocal sequence
        if previous is not None:
            previous(frame, event, argument)
        subject = frame.f_locals.get("self")
        if (event != "call" or frame.f_globals.get("__name__") != configuration["module"]
                or frame.f_code.co_name != configuration["function"]
                or type(subject).__name__ != configuration["class"]):
            return
        sequence += 1
        target = root / (str(os.getpid()) + "-" + str(sequence) + ".entered.json")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"pid": os.getpid(), "boundary": configuration["module"] + "."
                                         + configuration["class"] + "." + configuration["function"]}))
        os.replace(temporary, target)
        deadline = time.monotonic() + configuration["timeout_seconds"]
        while not (root / "release").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("F16_TEST_GATE_RELEASE_NOT_OBSERVED")
            time.sleep(.01)

    sys.setprofile(observe)
    threading.setprofile(observe)


def observe_commit_exit(configuration):
    """Terminate only an owned child after the real spool receiver returns, before ack."""
    if configuration is None:
        return
    owner_pid = os.getpid()
    previous = sys.getprofile()
    root = Path(configuration["directory"])
    root.mkdir(parents=True, exist_ok=True)

    def observe(frame, event, argument):
        if previous is not None:
            previous(frame, event, argument)
        caller = frame.f_back
        if (event != "return" or caller is None or os.getpid() == owner_pid
                or caller.f_globals.get("__name__") != "poise.infrastructure.telemetry_spool"
                or caller.f_code.co_name != "_deliver"):
            return
        spool = caller.f_locals.get("self")
        receiver = getattr(spool, "process", None)
        function = getattr(receiver, "__func__", receiver)
        if not hasattr(function, "__code__"):
            function = getattr(getattr(receiver, "__call__", None), "__func__", None)
        if function is None or frame.f_code is not getattr(function, "__code__", None):
            return
        claim = caller.f_locals["claim"]
        witness = {"pid": os.getpid(), "owner_pid": owner_pid,
                   "boundary": "actual-spool-receiver-return-before-ack",
                   "identity": claim.record["identity"], "queued_path": str(claim.path),
                   "requested_exit_code": configuration["exit_code"]}
        target = root / (str(os.getpid()) + ".committed-before-ack.json")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(witness))
        os.replace(temporary, target)
        os._exit(configuration["exit_code"])

    sys.setprofile(observe)
    threading.setprofile(observe)


def main():
    request = json.load(sys.stdin)
    specification = importlib.util.spec_from_file_location("f16_task_observer", request["observer"])
    observer = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(observer)
    sentinel = observer.observe()
    observe_gate(request["gate"])
    observe_commit_exit(request.get("commit_exit"))
    output_bytes, error_bytes = io.BytesIO(), io.BytesIO()
    output = io.TextIOWrapper(output_bytes, encoding="utf-8", write_through=True)
    error = io.TextIOWrapper(error_bytes, encoding="utf-8", write_through=True)
    sys.argv = ["poise", *request["argv"]]
    sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request["packet"], ensure_ascii=False).encode()),
                                encoding="utf-8")
    reached, code, unexpected = [], None, None
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
        try:
            reached.append("real-poise-module-entry")
            runpy.run_module("poise", run_name="__main__")
        except SystemExit as exc:
            code = exc.code
        except Exception as exc:
            unexpected = {"kind": type(exc).__name__, "message": str(exc)}
    loaded = sys.modules.get("poise")
    output.flush()
    error.flush()
    result = {"pid": os.getpid(), "reached": reached, "exit_code": code,
              "stdout": output_bytes.getvalue().decode(), "stderr": error_bytes.getvalue().decode(),
              "unexpected": unexpected, "sentinel": sentinel,
              "poise_source": None if loaded is None else loaded.__file__}
    # Instrumentation does not replace the CLI status. The parent checks both.
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
