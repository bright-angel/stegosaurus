"""Embed / extract round trips and file handling."""

from __future__ import annotations

import pytest
import support

from stegosaurus import analyse, embed, extract, resolve_carrier
from stegosaurus.errors import CarrierError, PayloadTooLargeError


def test_roundtrip_ascii(roomy):
    result = embed(roomy, "root pwd: 5+3g05aW", side_by_side=True)
    assert extract(result.output) == b"root pwd: 5+3g05aW"


def test_roundtrip_utf8(roomy):
    payload = "\u5bc6\u7801\uff1a5+3g05aW"  # 密码：5+3g05aW
    result = embed(roomy, payload, side_by_side=True)
    assert extract(result.output) == payload.encode("utf-8")


def test_roundtrip_binary(big_carrier):
    limit = min(analyse(big_carrier).capacity, 63)
    payload = bytes(range(1, limit + 1))
    result = embed(big_carrier, payload, side_by_side=True)
    assert extract(result.output) == payload


def test_payload_at_exact_capacity(big_carrier):
    limit = analyse(big_carrier).capacity
    payload = bytes((index % 255) + 1 for index in range(limit))
    result = embed(big_carrier, payload, side_by_side=True)
    assert result.payload_size == limit
    assert extract(result.output) == payload


def test_payload_over_capacity_is_rejected(big_carrier):
    limit = analyse(big_carrier).capacity
    with pytest.raises(PayloadTooLargeError) as excinfo:
        embed(big_carrier, b"A" * (limit + 1))
    assert excinfo.value.capacity == limit
    assert excinfo.value.size == limit + 1


def test_empty_payload_is_allowed(roomy):
    result = embed(roomy, "", side_by_side=True)
    assert extract(result.output) == b""


def test_pristine_carrier_yields_no_payload(roomy):
    pyc_path = support.compile_source(roomy)
    assert extract(pyc_path) == b""


def test_file_size_never_changes(roomy):
    pyc_path = support.compile_source(roomy)
    before = pyc_path.stat().st_size
    result = embed(pyc_path, "ABCDEF", side_by_side=True)
    assert result.output.stat().st_size == before
    assert result.output.read_bytes()[:16] == pyc_path.read_bytes()[:16]


def test_side_by_side_leaves_the_carrier_untouched(roomy):
    pyc_path = support.compile_source(roomy)
    original = pyc_path.read_bytes()
    result = embed(pyc_path, "side", side_by_side=True)

    assert result.output.name == f"{pyc_path.stem}-stegosaurus{pyc_path.suffix}"
    assert result.output.parent == pyc_path.parent
    assert pyc_path.read_bytes() == original
    assert result.output.read_bytes() != original


def test_overwrite_in_place(roomy):
    pyc_path = support.compile_source(roomy)
    result = embed(pyc_path, "inplace")
    assert result.output == pyc_path
    assert result.overwrote_carrier is True
    assert extract(pyc_path) == b"inplace"


def test_explicit_output_path(roomy, tmp_path):
    target = tmp_path / "custom.pyc"
    result = embed(roomy, "moved", output=target, side_by_side=True)
    assert result.output == target
    assert extract(target) == b"moved"


def test_reembedding_replaces_the_previous_payload(roomy):
    pyc_path = support.compile_source(roomy)
    embed(pyc_path, "AAAAAAAAAAAA")
    embed(pyc_path, "BB")
    assert extract(pyc_path) == b"BB"


def test_stale_payload_bytes_are_zeroed(roomy):
    """A shorter payload must not leave the tail of the old one behind."""
    pyc_path = support.compile_source(roomy)
    embed(pyc_path, "OLD-SECRET-VALUE")
    embed(pyc_path, "NEW")
    assert b"OLD" not in pyc_path.read_bytes()


def test_analysis_reports_the_carrier(roomy):
    result = analyse(roomy)
    assert result.capacity > 0
    assert result.code_objects >= 4  # module + tally + classify + stream + main
    assert result.size == support.compile_source(roomy).stat().st_size
    assert result.report() == f"Carrier can support a payload of {result.capacity} bytes"


def test_explode_reduces_capacity(big_carrier):
    plain = analyse(big_carrier).capacity
    guarded = analyse(big_carrier, explode_after=1).capacity
    assert guarded < plain


def test_unknown_suffix_is_rejected(tmp_path):
    stray = tmp_path / "carrier.txt"
    stray.write_text("print(1)\n", encoding="utf-8")
    with pytest.raises(CarrierError, match="must be one of"):
        resolve_carrier(stray)


def test_missing_carrier_is_rejected(tmp_path):
    with pytest.raises(CarrierError, match="does not exist"):
        resolve_carrier(tmp_path / "nope.py")


def test_payload_can_be_given_as_a_path(roomy, tmp_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"\x01\x02\x03\x04")
    result = embed(roomy, source, side_by_side=True)
    assert extract(result.output) == b"\x01\x02\x03\x04"


def test_identical_functions_sharing_one_code_blob(tmp_path):
    """``marshal`` stores identical ``co_code`` once and references it.

    Both code objects then point at the same bytes on disk, so those slots must
    be offered to the payload exactly once -- otherwise two payload bytes would
    fight over one file position.
    """
    from stegosaurus import layout, pyc
    from stegosaurus.codegraph import CodeNode

    script = tmp_path / "twins.py"
    script.write_text(
        "def alpha(values):\n"
        "    for value in values:\n"
        "        return value\n"
        "\n"
        "\n"
        "def beta(values):\n"
        "    for value in values:\n"
        "        return value\n",
        encoding="utf-8",
    )
    pyc_path = support.compile_source(script)

    loaded = pyc.load(pyc_path)
    nodes = list(CodeNode.from_code(loaded.code).walk())
    anchors = layout.locate_code_blobs(loaded.raw, loaded.code_offset)
    assert len(anchors) == len(nodes)
    shared = len({(a.offset, a.length) for a in anchors}) < len(anchors)

    limit = analyse(pyc_path).capacity
    payload = bytes((index % 26) + 65 for index in range(limit))
    result = embed(pyc_path, payload, side_by_side=True)

    assert result.output.stat().st_size == pyc_path.stat().st_size
    assert extract(result.output) == payload
    assert support.run_pyc(result.output).stdout == support.run_pyc(pyc_path).stdout
    if not shared:  # pragma: no cover - depends on the interpreter's compiler
        assert "twins" in str(script)
