# Wait through the originating owner

Read only for registered check execution, nested host waits or lost-result
recovery. Follow the [parent-call waiting contract](../../../../docs/workflows/runner.md#ожидание-родительского-вызова).

Start the configured check once and await its terminal response. The registered
runner is the sole continuous observer of child progress and process state.
Do not inspect active logs, count tests, query child CPU/processes, install a
parallel monitor or send another status request.

If the host yields the outer tool/cell/session, resume that same host handle.
This waits for the original call; it is not permission to start subject polling.
Use the actual exposed wait interface, not an invented MCP status operation or
chat timer. Ordinary direct-command waiting remains with that command's owner.

For an authorized cancellation use the existing registered owner. Never kill its
process through another shell. After a terminal failure, diagnose saved full
records/streams through bounded reading. If the response is lost, establish
outcome/effects from that owner before any replay; do not rerun for a nicer preview.
