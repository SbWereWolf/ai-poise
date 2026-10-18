"""Independent literal fixtures and arrangement guards, GREEN before implementation."""
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from hook_transport.preparation_source_upgrade_support import (
    FIXTURES, FORMS, SESSION, arrange_hooks, fixture_script, lifecycle, read_json, snapshot,
    assert_captures_preserved, assert_reconfiguration_reply, assert_reconfigured_state,
    assert_session_preserved, assert_target_summary, operation_result, public_reconfigure,
    reconfigure_packet, resume, save_replay_proof,
)
from runtime_services.worktree_preparation_support import arrange, capture_origin, use_environment_handler


@pytest.mark.parametrize('sample', ['plain', 'unicode'])
@pytest.mark.parametrize('form', [*FORMS, 'current'])
def test_literal_launcher_matches_full_independent_sample(sample, form):
    selected = read_json(FIXTURES / 'fixed-samples.json')[sample]
    golden = selected['forms'][form]
    expected = ('#!/bin/sh\nexec ' + shlex.join(golden['argv'])
                + (' "$@"' if golden['forward'] else '') + '\n').encode()
    observed = fixture_script(form, selected['coordinates'])
    assert observed == expected
    syntax = subprocess.run(['sh', '-n'], input=observed, capture_output=True)
    assert (syntax.returncode, syntax.stdout, syntax.stderr) == (0, b'', b'')


@pytest.mark.parametrize('sample', ['plain', 'unicode'])
@pytest.mark.parametrize('form', ['target-issuer', 'current-output'])
def test_guarded_literal_matches_independent_whole_frame(sample, form):
    selected = read_json(FIXTURES / 'guarded-fixed-samples.json')[sample]
    golden = selected['forms'][form]
    expected = ('#!/bin/sh\nexec ' + shlex.join(golden['argv']) + ' "$@"\n').encode()
    observed = fixture_script(form, selected['coordinates'])
    assert observed == expected
    arguments = shlex.split(observed.decode().splitlines()[1])
    assert arguments[5] == selected['coordinates']['code_root'] + '/src'
    assert arguments[arguments.index('env') + 1] == golden['argv'][9]
    other = 'current-output' if form == 'target-issuer' else 'target-issuer'
    assert observed != fixture_script(other, selected['coordinates'])
    syntax = subprocess.run(['sh', '-n'], input=observed, capture_output=True)
    assert (syntax.returncode, syntax.stdout, syntax.stderr) == (0, b'', b'')


@pytest.mark.parametrize('mutation', ['lost-forwarding', 'lost-LF', 'changed-roots', 'extra-argv'])
def test_exact_fixture_oracle_rejects_near_matches(mutation):
    coordinates = read_json(FIXTURES / 'fixed-samples.json')['plain']['coordinates']
    original = fixture_script('H5', coordinates)
    changed = {
        'lost-forwarding': original.replace(b' "$@"', b''),
        'lost-LF': original.removesuffix(b'\n'),
        'changed-roots': fixture_script('current', coordinates),
        'extra-argv': original.replace(b' "$@"', b' --foreign "$@"'),
    }[mutation]
    with pytest.raises(AssertionError):
        assert changed == original
    # A separately labelled current script is valid current data, not old H5.
    assert fixture_script('current', coordinates).endswith(b' "$@"\n')


def test_transparent_ready_process_emits_all_independent_bytes():
    observed = subprocess.run([sys.executable, '-I', '-B', str(FIXTURES / 'emit_result.py')],
                              capture_output=True)
    assert observed.returncode == 0
    assert observed.stdout == (FIXTURES / 'ready.stdout').read_bytes()
    assert observed.stderr == (FIXTURES / 'diagnostic.stderr').read_bytes()
    assert json.loads(observed.stdout)['schema'] == 'fixture/environment/v2'


def test_native_fixture_has_real_identity_and_prior_message(project, tmp_path):
    case = arrange_hooks(project, tmp_path)
    observed = snapshot(case)
    assert [row[3] for row in lifecycle(observed['rows']['events'])] == ['SessionStart', 'UserPromptSubmit']
    binding, = observed['rows']['bindings']
    assert binding[0] == SESSION
    assert json.loads(binding[2])['conversation_id'] == SESSION
    assert Path(case['binding']['launcher']).is_file()


def test_preparation_native_arrangement_keeps_real_project_binding(project, tmp_path, monkeypatch):
    preparation = arrange(project, monkeypatch)
    use_environment_handler(preparation)
    hook = arrange_hooks(project, tmp_path)
    origin = capture_origin(preparation)
    assert origin['task_id'] == 'T1' and origin['project_id'] == 'demo'
    assert origin['branch'] == 'tasks/T1'
    assert read_json(preparation['configuration'])['handler']['callable'] == 'apps.bootstrap.environment_result:handle'
    assert hook['binding']['project'] == 'demo'
    assert [row[3] for row in lifecycle(snapshot(hook)['rows']['events'])] == ['SessionStart', 'UserPromptSubmit']


def test_public_reconfiguration_replay_keeps_operation_and_observes_current_inputs(project, tmp_path):
    first_input = (FIXTURES / 'replay-initial.stdout').read_bytes()
    replay_input = (FIXTURES / 'replay-current.stdout').read_bytes()
    observation_input = tmp_path / 'current observation.txt'
    observation_input.write_bytes(first_input)
    probe = read_json(FIXTURES / 'replay-probe.template.json')
    probe['argv'] = [sys.executable, '-B', str(FIXTURES / 'replay-probe.py'), str(observation_input)]
    case = arrange_hooks(project, tmp_path, probe=probe)
    packet = reconfigure_packet(case)
    before = snapshot(case)
    code, first = public_reconfigure(case, packet)
    assert code == 0, first
    initial_capture = assert_reconfiguration_reply(case, packet, first, first_input)
    first_state = snapshot(case)
    assert_reconfigured_state(case, packet, before, first_state)
    observation_input.write_bytes(replay_input)
    repeated_code, repeated = public_reconfigure(case, packet)
    assert repeated_code == 0, repeated
    replay_capture = assert_reconfiguration_reply(case, packet, repeated, replay_input)
    with pytest.raises(AssertionError):
        assert_reconfiguration_reply(case, packet, first, replay_input)
    assert operation_result(repeated) == operation_result(first)
    replay_state = snapshot(case)
    assert replay_state == first_state
    assert_captures_preserved(initial_capture)
    result = resume(case, first['definition_path'])
    assert_target_summary(case, packet, result)
    assert_session_preserved(replay_state, snapshot(case))
    assert_captures_preserved(initial_capture)
    assert_captures_preserved(replay_capture)
    save_replay_proof(case, packet, before, first_state, replay_state,
                      (initial_capture, replay_capture), (first_input, replay_input), tmp_path)
