"""Locate ``co_code`` blobs inside a marshal stream.

Re-marshalling a reconstructed code object is not byte-transparent: ``marshal``
numbers its object table in traversal order, so a rebuilt code object can come
out longer than the original even when the bytecode is untouched.  Since the
whole point of this tool is that the carrier keeps its size, the writer never
re-serialises anything: it parses the marshal stream far enough to find the
payload area of every ``co_code`` and patches those bytes in place.

Only the structure needed to skip values correctly is implemented here -- no
code objects are materialised, no semantics are interpreted.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .errors import CarrierFormatError

__all__ = ["CodeAnchor", "locate_code_blobs"]

_FLAG_REF = 0x80
_VERSION = sys.version_info[:2]

#: Value types that occupy no payload bytes.
_EMPTY = frozenset("0NFT.S")  # NULL, None, False, True, Ellipsis, StopIteration
#: Types whose payload is a single 32 bit length followed by that many bytes.
_BLOB32 = "stuaA"
#: Types whose payload is a single byte length followed by that many bytes.
_BLOB8 = "zZ"
#: Container types with a 32 bit item count.
_CONTAINER32 = "([<>"


@dataclass(frozen=True)
class CodeAnchor:
    """Where a code object's bytecode lives inside the file."""

    offset: int
    length: int

    def span(self) -> range:
        return range(self.offset, self.offset + self.length)


class _Parser:
    """Walker for CPython's marshal format (version 4).

    ``marshal`` de-duplicates objects: a repeated ``co_code`` is written once
    and then referenced by index, which really happens when several functions
    compile to identical bytecode.  ``refs`` mirrors that table so such a slot
    resolves to the blob it points at.
    """

    __slots__ = ("blob", "pos", "anchors", "refs")

    def __init__(self, blob: bytes, start: int) -> None:
        self.blob = blob
        self.pos = start
        self.anchors: List[CodeAnchor] = []
        self.refs: List[Optional[CodeAnchor]] = []

    # -- primitives ------------------------------------------------------
    def _byte(self) -> int:
        if self.pos >= len(self.blob):
            raise CarrierFormatError("truncated marshal stream")
        value = self.blob[self.pos]
        self.pos += 1
        return value

    def _int32(self) -> int:
        raw = self.blob[self.pos : self.pos + 4]
        if len(raw) != 4:
            raise CarrierFormatError("truncated marshal stream")
        self.pos += 4
        return int.from_bytes(raw, "little", signed=True)

    def _skip(self, count: int) -> None:
        if count < 0 or self.pos + count > len(self.blob):
            raise CarrierFormatError("truncated marshal stream")
        self.pos += count

    # -- values ----------------------------------------------------------
    def read_object(self) -> None:
        kind_byte = self._byte()
        kind = chr(kind_byte & 0x7F)
        if kind_byte & _FLAG_REF:
            self.refs.append(None)  # a slot per referenced object keeps indices aligned

        if kind in _EMPTY:
            return
        if kind == "c":
            self._read_code()
        elif kind == "i":
            self._skip(4)
        elif kind == "I":
            self._skip(8)
        elif kind == "l":  # long: digit count, then 2 bytes per 15 bit digit
            self._skip(abs(self._int32()) * 2)
        elif kind == "f":  # legacy float: length prefixed digits
            self._skip(self._byte())
        elif kind == "g":
            self._skip(8)
        elif kind == "x":
            self._skip(16)
        elif kind == "y":  # legacy complex
            for _ in range(2):
                self._skip(self._byte())
        elif kind in _BLOB32:
            self._skip(self._int32())
        elif kind in _BLOB8:
            self._skip(self._byte())
        elif kind in _CONTAINER32:
            for _ in range(self._int32()):
                self.read_object()
        elif kind == ")":
            for _ in range(self._byte()):
                self.read_object()
        elif kind == "r":
            self._follow_reference()
        elif kind == "{":
            while True:
                if self.pos >= len(self.blob):
                    raise CarrierFormatError("truncated marshal dict")
                if self.blob[self.pos] == ord("0"):  # TYPE_NULL terminates
                    self.pos += 1
                    return
                self.read_object()  # key
                self.read_object()  # value
        else:
            raise CarrierFormatError(f"unsupported marshal type code {kind!r}")

    def _read_code(self) -> None:
        """Walk one code object, following ``w_object`` in CPython's marshal.c.

        The layout changed twice and is version gated below:

        * 3.8 - 3.10 write six leading longs (``co_nlocals`` is still stored)
          followed by ``co_varnames``/``co_freevars``/``co_cellvars``.
        * 3.11+ drops ``co_nlocals``, merges the three local tables into
          ``co_localsplusnames`` + ``co_localspluskinds`` and appends
          ``co_qualname`` and ``co_exceptiontable``.
        """
        modern = _VERSION >= (3, 11)
        leading = 5 if modern else 6  # argcount, posonly, kwonly, [nlocals], stacksize, flags
        for _ in range(leading):
            self._int32()
        self._read_bytecode()
        # consts, names, (varnames, freevars, cellvars | localsplusnames, localspluskinds),
        # filename, name, qualname -> always seven values
        for _ in range(7):
            self.read_object()
        self._int32()  # firstlineno
        self.read_object()  # linetable (co_lnotab before Python 3.10)
        if modern:
            self.read_object()  # exceptiontable

    def _read_bytecode(self) -> None:
        start = self.pos
        kind_byte = self.blob[start] if start < len(self.blob) else 0  # type: ignore[index]
        kind = chr(kind_byte & 0x7F)

        if kind in "st":  # bytes / interned bytes
            index = None
            if kind_byte & _FLAG_REF:
                index = len(self.refs)
                self.refs.append(None)
            length = int.from_bytes(self.blob[start + 1 : start + 5], "little", signed=True)
            data_offset = start + 5
            if length < 0 or data_offset + length > len(self.blob):
                raise CarrierFormatError("corrupt co_code length in marshal stream")
            self.pos = data_offset + length
            anchor = CodeAnchor(offset=data_offset, length=length)
            self.anchors.append(anchor)
            if index is not None:
                self.refs[index] = anchor
            return
        # co_code stored as a back reference: the blob it points at is reused.
        self.read_object()

    def _follow_reference(self) -> None:
        target = self._int32()
        if not 0 <= target < len(self.refs):
            raise CarrierFormatError(f"marshal reference {target} is out of range")
        anchor = self.refs[target]
        if anchor is not None:
            self.anchors.append(anchor)


