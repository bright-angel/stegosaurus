"""Reading and writing ``.pyc`` containers (PEP 552).

A ``.pyc`` file is a header followed by a marshal stream::

    +0   magic number   4 bytes   version specific, ends with CR LF
    +4   bit field      4 bytes   3.7+ only (PEP 552)
    +8   mtime+size     8 bytes   timestamp based .pyc, or source hash
    +16  marshal stream ...

The header is preserved verbatim so that a rewritten carrier keeps the exact
same invalidation metadata as the file it was derived from.
"""

from __future__ import annotations

import marshal
import os
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import CodeType
from typing import Tuple, Union

from .errors import CarrierFormatError

__all__ = ["PycHeader", "PycFile", "load", "dump", "interpreter_magic"]

_MAGIC_SIZE = 4
_FLAGS = struct.Struct("<I")
_LEGACY_HEADER_SIZE = 12  # Python <= 3.6: magic, mtime, source size
_MODERN_HEADER_SIZE = 16  # Python >= 3.7: magic, flags, 8 bytes of invalidation data
_KNOWN_FLAGS = 0b11  # bit 0: hash based, bit 1: check the source

PathLike = Union[str, "os.PathLike[str]"]


def interpreter_magic() -> bytes:
    """The magic number of the interpreter currently executing this code."""
    import importlib.util

    return importlib.util.MAGIC_NUMBER


@dataclass(frozen=True)
class PycHeader:
    """The invalidation header of a ``.pyc`` file."""

    raw: bytes
    magic: bytes
    flags: int
    legacy: bool = False

    @property
    def size(self) -> int:
        return len(self.raw)

    @property
    def hash_based(self) -> bool:
        """Bit 0 of the PEP 552 flag word; clear means a timestamp based file."""
        return not self.legacy and bool(self.flags & 0b01)

    @property
    def timestamp_based(self) -> bool:
        return not self.legacy and not self.hash_based

    @property
    def checked(self) -> bool:
        """Only meaningful for hash based files: bit 1 asks for source checking."""
        return self.hash_based and bool(self.flags & 0b10)

    @classmethod
    def parse(cls, prefix: bytes) -> PycHeader:
        """Parse the header from the first 12-16 bytes of a file."""
        if len(prefix) < _LEGACY_HEADER_SIZE:
            raise CarrierFormatError(
                f"file is too short to be a .pyc ({len(prefix)} bytes); expected a header of "
                f"at least {_LEGACY_HEADER_SIZE} bytes"
            )
        magic = prefix[:_MAGIC_SIZE]
        flags = _FLAGS.unpack_from(prefix, _MAGIC_SIZE)[0]
        if flags > _KNOWN_FLAGS:
            # Python <= 3.6 has no bit field: those 4 bytes are the mtime, which
            # is always a large number, so a "flag" value above 0b11 is a
            # reliable signal for the legacy 12 byte layout.
            return cls(prefix[:_LEGACY_HEADER_SIZE], magic, 0, legacy=True)
        if len(prefix) < _MODERN_HEADER_SIZE:
            raise CarrierFormatError(
                f"truncated .pyc header ({len(prefix)} bytes); expected {_MODERN_HEADER_SIZE}"
            )
        return cls(prefix[:_MODERN_HEADER_SIZE], magic, flags)

    def describe(self) -> str:
        if self.legacy:
            kind = "legacy timestamp (Python <= 3.6)"
        elif self.hash_based:
            kind = "hash based (checked)" if self.checked else "hash based (unchecked)"
        else:
            kind = "timestamp based"
        return f"{kind}, {self.size}-byte header"


@dataclass(frozen=True)
class PycFile:
    """A loaded carrier: header, root code object and the untouched bytes."""

    path: Path
    header: PycHeader
    code: CodeType
    raw: bytes

    @property
    def code_offset(self) -> int:
        """Offset of the first byte of the marshal stream."""
        return self.header.size

    @property
    def size(self) -> int:
        return len(self.raw)


def load(path: PathLike) -> PycFile:
    """Load ``path`` and unmarshal its root code object."""
    file = Path(path)
    try:
        blob = file.read_bytes()
    except OSError as exc:  # pragma: no cover - depends on the filesystem
        raise CarrierFormatError(f"cannot read carrier {file}: {exc}") from exc

    header = PycHeader.parse(blob[:_MODERN_HEADER_SIZE])
    magic = interpreter_magic()
    if header.magic != magic:
        raise CarrierFormatError(
            f"{file.name} was compiled by a different Python version: its magic number is "
            f"{header.magic.hex()}, this interpreter (Python "
            f"{sys.version_info.major}.{sys.version_info.minor}) uses {magic.hex()}. "
            "Marshal data is version specific; recompile the carrier with this interpreter."
        )

    try:
        code = marshal.loads(blob[header.size :])
    except (ValueError, EOFError, TypeError) as exc:
        raise CarrierFormatError(f"cannot unmarshal code object from {file.name}: {exc}") from exc

    if not isinstance(code, CodeType):
        raise CarrierFormatError(
            f"{file.name} does not contain a code object (found {type(code).__name__})"
        )

    return PycFile(path=file, header=header, code=code, raw=blob)


def write(path: PathLike, blob: bytes) -> Path:
    """Write ``blob`` to ``path`` atomically (temp file + rename)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".stegosaurus-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(blob)
        os.replace(tmp_name, target)
    except BaseException:  # pragma: no cover - defensive cleanup
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


def dump(path: PathLike, header: PycHeader, code: CodeType) -> Path:
    """Write ``header`` + marshalled ``code`` to ``path``.

    Note: the steganography writer does *not* use this, because re-marshalling a
    code object is not byte transparent.  It is provided for callers that need
    to materialise a code object as a ``.pyc`` file.
    """
    return write(path, header.raw + marshal.dumps(code))


def split(blob: bytes) -> Tuple[PycHeader, bytes]:
    """Split a raw ``.pyc`` blob into header and marshal payload."""
    header = PycHeader.parse(blob[:_MODERN_HEADER_SIZE])
    return header, blob[header.size :]
