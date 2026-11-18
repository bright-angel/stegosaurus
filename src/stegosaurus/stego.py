"""High level API: analyse, embed, extract."""

from __future__ import annotations

import logging
import math
import os
import py_compile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple, Union

from . import deadzone, layout, pyc
from .codegraph import CodeNode
from .deadzone import DeadZone
from .errors import CarrierError, PayloadTooLargeError
from .layout import CodeAnchor
from .pyc import PycHeader

__all__ = [
    "Carrier",
    "Analysis",
    "EmbedResult",
    "resolve_carrier",
    "analyse",
    "embed",
    "extract",
    "capacity",
    "decode_payload",
]

Payload = Union[str, bytes, bytearray, "os.PathLike[str]"]
PathLike = Union[str, "os.PathLike[str]"]

ALLOWED_SUFFIXES = (".py", ".pyc", ".pyo")
SIDE_BY_SIDE_INFIX = "-stegosaurus"

_log = logging.getLogger("stegosaurus")


@dataclass(frozen=True)
class Carrier:
    """The bytecode file a payload is read from / written to."""

    source: Path
    pyc: Path
    compiled: bool = False

    @property
    def suffix(self) -> str:
        return self.pyc.suffix


@dataclass(frozen=True)
class Analysis:
    """What a carrier can hold, without modifying anything."""

    carrier: Carrier
    header: PycHeader
    capacity: int
    code_objects: int
    size: int
    explode_after: float

    def report(self) -> str:
        return f"Carrier can support a payload of {self.capacity} bytes"


@dataclass(frozen=True)
class EmbedResult:
    """Outcome of a successful embed operation."""

    analysis: Analysis
    payload_size: int
    output: Path
    overwrote_carrier: bool

    def report(self) -> str:
        return "Payload embedded in carrier"


def _logger(logger: Optional[logging.Logger]) -> logging.Logger:
    return logger if logger is not None else _log


def resolve_carrier(path: PathLike, *, logger=None) -> Carrier:
    """Validate ``path`` and compile it first when it is a ``.py`` source file."""
    source = Path(path)
    log = _logger(logger)

    if not source.exists():
        raise CarrierError(f"carrier does not exist: {source}")
    if source.suffix not in ALLOWED_SUFFIXES:
        raise CarrierError(
            "Carrier file must be one of the following types: "
            f"{set(ALLOWED_SUFFIXES)}, got: {source.suffix!r}"
        )

    if source.suffix == ".py":
        try:
            compiled = py_compile.compile(str(source), doraise=True)
        except py_compile.PyCompileError as exc:  # pragma: no cover - syntax errors
            raise CarrierError(f"cannot compile {source}: {exc.msg}") from exc
        log.info("Compiled %s as %s for use as carrier", source, compiled)
        return Carrier(source=source, pyc=Path(compiled), compiled=True)

    return Carrier(source=source, pyc=source)


def _load(carrier: Carrier) -> Tuple[pyc.PycFile, CodeNode]:
    loaded = pyc.load(carrier.pyc)
    return loaded, CodeNode.from_code(loaded.code)


def _paired_blobs(loaded: pyc.PycFile, root: CodeNode) -> List[Tuple[CodeNode, CodeAnchor]]:
    """Code objects matched with the file region holding their bytecode."""
    nodes = list(root.walk())
    anchors = layout.locate_code_blobs(loaded.raw, loaded.code_offset)
    pairs = layout.pair_code_blobs(nodes, anchors)

    for node, anchor in pairs:
        span = loaded.raw[anchor.offset : anchor.offset + anchor.length]
        if anchor.length != len(node.bytecode) or span != node.original.co_code:
            raise CarrierError(f"internal error: bytecode blob of {node.qualname} does not match")
    return pairs


def _slot_stream(
    loaded: pyc.PycFile,
    root: CodeNode,
    *,
    explode_after: float = math.inf,
    include_inline_caches: bool = False,
) -> Iterator[DeadZone]:
    """Payload slots in writing order: reverse pre-order of physical blobs.

    A slot is a *file position*, so a bytecode run shared by several code
    objects is offered once.  Embedding and extraction both go through this
    function, which is what keeps the two in step.
    """
    pairs = _paired_blobs(loaded, root)
    for node, _anchor in reversed(pairs):
        yield from deadzone.iter_node_slots(
            node, explode_after=explode_after, include_inline_caches=include_inline_caches
        )


def _log_zones(slots: Sequence[DeadZone], log: logging.Logger) -> None:
    if not log.isEnabledFor(logging.DEBUG):
        return
    for slot in slots:
        log.debug("%s (%d)", slot.opname, slot.value)


