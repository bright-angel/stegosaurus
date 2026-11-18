"""The payload must be invisible to the interpreter.

These are the guarantees the tool exists for: identical size, identical
behaviour, identical disassembly, and no readable payload in the file.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util

import pytest
import support

from stegosaurus import analyse, embed

NOISE = "PAYLOAD_IS_HERE_42"


def test_stdout_is_unchanged(carrier):
    pyc_path = support.compile_source(carrier)
    baseline = support.run_pyc(pyc_path)
    result = embed(pyc_path, NOISE[:8], side_by_side=True)
    patched = support.run_pyc(result.output)

    assert baseline.stdout and baseline.returncode == 0
    assert patched.stdout == baseline.stdout
    assert patched.returncode == baseline.returncode


def test_disassembly_is_unchanged(carrier):
    """The claim from the upstream README, re-checked on this interpreter."""
    pyc_path = support.compile_source(carrier)
    before = support.dis_text(pyc_path)
    result = embed(pyc_path, NOISE[:8], side_by_side=True)
    assert support.dis_text(result.output) == before


def test_instruction_stream_is_unchanged(carrier, big_carrier):
    for path, payload in ((carrier, "ABCDEF"), (big_carrier, "x" * 40)):
        pyc_path = support.compile_source(path)
        before = support.instruction_stream(pyc_path)
        result = embed(pyc_path, payload, side_by_side=True)
        assert support.instruction_stream(result.output) == before


def test_our_patch_is_surgical(roomy):
    """Only the bytes we claim to write may change -- nothing else in the file.

    This is the property that makes the carrier indistinguishable apart from the
    payload area: no header churn, no marshal re-serialisation, no size drift.
    """
    from stegosaurus import layout, pyc
    from stegosaurus.codegraph import CodeNode
    from stegosaurus.deadzone import iter_dead_zones

    payload = b"SURGICAL-PATCH"
    pyc_path = support.compile_source(roomy)
    original = pyc_path.read_bytes()
    result = embed(pyc_path, payload, side_by_side=True)
    patched = result.output.read_bytes()

    assert len(patched) == len(original)

    loaded = pyc.load(pyc_path)
    root = CodeNode.from_code(loaded.code)
    anchors = layout.locate_code_blobs(loaded.raw, loaded.code_offset)
    anchor_by_node = {id(node): anchor for node, anchor in zip(root.walk(), anchors)}

    # Payload order is reverse pre-order: nested code objects first, module last.
    slot_positions = [
        anchor_by_node[id(slot.node)].offset + slot.offset for slot in iter_dead_zones(root)
    ]

    changed = {index for index, (a, b) in enumerate(zip(original, patched)) if a != b}
    assert changed <= set(slot_positions), "bytes outside the dead zones changed"
    assert len(changed) == len(payload)
    assert [patched[position] for position in slot_positions[: len(payload)]] == list(payload)


def test_marshal_payload_keeps_its_length(carrier):
    pyc_path = support.compile_source(carrier)
    before = support.code_bytes(pyc_path)
    result = embed(pyc_path, "ABCDEF", side_by_side=True)
    assert len(support.code_bytes(result.output)) == len(before)


def test_payload_is_not_a_readable_string(roomy):
    pyc_path = support.compile_source(roomy)
    result = embed(pyc_path, NOISE, side_by_side=True)
    assert NOISE.encode() not in result.output.read_bytes()
    assert b"root pwd" not in result.output.read_bytes()


def test_explode_guard_keeps_bytes_out_of_printable_runs(big_carrier):
    """With -e the payload never sits next to a long printable run."""
    pyc_path = support.compile_source(big_carrier)
    payload = "EXPLODE_GUARD_TEST_VALUE"
    result = embed(pyc_path, payload, side_by_side=True, explode_after=1)
    assert payload.encode() not in result.output.read_bytes()
    assert support.run_pyc(result.output).stdout == support.run_pyc(pyc_path).stdout


def test_patched_module_is_importable(carrier):
    pyc_path = support.compile_source(carrier)
    result = embed(pyc_path, "IMPORT", side_by_side=True)

    loader = importlib.machinery.SourcelessFileLoader("patched_carrier", str(result.output))
    spec = importlib.util.spec_from_loader("patched_carrier", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    assert module.fib_recursive(10) == 55
    assert module.fib_closed(10) == 55


def test_exceptions_still_propagate(tmp_path):
    script = tmp_path / "boom.py"
    script.write_text(
        "def explode():\n    return 1 / 0\n\n\nexplode()\n",
        encoding="utf-8",
    )
    pyc_path = support.compile_source(script)
    baseline = support.run_pyc(pyc_path)
    payload = "B" * min(4, analyse(pyc_path).capacity)
    result = embed(pyc_path, payload, side_by_side=True)
    patched = support.run_pyc(result.output)

    assert baseline.returncode != 0
    assert patched.returncode == baseline.returncode
    assert "ZeroDivisionError" in patched.stderr
    assert patched.stderr.splitlines()[-1] == baseline.stderr.splitlines()[-1]


def test_trailing_zero_terminator_limits_the_payload(carrier):
    """Extraction stops at the first zero byte, so padding stays invisible."""
    from stegosaurus import extract

    pyc_path = support.compile_source(carrier)
    result = embed(pyc_path, "AB", side_by_side=True)
    assert extract(result.output) == b"AB"
    assert len(extract(result.output, explode_after=1)) <= 2


@pytest.mark.parametrize("payload", ["A", "AB", "ABC"])
def test_short_payloads_roundtrip(carrier, payload):
    from stegosaurus import extract

    pyc_path = support.compile_source(carrier)
    result = embed(pyc_path, payload, side_by_side=True)
    assert extract(result.output) == payload.encode()
    assert support.run_pyc(result.output).stdout == support.run_pyc(pyc_path).stdout
