"""Small application ports for replaceable maintenance result handling."""

from collections.abc import Callable, Mapping
from subprocess import CompletedProcess
from typing import Protocol, TypedDict


type InvokeCommand = Callable[[str], CompletedProcess[bytes]]


class OperationContext(TypedDict):
    operation: str
    dry_run: bool


class ResultHandler(Protocol):
    def __call__(
        self, initial: CompletedProcess[bytes], invoke: InvokeCommand,
        options: Mapping[str, object], context: OperationContext,
    ) -> dict[str, object]: ...
