"""A mutable view over a tree of :class:`types.CodeType` objects.

The original ``stegosaurus`` rebuilt code objects with the positional
``types.CodeType(...)`` constructor, which changed signature in 3.8 (added
``posonlyargcount``), 3.11 (``co_qualname``/``co_exceptiontable``) and again in
3.12/3.13.  ``CodeType.replace()`` (added in 3.8) is the supported, forward
compatible way to derive a new code object from an existing one, so this module
only ever touches the two fields we need: ``co_code`` and ``co_consts``.
"""

from __future__ import annotations

from types import CodeType
from typing import Iterator, List, Union

__all__ = ["CodeNode"]

#: A constant is either a nested code node or any other constant (immutable).
Const = Union["CodeNode", object]


class CodeNode:
    """One code object plus its (recursively wrapped) constants.

    ``bytecode`` is a :class:`bytearray` copy of ``co_code`` and is the only
    thing callers mutate.  ``consts`` mirrors ``co_consts`` positionally so
    nested functions/classes keep their identity and ordering.
    """

    __slots__ = ("original", "bytecode", "consts")

    def __init__(self, original: CodeType, bytecode: bytearray, consts: List[Const]) -> None:
        self.original = original
        self.bytecode = bytecode
        self.consts = consts

    # -- construction ----------------------------------------------------
    @classmethod
    def from_code(cls, code: CodeType) -> CodeNode:
        consts: List[Const] = [
            cls.from_code(const) if isinstance(const, CodeType) else const
            for const in code.co_consts
        ]
        return cls(code, bytearray(code.co_code), consts)

    # -- traversal -------------------------------------------------------
    def walk(self) -> Iterator[CodeNode]:
        """Pre-order DFS, matching the upstream implementation's stack order."""
        yield self
        for const in self.consts:
            if isinstance(const, CodeNode):
                yield from const.walk()

    def __iter__(self) -> Iterator[CodeNode]:
        return self.walk()

    # -- rebuilding ------------------------------------------------------
    @property
    def is_modified(self) -> bool:
        return bytes(self.bytecode) != self.original.co_code

    def rebuild(self) -> CodeType:
        """Return a code object carrying the current bytecode/constants."""
        consts = tuple(
            const.rebuild() if isinstance(const, CodeNode) else const for const in self.consts
        )
        return self.original.replace(co_code=bytes(self.bytecode), co_consts=consts)

    # -- reporting -------------------------------------------------------
    @property
    def qualname(self) -> str:
        return getattr(self.original, "co_qualname", self.original.co_name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CodeNode {self.qualname} code={len(self.bytecode)}B consts={len(self.consts)}>"
