"""``.pyc`` container handling: PEP 552 headers, load, dump."""

from __future__ import annotations

import marshal
import py_compile
import struct

import pytest
import support

from stegosaurus import pyc, stego
from stegosaurus.errors import CarrierFormatError

MTIME = struct.pack("<I", 1_700_000_000)


def _blob(header: bytes, code=None) -> bytes:
    if code is None:
        code = support.load_code(support.compile_source(support.DATA_DIR / "carrier.py"))
    return header + marshal.dumps(code)


def test_modern_header_is_sixteen_bytes_and_keeps_magic(carrier):
    loaded = pyc.load(support.compile_source(carrier))
    assert loaded.header.size == 16
    assert not loaded.header.legacy
    assert loaded.header.magic == pyc.interpreter_magic()
    assert loaded.path.read_bytes()[:16] == loaded.header.raw


def test_timestamp_and_hash_headers_both_parse(tmp_path):
    timestamp = pyc.load(support.compile_source(support.copy_data_carrier(tmp_path, "carrier.py")))
    assert timestamp.header.timestamp_based
    assert not timestamp.header.hash_based

    hashed_path = py_compile.compile(
        str(tmp_path / "carrier.py"),
        cfile=str(tmp_path / "hash.pyc"),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
    )
    hashed = pyc.load(hashed_path)
    assert hashed.header.hash_based
    assert not hashed.header.timestamp_based
    assert hashed.header.size == 16


def test_legacy_12_byte_header_is_recognised():
    """Python <= 3.6 has no bit field; those four bytes are the mtime."""
    prefix = b"0d0d\x0a"[:4] + MTIME + MTIME
    header = pyc.PycHeader.parse(prefix)
    assert header.legacy is True
    assert header.size == 12
    assert "legacy" in header.describe()


def test_hash_based_header_survives_a_rewrite(tmp_path):
    hashed_path = tmp_path / "hash.pyc"
    py_compile.compile(
        str(support.copy_data_carrier(tmp_path, "roomy.py")),
        cfile=str(hashed_path),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
    )
    before = hashed_path.read_bytes()[:16]
    stego.embed(hashed_path, "ABCD")
    assert hashed_path.read_bytes()[:16] == before


def test_load_rejects_files_that_are_not_bytecode(tmp_path):
    broken = tmp_path / "broken.pyc"
    broken.write_bytes(b"\xff" * 32)
    with pytest.raises(CarrierFormatError):
        pyc.load(broken)


def test_load_rejects_truncated_header(tmp_path):
    stub = tmp_path / "stub.pyc"
    stub.write_bytes(b"\x00\x00")
    with pytest.raises(CarrierFormatError, match="too short"):
        pyc.load(stub)


def test_load_explains_magic_mismatch(tmp_path):
    """A carrier built by another interpreter must fail with actionable advice."""
    code = support.load_code(support.compile_source(support.DATA_DIR / "carrier.py"))
    magic = int.from_bytes(pyc.interpreter_magic(), "little") ^ 0x0101_0101
    foreign_magic = magic.to_bytes(4, "little")  # guaranteed to differ
    path = tmp_path / "foreign.pyc"
    path.write_bytes(_blob(foreign_magic + struct.pack("<I", 0) + b"\x00" * 8, code))
    with pytest.raises(CarrierFormatError, match="different Python version"):
        pyc.load(path)


def test_dump_is_byte_identical_to_marshal_dump(tmp_path, carrier):
    """The writer must not perturb the marshal stream."""
    source = support.compile_source(carrier)
    loaded = pyc.load(source)
    output = pyc.dump(tmp_path / "copy.pyc", loaded.header, loaded.code)
    assert output.read_bytes() == source.read_bytes()


def test_split_round_trip(carrier):
    source = support.compile_source(carrier)
    header, payload = pyc.split(source.read_bytes())
    assert header.raw + payload == source.read_bytes()
    assert marshal.loads(payload).co_name == "<module>"
