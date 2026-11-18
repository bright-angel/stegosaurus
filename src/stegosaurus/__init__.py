"""Stegosaurus - payloads hidden inside CPython bytecode.

Public API::

    >>> from stegosaurus import analyse, embed, extract
    >>> analyse("example.py").capacity
    20
    >>> embed("example.py", "root pwd: 5+3g05aW")   # doctest: +SKIP
    >>> extract("__pycache__/example.cpython-313-stegosaurus.pyc")  # doctest: +SKIP
    b'root pwd: 5+3g05aW'

A modern rewrite of https://github.com/AngelKitty/stegosaurus (ISC, Jon Herron).
"""

from __future__ import annotations

from .deadzone import DeadZone, count_dead_zones, iter_dead_zones, iter_node_slots
from .errors import (
    CarrierError,
    CarrierFormatError,
    PayloadError,
    PayloadTooLargeError,
    StegosaurusError,
)
from .stego import (
    Analysis,
    Carrier,
    EmbedResult,
    analyse,
    capacity,
    decode_payload,
    embed,
    extract,
    resolve_carrier,
)

__version__ = "2.0.0"

__all__ = [
    "__version__",
    "analyse",
    "capacity",
    "embed",
    "extract",
    "decode_payload",
    "resolve_carrier",
    "Analysis",
    "Carrier",
    "EmbedResult",
    "DeadZone",
    "iter_dead_zones",
    "iter_node_slots",
    "iter_node_slots",
    "count_dead_zones",
    "StegosaurusError",
    "CarrierError",
    "CarrierFormatError",
    "PayloadError",
    "PayloadTooLargeError",
]
