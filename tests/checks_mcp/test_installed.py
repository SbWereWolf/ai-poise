"""Earliest GREEN implementation; live host and future docs are separate claims."""
from .hooks_support import hooks, native
from .installed_support import installed


def test_real_noneditable_mcp_run_preview_cancel_and_native_entry(case):
    python = installed(case)
    peer = case.peer(python=python, isolated=True); peer.initialize()
    _, preview = case.full_run(peer, 'preview', case.arguments(preview=True))
    assert preview['result']['status'] == 'planned' and not case.rows()
    _, completed = case.full_run(peer, 'completed')
    case.assert_streams(case.record(completed))
    case.child(marker='installed-held', mode='term')
    peer.call('held', 'run', case.arguments())
    case.started('installed-held')
    _, cancelled = case.cancel_outcome(peer)
    assert cancelled['status'] == 'cancelled'
    _, held = peer.outcome('held')
    assert case.record(held)['actual_exit_code'] == 17
    chain = hooks(case)
    delivered, _ = native(case, chain, 'PreToolUse', 'mcp__repo_checks__run',
                          case.arguments(preview=True), python=python, isolated=True)
    assert delivered['systemMessage'] == 'seed|before|after'
