"""Command line interface.

The argument surface is a superset of the original tool, so existing command
lines keep working::

    stegosaurus <carrier> -r
    stegosaurus <carrier> -p "payload"
    stegosaurus <carrier> -s -p "payload" -vv
    stegosaurus <carrier>.pyc -x

Added on top of the original: ``--payload-file`` for binary payloads,
``-o/--output`` for an explicit destination and ``--version``.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__, stego
from .errors import CarrierError, PayloadError, PayloadTooLargeError, StegosaurusError

PROG = "stegosaurus"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_TOO_LARGE = 2
EXIT_CARRIER = 3


class _Fatal(Exception):
    """A usage/validation problem; printed in the original tool's style."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Embed and extract payloads inside Python bytecode without changing "
        "the carrier's size or runtime behaviour.",
        epilog="Payloads occupy bytes of instructions that take no argument; nothing else "
        "in the file changes.",
    )
    parser.add_argument("carrier", help="Carrier py, pyc or pyo file")
    parser.add_argument("-p", "--payload", help="Embed payload in carrier file")
    parser.add_argument(
        "--payload-file",
        metavar="PATH",
        help="Embed the raw contents of PATH (binary safe; alternative to -p)",
    )
    parser.add_argument(
        "-r",
        "--report",
        action="store_true",
        help="Report max available payload size carrier supports",
    )
    parser.add_argument(
        "-s",
        "--side-by-side",
        action="store_true",
        help="Do not overwrite carrier file, install side by side instead.",
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase verbosity once per use"
    )
    parser.add_argument(
        "-x", "--extract", action="store_true", help="Extract payload from carrier file"
    )
    parser.add_argument(
        "-e",
        "--explode",
        type=int,
        default=None,
        metavar="N",
        help="Skip slots that follow N consecutive printable bytes, so the payload never "
        "shows up as a readable string",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="Write the carrier with the payload to PATH instead of overwriting it",
    )
    parser.add_argument("-V", "--version", action="version", version=f"{PROG} {__version__}")
    return parser


def _init_logger(verbosity: int) -> logging.Logger:
    logger = logging.getLogger("stegosaurus")
    logger.handlers.clear()
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    if verbosity >= 2:
        logger.setLevel(logging.DEBUG)
    elif verbosity == 1:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)
    return logger


def _validate(args: argparse.Namespace, logger: logging.Logger) -> float:
    """Reject impossible argument combinations; return the effective ``explode`` value."""
    suffix = Path(args.carrier).suffix
    if suffix not in stego.ALLOWED_SUFFIXES:
        raise _Fatal(
            "Carrier file must be one of the following types: "
            f"{set(stego.ALLOWED_SUFFIXES)}, got: {suffix!r}"
        )

    if args.payload is not None and args.payload_file is not None:
        raise _Fatal("Use either -p/--payload or --payload-file, not both")

    if args.payload is None and args.payload_file is None:
        if not args.report and not args.extract:
            raise _Fatal("Unless -r or -x are specified, a payload is required")

    if args.extract or args.report:
        if args.payload is not None or args.payload_file is not None:
            logger.warning("Payload is ignored when -x or -r is specified")
        if args.side_by_side or args.output:
            logger.warning("Side by side is ignored when -x or -r is specified")

    if args.explode is not None and args.explode < 1:
        raise _Fatal("Values for -e must be positive integers")

    logger.debug("Validated args")
    return math.inf if args.explode is None else float(args.explode)


def _read_payload(args: argparse.Namespace) -> bytes:
    if args.payload_file is not None:
        path = Path(args.payload_file)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise PayloadError(f"cannot read payload file {path}: {exc}") from exc
    return args.payload.encode("utf-8")


def run(args: argparse.Namespace) -> int:
    logger = _init_logger(args.verbose)
    explode_after = _validate(args, logger)

    carrier = stego.resolve_carrier(args.carrier, logger=logger)

    if args.extract:
        payload = stego.extract(carrier, explode_after=explode_after, logger=logger)
        print(f"Extracted payload: {stego.decode_payload(payload)}")
        return EXIT_OK

    analysis = stego.analyse(carrier, explode_after=explode_after, logger=logger)

    if args.report:
        print(analysis.report())
        return EXIT_OK

    payload = _read_payload(args)
    if len(payload) > analysis.capacity:
        raise PayloadTooLargeError(analysis.capacity, len(payload))

    result = stego.embed(
        carrier,
        payload,
        output=args.output,
        side_by_side=args.side_by_side,
        explode_after=explode_after,
        logger=logger,
    )
    print(result.report())
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return run(args)
    except _Fatal as exc:
        print(f"Fatal error: {exc}\nUse -h or --help for usage", file=sys.stderr)
        return EXIT_USAGE
    except PayloadTooLargeError as exc:
        print(f"Fatal error: {exc}\nUse -h or --help for usage", file=sys.stderr)
        return EXIT_TOO_LARGE
    except CarrierError as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        return EXIT_CARRIER
    except StegosaurusError as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
