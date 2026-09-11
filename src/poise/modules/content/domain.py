from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from ..foundation.errors import DomainError


class ContentState(StrEnum):
    EMPTY = "empty"
    TEMPLATE = "template"
    POPULATED = "populated"


@dataclass(frozen=True)
class SectionRule:
    name: str
    template: str
    normalization: str
    required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise DomainError("Секции требуется непустой идентификатор")
        if not isinstance(self.template, str) or type(self.required) is not bool:
            raise DomainError(f"Некорректное правило секции {self.name}")
        if self.normalization not in ("strip", "exact"):
            raise DomainError(f"Неизвестная normalization секции {self.name}")

    def normalize(self, text: str) -> str:
        return text.strip() if self.normalization == "strip" else text


@dataclass(frozen=True)
class SectionValue:
    name: str
    content: str
    state: ContentState


@dataclass(frozen=True)
class SectionBook:
    """Общий алгоритм секций; ничего не знает о Task, Sprint или I/O."""
    rules: tuple[SectionRule, ...]

    def __post_init__(self) -> None:
        if type(self.rules) is not tuple or any(not isinstance(r, SectionRule) for r in self.rules):
            raise DomainError("SectionBook требует неизменный набор SectionRule")
        names = [rule.name for rule in self.rules]
        if len(names) != len(set(names)):
            raise DomainError("Повтор идентификатора секции")

    def prepare(self, values: dict[str, str]) -> tuple[SectionValue, ...]:
        expected = {rule.name for rule in self.rules}
        if not isinstance(values, dict) or set(values) != expected:
            raise DomainError(f"Требуется точный набор секций: {sorted(expected)}")
        prepared = []
        for rule in self.rules:
            text = values[rule.name]
            if not isinstance(text, str):
                raise DomainError(f"Секция {rule.name} должна быть текстом")
            normalized = rule.normalize(text)
            state = (ContentState.EMPTY if normalized == "" else
                     ContentState.TEMPLATE if normalized == rule.normalize(rule.template) else
                     ContentState.POPULATED)
            if rule.required and state != ContentState.POPULATED:
                raise DomainError(f"Секция {rule.name} обязательна и не может оставаться шаблоном")
            prepared.append(SectionValue(rule.name, text, state))
        return tuple(prepared)