def locate_code_blobs(blob: bytes, offset: int) -> List[CodeAnchor]:
    """Return every ``co_code`` location in ``blob``, in marshal order.

    ``offset`` is where the marshal stream starts (past the ``.pyc`` header).
    Marshal writes a code object before the code objects nested in its
    constants, so this order equals a pre-order walk of the code tree.
    """
    parser = _Parser(blob, offset)
    parser.read_object()
    if not parser.anchors:
        raise CarrierFormatError("no code object found in marshal stream")
    if parser.pos != len(blob):
        # A correct parse consumes the stream exactly; anything else means the
        # offsets we would patch are guesses, and guessing corrupts carriers.
        raise CarrierFormatError(
            f"marshal stream ends at {parser.pos} but the file has {len(blob)} bytes "
            f"({len(blob) - parser.pos} trailing bytes)"
        )
    return parser.anchors


def pair_code_blobs(
    nodes: Sequence, anchors: Sequence[CodeAnchor]
) -> List[Tuple[object, CodeAnchor]]:
    """Pair each code object with the file region that holds its bytecode.

    Both sequences are in pre-order, so pairing is positional.  Code objects
    that share a ``co_code`` (marshal emits it once and references it) collapse
    onto the first occurrence: physically there is only one run of bytes, so it
    must only be offered to the payload once.  ``stego`` verifies the bytes
    afterwards, which makes a mis-pairing impossible to miss.
    """
    if len(nodes) != len(anchors):
        raise CarrierFormatError(
            f"found {len(anchors)} bytecode blobs for {len(nodes)} code objects"
        )
    paired: List[Tuple[object, CodeAnchor]] = []
    seen = set()
    for node, anchor in zip(nodes, anchors):
        key = (anchor.offset, anchor.length)
        if key in seen:
            continue
        seen.add(key)
        paired.append((node, anchor))
    return paired
