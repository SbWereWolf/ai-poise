"""Configured static skill facts and observed references keep sample/trigger distinctions."""
import pytest
import hashlib

from tests.accounting.agent_metrics_support import (
    FIXTURES, MetricsScene, assert_measurements, assert_metadata, assert_unavailable_metadata,
    context_view, literal, metadata_values, replace_tokens,
)
from tests.hook_transport.architecture_support import write


def test_protected_skill_static_sizes_and_insufficient_sample_are_observable_without_edits(project, tmp_path):
    scene = MetricsScene(project, tmp_path)
    target = project["app"] / ".agents/skills/fixture-protected/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_bytes((FIXTURES / "protected-skill.md").read_bytes())
    original = target.read_bytes()
    assert original == b"# Protected\n"
    scene.source()
    scene.add_source(literal("skill-content-source.json"))
    write(scene.sources / "effective-policy.json", literal("protected-effective-policy.json"))
    write(scene.sources / "routing.json", literal("empty-skill-routing.json"))
    scene.add_source(literal("protected-skill-policy-source.json"))
    scene.install()
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        # Explicit derived view carries the audit/proxy, not provider-token spend.
        reply = scene.wait_query(family="skill-content", kind="derived", predicate=lambda value: any(
            row["name"] == "core_bytes" and row["value"] == 12 for row in value["metrics"]))
        assert_measurements(reply, literal("expected-protected-skill-static.json"))
        assert_metadata(reply, literal("expected-protected-skill-context.json"))
        assert_metadata(reply, {"issues": [], "unreachable_paths": []})
        digests = metadata_values(reply, "normalized_digest") + metadata_values(reply, "normalized_content_sha256")
        assert hashlib.sha256(b"# Protected").hexdigest() in digests
        assert target.read_bytes() == original
        scene.close()
        again = scene.query(family="skill-content", kind="derived")
        assert again["metrics"] == reply["metrics"]
        assert target.read_bytes() == original
    finally:
        scene.close()


def skill_scene(project, tmp_path, *, with_policy, start=True, minimum_sessions=None, source="skill-selection"):
    scene = MetricsScene(project, tmp_path)
    for skill, content in (("fixture-alpha", "alpha-skill.md"), ("fixture-beta", "beta-skill.md")):
        root = project["app"] / ".agents/skills" / skill
        (root / "references").mkdir(parents=True)
        (root / "SKILL.md").write_bytes((FIXTURES / content).read_bytes())
        (root / "references/ref.md").write_bytes((FIXTURES / "skill-reference.md").read_bytes())
    target = project["app"] / "src/example.py"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"two\nthree\n")
    write(project["app"] / ".agents/references/skill-routing.json", literal("skill-routing.json"))
    scene.source(source)
    if minimum_sessions is not None:
        scene.policy["analyses"]["skill_content"]["minimum_skill_sessions"] = minimum_sessions
        scene.policy["analyses"]["skill_content"]["minimum_reference_sessions"] = minimum_sessions
        write(scene.policy_path, scene.policy)
    scene.add_source(literal("skill-content-source.json"))
    if with_policy:
        write(scene.sources / "effective-policy.json", replace_tokens(literal("effective-policy.json"), scene.tokens))
        scene.add_source(literal("skill-policy-source.json"))
    scene.install()
    if start:
        scene.native("SessionStart")
    return scene


@pytest.mark.parametrize("with_policy", [False, True], ids=["unknown-trigger", "known-trigger"])
def test_reference_repetition_keeps_known_and_unknown_policy_trigger_distinct(project, tmp_path, with_policy):
    scene = skill_scene(project, tmp_path, with_policy=with_policy)
    originals = {str(path): path.read_bytes() for path in (project["app"] / ".agents").rglob("*") if path.is_file()}
    try:
        scene.native("Stop")
        expected = literal("expected-skill-reference-" + ("known" if with_policy else "unknown") + ".json")
        result = scene.wait_measurements("skill-references", expected, kind="derived")
        context = literal("expected-reference-context.json")
        context["trigger_known"] = with_policy
        assert_metadata(result, context)
        if with_policy:
            assert_metadata(result, {"triggered": True, "untriggered_read": False})
        for field in literal("unavailable-taskless-reference-fields.json"):
            assert_unavailable_metadata(result, field, state="not_applicable", source="task-adapter")
        audit = scene.wait_query(family="skill-content", kind="derived", predicate=lambda value: any(
            row.get("context", {}).get("row_type") == "reference" and
            row.get("context", {}).get("path") == ".agents/skills/fixture-alpha/references/ref.md"
            for row in value["metrics"]))
        reference = context_view(audit, {"row_type": "reference", "path": ".agents/skills/fixture-alpha/references/ref.md"})
        assert_measurements(reference, literal("expected-reference-audit-" + ("known" if with_policy else "unknown") + ".json"))
        scene.close()
        assert scene.query(family="skill-references", kind="derived")["metrics"] == result["metrics"]
        assert {str(path): path.read_bytes() for path in (project["app"] / ".agents").rglob("*") if path.is_file()} == originals
    finally:
        scene.close()


