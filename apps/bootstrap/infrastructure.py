"""Explicit file settings, standard template rendering and process mechanics."""

from dataclasses import dataclass
import importlib
import json
from pathlib import Path
from string import Template
from subprocess import CompletedProcess, run
from typing import cast

from .contracts import ResultHandler


@dataclass(frozen=True)
class CommandConfiguration:
    raw: dict

    @classmethod
    def load(cls, path: Path) -> "CommandConfiguration":
        return cls.from_bytes(path.read_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes) -> "CommandConfiguration":
        return cls.from_mapping(json.loads(raw.decode("utf-8")))

    @classmethod
    def from_mapping(cls, raw: dict) -> "CommandConfiguration":
        if not isinstance(raw, dict):
            raise ValueError("Конфигурация команды должна быть JSON-объектом")
        parameters, commands, handler = raw["parameters"], raw["commands"], raw["handler"]
        if not isinstance(parameters, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in parameters.items()
        ):
            raise ValueError("parameters должны содержать явные строковые значения")
        if not isinstance(commands, dict) or not commands:
            raise ValueError("commands должны содержать явные шаблоны аргументов")
        for name, arguments in commands.items():
            if not isinstance(name, str) or not isinstance(arguments, list) or not arguments:
                raise ValueError("Каждая команда должна иметь имя и список аргументов")
            if any(not isinstance(argument, str) for argument in arguments):
                raise ValueError("Аргументы шаблона должны быть строками")
        if not isinstance(handler, dict) or not isinstance(handler["callable"], str):
            raise ValueError("handler.callable должен указывать module:callable")
        if not isinstance(handler["options"], dict):
            raise ValueError("handler.options должен быть явным JSON-объектом")
        return cls(raw)

    @property
    def handler_selector(self) -> str:
        return self.raw["handler"]["callable"]

    @property
    def handler_options(self) -> dict:
        return self.raw["handler"]["options"]

    def rendered_commands(self, catalog_dir: str | None = None, *, values: dict | None = None) -> dict[str, list[str]]:
        values = {**self.raw["parameters"], **(values or {})}
        if catalog_dir is not None:
            values["catalog_dir"] = catalog_dir
        return {
            name: [Template(argument).substitute(values) for argument in arguments]
            for name, arguments in self.raw["commands"].items()
        }


def load_handler(selector: str) -> ResultHandler:
    module_name, attribute = selector.split(":", 1)
    selected = getattr(importlib.import_module(module_name), attribute)
    if not callable(selected):
        raise ValueError(f"Выбранный обработчик не является callable: {selector}")
    return cast(ResultHandler, selected)


@dataclass(frozen=True)
class CommandExecutor:
    commands: dict[str, list[str]]
    forwarded: list[str]
    cwd: Path

    def run(self, name: str) -> CompletedProcess[bytes]:
        return run([*self.commands[name], *self.forwarded], cwd=self.cwd,
                   capture_output=True, check=False, shell=False)
