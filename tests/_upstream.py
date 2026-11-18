"""Oracle harness around the *unmodified* upstream implementation.

Upstream (https://github.com/AngelKitty/stegosaurus, 2017) targets Python 3.6.
Two interpreter details changed since then and are shimmed here -- nothing else:

``_loadBytecode``
    upstream does ``f.read(12)``; the ``.pyc`` header became 16 bytes in 3.7
    (PEP 552), which desynchronises ``marshal.load``.  Same function, one
    constant changed.

``_toCodeType``
    upstream builds a code object from 15 positional ``types.CodeType``
    arguments; that signature changed in 3.8 and again in 3.11/3.12.  Replaced
    with the equivalent ``CodeType.replace()`` recursion.

Everything that decides *whether*, *where* and *in which order* a payload byte
is written stays untouched and is executed straight from the upstream source:
``MutableBytecode``, ``_createMutableBytecodeStack``,
``_bytesAvailableForPayload``, ``_embedPayload``, ``_extractPayload``,
``_maxSupportedPayloadSize``, ``_getCarrierFile``, ``_dumpBytecode``.

Inline cache slots (``CACHE``, Python 3.11+) are optionally filtered out of the
slot stream by wrapping ``_bytesAvailableForPayload`` with a pass-through
filter; the wrapper only drops slots, it never reorders or invents them.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import logging
import marshal
import math
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

_SEARCH_PATHS = (
    "upstream-ref/stegosaurus.py",
    "../upstream-ref/stegosaurus.py",
    "tools/upstream/stegosaurus.py",
    "vendor/upstream/stegosaurus.py",
)


def discover() -> Optional[Path]:
    """Locate the upstream source, honouring ``$STEGOSAURUS_UPSTREAM``."""
    override = os.environ.get("STEGOSAURUS_UPSTREAM")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate
    for relative in _SEARCH_PATHS:
        candidate = REPO_ROOT / relative
        if candidate.is_file():
            return candidate
    return None


def _quiet_logger() -> logging.Logger:
    logger = logging.getLogger("upstream-stegosaurus")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.CRITICAL)
    logger.propagate = False
    return logger


class UpstreamOracle:
    """Drive upstream's internals the way upstream's ``main()`` does."""

    def __init__(self, source: Path) -> None:
        self.source = Path(source)
        self.module = self._load(self.source)
        self.logger = _quiet_logger()
        self._install_compat()

    # -- loading ---------------------------------------------------------
    @staticmethod
    def _load(source: Path):
        spec = importlib.util.spec_from_file_location("upstream_stegosaurus", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # upstream guards main() behind __name__
        return module

    def _install_compat(self) -> None:
        module = self.module
        mutable_bytecode = module.MutableBytecode

        def load_bytecode(carrier, logger):  # upstream: f.read(12)
            with open(carrier, "rb") as handle:
                header = handle.read(16)
                code = marshal.load(handle)
            return header, code

        def to_code_type(mutable):
            consts = tuple(
                to_code_type(const) if isinstance(const, mutable_bytecode) else const
                for const in mutable.consts
            )
            return mutable.originalCode.replace(co_code=bytes(mutable.bytes), co_consts=consts)

        module._loadBytecode = load_bytecode
        module._toCodeType = to_code_type

    # -- cache filter ----------------------------------------------------
    @contextlib.contextmanager
    def _filter_inline_caches(self, enabled: bool):
        module = self.module
        if not enabled:
            yield
            return
        original = module._bytesAvailableForPayload

        def filtered(stack, explodeAfter, logger=None):
            for buffer, index in original(stack, explodeAfter, logger):
                if buffer[index - 1] == 0:  # inline cache entry, not an instruction
                    continue
                yield buffer, index

        module._bytesAvailableForPayload = filtered
        try:
            yield
        finally:
            module._bytesAvailableForPayload = original

    # -- plumbing --------------------------------------------------------
    def _open(self, carrier, explode_after):
        module = self.module
        args = SimpleNamespace(
            carrier=str(carrier),
            payload=None,
            report=False,
            side_by_side=False,
            verbose=0,
            extract=False,
            explode=explode_after,
        )
        pyc_path = module._getCarrierFile(args, self.logger)
        header, code = module._loadBytecode(pyc_path, self.logger)
        mutable = module.MutableBytecode(code)
        stack = module._createMutableBytecodeStack(mutable)
        return pyc_path, header, mutable, stack

    # -- public API ------------------------------------------------------
    def slots(
        self, carrier, *, explode_after: float = math.inf, filter_caches: bool = True
    ) -> List[Tuple[str, int]]:
        """Upstream's payload slot stream as ``(code_object_name, offset)``."""
        with self._filter_inline_caches(filter_caches):
            _, _, mutable, stack = self._open(carrier, explode_after)
            names = {id(node.bytes): node.originalCode.co_name for node in stack}
            return [
                (names[id(buffer)], index)
                for buffer, index in self.module._bytesAvailableForPayload(
                    stack, explode_after, self.logger
                )
            ]

    def capacity(
        self, carrier, *, explode_after: float = math.inf, filter_caches: bool = True
    ) -> int:
        with self._filter_inline_caches(filter_caches):
            _, _, _, stack = self._open(carrier, explode_after)
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                return self.module._maxSupportedPayloadSize(stack, explode_after, self.logger)

    def embed(
        self,
        carrier,
        payload: str,
        *,
        side_by_side: bool = True,
        explode_after: float = math.inf,
        filter_caches: bool = True,
    ) -> Tuple[Path, str]:
        """Embed ``payload``; return ``(output_path, stdout)``."""
        with self._filter_inline_caches(filter_caches):
            pyc_path, header, mutable, stack = self._open(carrier, explode_after)
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                self.module._embedPayload(stack, payload, explode_after, self.logger)
                if side_by_side:
                    base, ext = os.path.splitext(pyc_path)
                    pyc_path = f"{base}-stegosaurus{ext}"
                code = self.module._toCodeType(mutable)
                self.module._dumpBytecode(header, code, pyc_path, self.logger)
            return Path(pyc_path), captured.getvalue()

    def extract(
        self, carrier, *, explode_after: float = math.inf, filter_caches: bool = True
    ) -> str:
        with self._filter_inline_caches(filter_caches):
            _, _, _, stack = self._open(carrier, explode_after)
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                self.module._extractPayload(stack, explode_after, self.logger)
            text = captured.getvalue()
        prefix = "Extracted payload: "
        return text[len(prefix) :].rstrip("\n") if text.startswith(prefix) else text


def load_oracle() -> Optional[UpstreamOracle]:
    source = discover()
    return UpstreamOracle(source) if source else None


def iter_upstream_slots(oracle: UpstreamOracle, carrier) -> Iterator[Tuple[str, int]]:
    return iter(oracle.slots(carrier))
