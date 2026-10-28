"""The existing dispatcher consumes the actual external handler response."""
import pytest

from tests.hook_transport.architecture_support import Chain
from .helpers import Scene


@pytest.mark.parametrize('mode', ['enabled', 'disabled'])
def test_optional_handler_preserves_chain(project, tmp_path, mode):
    chain = Chain(project, tmp_path / 'native-chain', 'F12_OPTIONAL_HANDLER_' + mode)
    scene = Scene(project, tmp_path, general=chain.general); scene.require(); scene.ready()
    before_bytes = scene.database.read_bytes()
    scene.policy_data['enabled'] = mode == 'enabled'; scene.save()
    before, after = chain.command('before'), chain.command('after')
    handler = chain.command('source-index')
    handler['options'] = {}
    handler['implementation']['argv'] = [*scene.argv(), '--handler']
    path = chain.configuration('with-optional-index', [before, handler, after])
    chain.install([path])
    try:
        result = chain.invoke()
        packets = chain.assert_order(['before', 'after'])
        incoming = packets[1]['response']
        assert incoming['systemMessage'] == 'seed|before'
        assert result['systemMessage'] == 'seed|before|after'
        # The real dispatcher rejects a mismatching invocation_id before "after".
        assert packets[0]['invocation_id'] != packets[1]['invocation_id']
        if mode == 'enabled':
            assert incoming['hookSpecificOutput']['hookEventName'] == 'SessionStart'
            context = incoming['hookSpecificOutput']['additionalContext']
            assert 'source-index' in context
            assert str(scene.checkout) in context
            assert str(scene.policy) in context
        else:
            assert scene.records() == []
        assert scene.database.read_bytes() == before_bytes
    finally:
        chain.finish()
