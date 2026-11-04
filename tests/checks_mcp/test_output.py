"""A real MCP result reaches the common F08 owner; machine bytes stay complete."""
import json
from pathlib import Path

import pytest

from tests.hook_transport.architecture_support import Chain
from tests.hook_transport.session_output_support import scene_for_chain, configure_chain

from .hooks_support import native
from .support import literal


@pytest.mark.parametrize('status,code', [('passed', 0), ('failed', 7)])
def test_actual_callback_brief_expanded_full_share_one_real_result(case, status, code):
    case.stdout_file.write_bytes(('independent long output α\n' * 3000).encode())
    case.child(code=code)
    peer = case.peer(); peer.initialize()
    envelope, body = case.full_run(peer)
    case.assert_streams(case.record(body))
    assert body['result']['status'] == status
    before = case.rows()

    chain = Chain(case.scene.project, case.root / 'output-chain', 'F13_COMMON_OUTPUT',
                  general=case.scene.general)
    output_parent = case.root / 'output-scene'
    output_parent.mkdir()
    scene = scene_for_chain(chain, case.scene.project, output_parent)
    tool = 'mcp__repo_checks__run'
    scene.policy['parsers'][tool] = literal('output-profile.json')
    scene.save_policy()
    configure_chain(chain, scene, matcher='Bash|' + tool, include_end=True)
    case.cleanup_callbacks.append(chain.finish)
    delivered, _ = native(case, chain, 'PostToolUse', tool, case.arguments(), response=envelope)
    expected = literal('output-expectations.json')[status]
    assert delivered.get('continue') is not False
    assert delivered['hookSpecificOutput']['additionalContext'] == expected['brief']
    expanded, full = scene.service('expanded'), scene.service('full')
    assert expanded['text'] == expected['expanded']
    assert expanded['run_id'] == full['run_id']
    assert Path(full['full_path']).is_absolute()
    assert json.loads(Path(full['full_path']).read_bytes()) == envelope
    assert case.rows() == before, 'OUTPUT_SERVICE_REPEATED_THE_CHECK'
    case.assert_streams(case.record(body))
