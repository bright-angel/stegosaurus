"""Exception hierarchy for :mod:`stegosaurus`."""

from __future__ import annotations


class StegosaurusError(Exception):
    """Base class for every error raised by this package."""


class CarrierError(StegosaurusError):
    """The carrier file cannot be used (wrong extension, unreadable, ...)."""


class CarrierFormatError(CarrierError):
    """The carrier is not a ``.pyc`` file this interpreter can unmarshal."""


class PayloadError(StegosaurusError):
    """The payload is unusable (empty, not encodable, ...)."""


class PayloadTooLargeError(PayloadError):
    """The payload does not fit into the carrier's dead zones."""

    def __init__(self, capacity: int, size: int) -> None:
        super().__init__(
            "Carrier can only support a payload of "
            f"{capacity} bytes, payload of {size} bytes received"
        )
        self.capacity = capacity
        self.size = size
