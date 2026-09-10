import pytest
from harness.modules.content.domain import SectionRule, SectionBook
from harness.modules.foundation.errors import DomainError


def rule(name="report", required=True, normalization="strip"):
    return SectionRule(name, "Заполнить", normalization, required)


def test_preserves_raw_text_and_classifies_normalized_content():
    value = SectionBook((rule(),)).prepare({"report": "  Результат\n"})[0]
    assert value.name == "report"
    assert value.content == "  Результат\n"
    assert value.state == "populated"


@pytest.mark.parametrize("text", ["", "  ", " Заполнить\n"])
def test_required_empty_or_template_is_rejected(text):
    with pytest.raises(DomainError, match="report"):
        SectionBook((rule(),)).prepare({"report": text})


def test_optional_sections_still_have_explicit_content_state():
    book = SectionBook((rule("one", False), rule("two", False)))
    got = book.prepare({"one": "  ", "two": "Заполнить"})
    assert [x.state for x in got] == ["empty", "template"]


def test_normalization_is_explicit_not_an_implicit_strip():
    value = SectionBook((rule(normalization="exact"),)).prepare({"report": " Заполнить "})[0]
    assert value.state == "populated"


@pytest.mark.parametrize("values", [{}, {"report":"x", "extra":"y"}, {"report":17}])
def test_exact_section_contract(values):
    with pytest.raises(DomainError):
        SectionBook((rule(),)).prepare(values)


def test_same_content_library_handles_different_section_names():
    got = SectionBook((rule("conclusions"), rule("plan"))).prepare({"plan":"p", "conclusions":"c"})
    assert [(x.name, x.content) for x in got] == [("conclusions","c"), ("plan","p")]


def test_invalid_normalization_or_duplicate_rule_has_no_fallback():
    with pytest.raises(DomainError):
        SectionRule("report", "template", "guess", True)
    with pytest.raises(DomainError):
        SectionBook((rule(), rule()))


def test_missing_normalization_in_process_is_not_defaulted(project):
    import json
    from conftest import Harness
    from harness.common import HarnessError
    path=project["root"]/"config/processes/development.json"
    process=json.loads(path.read_text())
    del process["stages"][0]["normalization"]
    path.write_text(json.dumps(process))
    with pytest.raises(HarnessError,match="normalization"):
        Harness(project["config_path"],"s")
