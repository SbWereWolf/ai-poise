from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_canonical_session_contract_and_entry_are_documented_in_russian():
    runtime = read("docs/configuration/runtime-services.md")
    setup = read("docs/configuration/project-setup.md")
    wsl = read("docs/configuration/wsl-local-delivery.md")

    for phrase in (
        "единая граница",
        "CODEX_SESSION_ID",
        "POISE_CALLER_BINDING",
        "коллизи",
        "read-only",
        "равн",
        "RD-002",
    ):
        assert phrase in runtime
    assert "POISE_SESSION" not in runtime
    assert "POISE_SESSION" not in setup
    assert "POISE_SESSION" not in wsl


def test_agent_guidance_forbids_session_substitution_and_preserves_role_handoff():
    root_rules = read("AGENTS.md")
    source_rules = read("src/AGENTS.md")
    workflow = read(".agents/skills/poise/SKILL.md")
    development = read(".agents/skills/poise-development/SKILL.md")

    combined = "\n".join((root_rules, source_rules, workflow, development))
    assert "POISE_CALLER_BINDING" in combined
    assert "must not substitute" in combined
    assert "handoff" in workflow and "bootstrap" in workflow
    assert "independent review" in development


def test_runtime_and_library_docs_identify_one_owner_and_current_limits():
    hooks = read("docs/configuration/runtime-hooks.md")
    library = read("docs/architecture/library-api.md")
    combined = hooks + "\n" + library

    assert "SessionEstablisher" in combined
    assert "runtime_bindings" in combined
    assert "anti-tamper" in combined
    assert "не даёт" in combined or "не предоставляет" in combined