def test_explicit_skill_policy_exposes_selection_and_surface_recall_without_editing_skills(project, tmp_path):
    scene = skill_scene(project, tmp_path, with_policy=True)
    try:
        scene.native("Stop")
        expected = literal("expected-skill-selection-summary.json")
        reply = scene.wait_query(family="skill-selection", kind="derived", predicate=lambda value: any(
            row["name"] == "selection_recall_proxy" and row["value"] == .5 for row in value["metrics"]))
        summary = context_view(reply, literal("public-record-context.json")["selection_summary_context"])
        assert_measurements(summary, expected)
        assert_measurements(context_view(reply, {"skill": "fixture-alpha"}), literal("expected-alpha-selection.json"))
        assert_measurements(context_view(reply, {"skill": "fixture-beta"}), literal("expected-beta-selection.json"))
        for skill, filename in (("fixture-alpha", "expected-alpha-selection-context.json"),
                                ("fixture-beta", "expected-beta-selection-context.json")):
            selected = context_view(reply, {"skill": skill})
            for key, expected in literal(filename).items():
                assert any(row["context"].get(key) == expected and
                           (type(expected) is not bool or type(row["context"].get(key)) is bool)
                           for row in selected["metrics"]), (skill, key, expected)
        for field in literal("unavailable-taskless-skill-fields.json"):
            assert_unavailable_metadata(reply, field, state="not_applicable", source="task-adapter")
        assert_metadata(reply, {"skill": "fixture-alpha", "phase": "implement"})
        assert_metadata(reply, {"skill": "fixture-beta"})
    finally:
        scene.close()


def test_skill_reads_keep_file_kind_and_actual_repeated_attribution(project, tmp_path):
    scene = skill_scene(project, tmp_path, with_policy=True)
    try:
        scene.native("Stop")
        reply = scene.wait_query(family="files-skills-checks", predicate=lambda value: any(
            row.get("context", {}).get("call_id") == "f16-ref-read-two" for row in value["metrics"]))
        for expected in literal("expected-skill-file-contexts.json"):
            selected = context_view(reply, {"call_id": expected["call_id"]})
            for key, value in expected.items():
                assert any(row["context"].get(key) == value and
                           (type(value) is not bool or type(row["context"].get(key)) is bool)
                           for row in selected["metrics"]), (key, value)
    finally:
        scene.close()


def test_static_duplicate_and_orphan_facts_keep_independent_content_sizes_and_paths(project, tmp_path):
    scene = skill_scene(project, tmp_path, with_policy=True, start=False)
    orphan = project["app"] / ".agents/skills/fixture-alpha/orphan.md"
    orphan.write_bytes((FIXTURES / "orphan-skill.md").read_bytes())
    originals = {str(path): path.read_bytes() for path in (project["app"] / ".agents").rglob("*") if path.is_file()}
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        reply = scene.wait_query(family="skill-content", kind="derived", predicate=lambda value: any(
            row["name"] == "normalized_exact_duplicate_groups" and row["value"] == 1 for row in value["metrics"]))
        assert_measurements(context_view(reply, {"scope": "summary"}), literal("expected-positive-skill-content-summary.json"))
        for skill, filename in (("fixture-alpha", "expected-alpha-core-audit.json"),
                                ("fixture-beta", "expected-beta-core-audit.json")):
            core = context_view(reply, {"row_type": "skill", "skill": skill})
            assert_measurements(core, literal(filename))
        alpha = context_view(reply, {"row_type": "skill", "skill": "fixture-alpha"})
        assert any(row["context"].get("unreachable_paths") == [".agents/skills/fixture-alpha/orphan.md"]
                   for row in alpha["metrics"])
        duplicate_digest = hashlib.sha256(b"# Ref").hexdigest()
        digests = metadata_values(reply, "normalized_digest") + metadata_values(reply, "normalized_content_sha256")
        assert duplicate_digest in digests
        scene.close()
        assert {str(path): path.read_bytes() for path in (project["app"] / ".agents").rglob("*") if path.is_file()} == originals
    finally:
        scene.close()


def test_configured_threshold_qualifies_the_actual_sample_without_rewriting_skills(project, tmp_path):
    scene = skill_scene(project, tmp_path, with_policy=True, start=False, minimum_sessions=1)
    originals = {str(path): path.read_bytes() for path in (project["app"] / ".agents").rglob("*") if path.is_file()}
    try:
        scene.native("SessionStart")
        scene.native("Stop")
        result = scene.wait_query(family="skill-content", kind="derived", predicate=lambda value: any(
            row["name"] == "skills_measured" and row["value"] == 2 for row in value["metrics"]))
        assert_measurements(context_view(result, {"scope": "summary"}), literal("expected-qualified-skill-summary.json"))
        for expected in literal("expected-qualified-skill-contexts.json"):
            skill = expected["skill"]
            selected = context_view(result, {"row_type": "skill", "skill": skill})
            for key, value in expected.items():
                assert any(row["context"].get(key) == value for row in selected["metrics"]), (skill, key)
        scene.close()
        assert {str(path): path.read_bytes() for path in (project["app"] / ".agents").rglob("*") if path.is_file()} == originals
    finally:
        scene.close()


def test_selected_skill_without_observed_core_read_is_recorded_as_selected_not_read(project, tmp_path):
    scene = skill_scene(project, tmp_path, with_policy=True, source="skill-selection-no-core")
    try:
        scene.native("Stop")
        reply = scene.wait_query(family="skill-selection", kind="derived", predicate=lambda value: any(
            row["name"] == "selected_not_read_instances" and row["value"] == 1 for row in value["metrics"]))
        selected = context_view(reply, {"skill": "fixture-alpha"})
        assert_measurements(selected, literal("expected-selected-not-read.json"))
        assert any(row["context"].get("core_read") is False for row in selected["metrics"])
    finally:
        scene.close()
