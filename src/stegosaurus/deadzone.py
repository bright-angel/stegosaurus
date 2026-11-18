"""Dead zone discovery: the byte slots a payload may occupy.

Since Python 3.6 every instruction occupies two bytes ("wordcode").  Opcodes
that take no argument keep their second byte unused: CPython never reads it, so
overwriting it cannot change the behaviour of the program.  Those spare bytes
are the dead zones this tool writes the payload into.

Two properties are needed for a slot to be safe:

* the opcode byte is below ``opcode.HAVE_ARGUMENT`` (i.e. an opcode without
  argument), and
* the second byte is *ignored* by the interpreter.

The second point is where the modern rewrite differs from the 2017 original.
``CACHE`` (opcode 0, Python 3.11+) is below ``HAVE_ARGUMENT`` but its second
byte carries inline cache data - a version tag, a cached type pointer, a
counter - which the adaptive interpreter reads before it ever rewrites the
slot.  Treating it as free space corrupts the specialisation state, so inline
cache slots are excluded by default.  Python <= 3.10 emits no ``CACHE``
instructions, which is why the upstream implementation and this one enumerate
identical slots there (see the cross-validation suite).

Slot order is significant: extraction walks the same sequence that embedding
wrote, so the traversal must be stable across versions and must match upstream.
Upstream collected code objects depth-first and iterated the resulting list in
reverse, which means: reverse pre-order, nested code objects first, module
scope last.
"""

from __future__ import annotations

import math
import opcode
import string
from dataclasses import dataclass
from typing import Iterator, Optional

from .codegraph import CodeNode

__all__ = [
    "DeadZone",
    "iter_dead_zones",
    "iter_node_slots",
    "count_dead_zones",
    "INSTRUCTION_SIZE",
]

#: Bytes per instruction slot (wordcode, Python 3.6+).
INSTRUCTION_SIZE = 2

#: ``string.printable`` as a set of byte values -- used for the "explode" guard.
_PRINTABLE = frozenset(string.printable.encode("ascii"))
_HAVE_ARGUMENT = opcode.HAVE_ARGUMENT
#: Opcode of an inline cache entry, or ``None`` on interpreters without caches.
_CACHE_OPCODE: Optional[int] = opcode.opmap.get("CACHE")


@dataclass(frozen=True)
class DeadZone:
    """A single payload byte inside ``node.bytecode``.

    ``offset`` points at the ignored second byte of the instruction, so the
    opcode can always be found at ``offset - 1``.
    """

    node: CodeNode
    offset: int

    @property
    def opcode(self) -> int:
        return self.node.bytecode[self.offset - 1]

    @property
    def opname(self) -> str:
        return opcode.opname[self.opcode]

    @property
    def value(self) -> int:
        return self.node.bytecode[self.offset]

    def write(self, value: int) -> None:
        if not 0 <= value <= 0xFF:
            raise ValueError(f"payload byte out of range: {value}")
        self.node.bytecode[self.offset] = value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DeadZone {self.node.qualname}+{self.offset} {self.opname}={self.value}>"


def iter_node_slots(
    node: CodeNode,
    *,
    explode_after: float = math.inf,
    include_inline_caches: bool = False,
) -> Iterator[DeadZone]:
    """Yield the payload slots of a single code object, in ascending order."""
    code = node.bytecode
    printable_run = 0
    for offset in range(len(code)):
        if code[offset] in _PRINTABLE:
            printable_run += 1
        else:
            printable_run = 0

        if offset % INSTRUCTION_SIZE:
            continue
        if code[offset] >= _HAVE_ARGUMENT:
            continue
        if code[offset] == _CACHE_OPCODE and not include_inline_caches:
            continue
        if printable_run >= explode_after:
            # Reset the counter, exactly like the reference implementation:
            # the skipped slot also breaks the run it was guarding.
            printable_run = 0
            continue
        yield DeadZone(node, offset + 1)


def iter_dead_zones(
    root: CodeNode,
    *,
    explode_after: float = math.inf,
    include_inline_caches: bool = False,
) -> Iterator[DeadZone]:
    """Yield every payload slot of a code tree in payload order.

    Order is reverse pre-order: nested code objects first, module scope last.
    This mirrors the reference implementation and, more importantly, must be
    identical between embedding and extraction.

    :param explode_after: skip a slot when more than this many consecutive
        printable bytes precede it.  Long printable runs inside the bytecode
        (e.g. the characters of a short string constant) are what a naive
        ``strings`` scan would pick up; embedding there would expose the
        payload as a readable string.  ``math.inf`` disables the guard, which
        is upstream's default.
    :param include_inline_caches: also hand out ``CACHE`` slots.  Only useful
        to reproduce upstream's behaviour on Python 3.11+; unsafe in general.
    """
    for node in reversed(list(root.walk())):
        yield from iter_node_slots(
            node, explode_after=explode_after, include_inline_caches=include_inline_caches
        )


def count_dead_zones(root: CodeNode, **kwargs: object) -> int:
    """Number of bytes the code tree can hold, ignoring shared code blobs."""
    return sum(1 for _ in iter_dead_zones(root, **kwargs))  # type: ignore[arg-type]