def analyse(
    carrier: Union[PathLike, Carrier],
    *,
    explode_after: float = math.inf,
    include_inline_caches: bool = False,
    logger=None,
) -> Analysis:
    """Count the payload bytes ``carrier`` can hold without changing its size."""
    log = _logger(logger)
    resolved = carrier if isinstance(carrier, Carrier) else resolve_carrier(carrier, logger=log)
    loaded, root = _load(resolved)
    log.debug("Read header and bytecode from carrier")

    slots = list(
        _slot_stream(
            loaded, root, explode_after=explode_after, include_inline_caches=include_inline_caches
        )
    )
    _log_zones(slots, log)
    log.info("Found %d bytes available for payload", len(slots))

    return Analysis(
        carrier=resolved,
        header=loaded.header,
        capacity=len(slots),
        code_objects=sum(1 for _ in root.walk()),
        size=len(loaded.raw),
        explode_after=explode_after,
    )


def capacity(carrier, *, explode_after: float = math.inf, **kwargs) -> int:
    """Shorthand for ``analyse(...).capacity``."""
    return analyse(carrier, explode_after=explode_after, **kwargs).capacity


def _encode_payload(payload: Payload) -> bytes:
    if isinstance(payload, str):
        return payload.encode("utf-8")
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    return Path(payload).read_bytes()


def embed(
    carrier: Union[PathLike, Carrier],
    payload: Payload,
    *,
    output: Optional[PathLike] = None,
    side_by_side: bool = False,
    explode_after: float = math.inf,
    include_inline_caches: bool = False,
    logger=None,
) -> EmbedResult:
    """Write ``payload`` into the dead zones of ``carrier``.

    Nothing is re-serialised: payload bytes are patched straight into the
    carrier's marshal stream, so the file keeps its exact size and every byte
    outside a dead zone stays untouched.
    """
    log = _logger(logger)
    resolved = carrier if isinstance(carrier, Carrier) else resolve_carrier(carrier, logger=log)
    loaded, root = _load(resolved)
    log.debug("Read header and bytecode from carrier")
    pairs = _paired_blobs(loaded, root)

    data = _encode_payload(payload)
    slots = list(
        _slot_stream(
            loaded, root, explode_after=explode_after, include_inline_caches=include_inline_caches
        )
    )
    _log_zones(slots, log)
    log.info("Found %d bytes available for payload", len(slots))

    if len(data) > len(slots):
        raise PayloadTooLargeError(len(slots), len(data))

    for index, slot in enumerate(slots):
        slot.write(data[index] if index < len(data) else 0)
    log.info("Payload embedded in carrier")
    _log_zones(slots, log)

    blob = bytearray(loaded.raw)
    for node, anchor in pairs:
        blob[anchor.offset : anchor.offset + anchor.length] = node.bytecode

    if output is not None:
        target = Path(output)
        overwrote = target.resolve() == loaded.path.resolve()
    elif side_by_side:
        target = loaded.path.with_name(
            f"{loaded.path.stem}{SIDE_BY_SIDE_INFIX}{loaded.path.suffix}"
        )
        overwrote = False
        log.debug("Creating new carrier file name for side-by-side install")
    else:
        target = loaded.path
        overwrote = True

    pyc.write(target, bytes(blob))
    log.info("Wrote carrier file as %s", target)

    analysis = Analysis(
        carrier=resolved,
        header=loaded.header,
        capacity=len(slots),
        code_objects=sum(1 for _ in root.walk()),
        size=len(blob),
        explode_after=explode_after,
    )
    return EmbedResult(
        analysis=analysis, payload_size=len(data), output=target, overwrote_carrier=overwrote
    )


def extract(
    carrier: Union[PathLike, Carrier],
    *,
    explode_after: float = math.inf,
    include_inline_caches: bool = False,
    logger=None,
) -> bytes:
    """Read the payload back out of ``carrier``."""
    log = _logger(logger)
    resolved = carrier if isinstance(carrier, Carrier) else resolve_carrier(carrier, logger=log)
    loaded, root = _load(resolved)
    log.debug("Read header and bytecode from carrier")

    collected = bytearray()
    for slot in _slot_stream(
        loaded, root, explode_after=explode_after, include_inline_caches=include_inline_caches
    ):
        value = slot.value
        if value == 0:
            break
        collected.append(value)
    return bytes(collected)


def decode_payload(data: bytes) -> str:
    """Best-effort textual rendering of an extracted payload (CLI helper).

    Printable UTF-8 is returned as is; anything else is escaped so that binary
    payloads and control characters never reach the terminal raw.
    """
    if not data:
        return ""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None and text.isprintable():
        return text
    escaped = "".join(chr(byte) if 32 <= byte < 127 else f"\\x{byte:02x}" for byte in data)
    return f"{escaped} (raw bytes: {len(data)})"
