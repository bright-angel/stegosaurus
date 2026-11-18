"""Cross-validation against the upstream reference implementation.

Two files independently agree when everything they contain matches, so the
checks here compare the *embedded bytecode* rather than the whole file:

1. slot streams (``(code object, offset)`` pairs, in payload order),
2. capacity counts,
3. every code object's ``co_code`` after embedding,
4. extraction of each implementation's output by the other,
5. the CLI messages.

Whole-file equality is deliberately not asserted: the upstream writer rebuilds
code objects and re-marshals them, which renumbers marshal's object table and
can even change the file length, while this implementation patches the marshal
stream in place (see ``test_our_patch_is_surgical``).  The payload area ends up
byte-identical either way, which is what point 3 checks.

The only sanctioned semantic difference is inline cache handling: upstream
predates Python 3.11's ``CACHE`` instructions and treats them as free space.
Pairing ``include_inline_caches=True`` (ours) with ``filter_caches=False``
(upstream) makes the two embed into exactly the same slots.
"""

from __future__ import annotations

import math
import sys

import pytest
import support

from stegosaurus import analyse, embed, extract, iter_dead_zones
from stegosaurus.codegraph import CodeNode

pytestmark = pytest.mark.interop

PAYLOADS = ["", "A", "ABCDE", "root pwd: 5+3g05aW"]
HAS_INLINE_CACHES = sys.version_info >= (3, 11)


def _pair(oracle, tmp_path, pyc_path, payload, *, explode_after=math.inf, include_caches=False):
    """Embed the same payload with both implementations; return both files."""
    mine = tmp_path / "mine.pyc"
    embed(
        pyc_path,
        payload,
        output=mine,
        explode_after=explode_after,
        include_inline_caches=include_caches,
    )
    theirs, stdout = oracle.embed(
        pyc_path,
        payload,
        side_by_side=True,
        explode_after=explode_after,
        filter_caches=not include_caches,
    )
    return mine, theirs, stdout


# --------------------------------------------------------------------------- #
# slot streams
# --------------------------------------------------------------------------- #
def test_slot_stream_matches_upstream(oracle, roomy):
    pyc_path = support.compile_source(roomy)
    root = CodeNode.from_code(support.load_code(pyc_path))
    ours = [(slot.node.original.co_name, slot.offset) for slot in iter_dead_zones(root)]
    assert ours == oracle.slots(pyc_path)


def test_slot_stream_matches_on_a_large_carrier(oracle, big_carrier):
    pyc_path = support.compile_source(big_carrier)
    root = CodeNode.from_code(support.load_code(pyc_path))
    ours = [(slot.node.original.co_name, slot.offset) for slot in iter_dead_zones(root)]
    assert ours == oracle.slots(pyc_path)


def test_capacity_matches_upstream(oracle, roomy, big_carrier):
    for path in (roomy, big_carrier):
        pyc_path = support.compile_source(path)
        assert analyse(pyc_path).capacity == oracle.capacity(pyc_path)


def test_capacity_matches_with_explode(oracle, big_carrier):
    pyc_path = support.compile_source(big_carrier)
    for threshold in (1, 2, 5, 10):
        assert analyse(pyc_path, explode_after=threshold).capacity == oracle.capacity(
            pyc_path, explode_after=threshold
        )


# --------------------------------------------------------------------------- #
# identical embedded bytecode
# --------------------------------------------------------------------------- #
def code_blobs(path):
    """``[(code object name, co_code)]`` in pre-order, i.e. payload order."""
    return [
        (node.original.co_name, bytes(node.bytecode))
        for node in CodeNode.from_code(support.load_code(path)).walk()
    ]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_embedded_bytecode_is_identical_to_upstream(oracle, tmp_path, roomy, payload):
    pyc_path = support.compile_source(roomy)
    mine, theirs, _ = _pair(oracle, tmp_path, pyc_path, payload)
    assert code_blobs(mine) == code_blobs(theirs)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_embedded_bytecode_matches_upstream_inline_cache_behaviour(
    oracle, tmp_path, roomy, payload
):
    """With cache slots enabled both tools fill the same slots in the same order."""
    pyc_path = support.compile_source(roomy)
    mine, theirs, _ = _pair(oracle, tmp_path, pyc_path, payload, include_caches=True)
    assert code_blobs(mine) == code_blobs(theirs)


def test_embedded_bytecode_matches_with_explode(oracle, tmp_path, big_carrier):
    pyc_path = support.compile_source(big_carrier)
    mine, theirs, _ = _pair(oracle, tmp_path, pyc_path, "EXPLODE_GUARD", explode_after=4)
    assert code_blobs(mine) == code_blobs(theirs)


def test_full_capacity_payload_matches(oracle, tmp_path, big_carrier):
    pyc_path = support.compile_source(big_carrier)
    limit = analyse(pyc_path).capacity
    payload = "".join(chr(0x41 + (index % 26)) for index in range(limit))
    mine, theirs, _ = _pair(oracle, tmp_path, pyc_path, payload)
    assert code_blobs(mine) == code_blobs(theirs)
    assert extract(mine) == payload.encode()


def test_side_by_side_naming_matches(oracle, roomy):
    pyc_path = support.compile_source(roomy)
    theirs, _ = oracle.embed(pyc_path, "NAME")
    assert theirs.name == f"{pyc_path.stem}-stegosaurus{pyc_path.suffix}"


# --------------------------------------------------------------------------- #
# cross extraction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("payload", PAYLOADS[1:])
def test_we_extract_upstream_output(oracle, roomy, payload):
    pyc_path = support.compile_source(roomy)
    theirs, _ = oracle.embed(pyc_path, payload)
    assert extract(theirs) == payload.encode("utf-8")


@pytest.mark.parametrize("payload", PAYLOADS[1:])
def test_upstream_extracts_our_output(oracle, tmp_path, roomy, payload):
    pyc_path = support.compile_source(roomy)
    mine, _, _ = _pair(oracle, tmp_path, pyc_path, payload)
    assert oracle.extract(mine) == payload


def test_utf8_payload_crosses_both_ways(oracle, tmp_path, roomy):
    payload = "\u5bc6\u7801\uff1a5+3g05aW"
    pyc_path = support.compile_source(roomy)
    mine, theirs, _ = _pair(oracle, tmp_path, pyc_path, payload)

    assert extract(theirs) == payload.encode("utf-8")
    assert oracle.extract(mine) == payload
    assert code_blobs(mine) == code_blobs(theirs)


def test_empty_payload_changes_nothing(oracle, tmp_path, roomy):
    """Embedding nothing must leave the carrier untouched -- ours literally so."""
    pyc_path = support.compile_source(roomy)
    mine, theirs, _ = _pair(oracle, tmp_path, pyc_path, "")
    assert mine.read_bytes() == pyc_path.read_bytes()
    # Upstream re-marshals, so its file differs in marshal metadata even though
    # the bytecode it carries is the same.
    assert code_blobs(theirs) == code_blobs(pyc_path)


def test_upstream_rewrites_the_whole_stream(oracle, tmp_path, roomy):
    """Documents the difference the in-place writer removes.

    Upstream's ``CodeType`` reconstruction renumbers marshal's object table,
    which perturbs bytes outside the payload area and can even change the file
    length; this implementation leaves those bytes alone.
    """
    pyc_path = support.compile_source(roomy)
    original = pyc_path.read_bytes()
    mine, theirs, _ = _pair(oracle, tmp_path, pyc_path, "ABCD")

    upstream_changed = sum(1 for a, b in zip(original, theirs.read_bytes()) if a != b)
    assert mine.stat().st_size == pyc_path.stat().st_size
    assert len(theirs.read_bytes()) != len(original) or upstream_changed > 4
    assert sum(1 for a, b in zip(original, mine.read_bytes()) if a != b) == 4


# --------------------------------------------------------------------------- #
# behaviour parity
# --------------------------------------------------------------------------- #
def test_embedded_message_matches(oracle, tmp_path, roomy):
    pyc_path = support.compile_source(roomy)
    _, _, stdout = _pair(oracle, tmp_path, pyc_path, "MSG")
    assert stdout.strip() == "Payload embedded in carrier"


def test_both_outputs_run_identically(oracle, tmp_path, roomy):
    pyc_path = support.compile_source(roomy)
    mine, theirs, _ = _pair(oracle, tmp_path, pyc_path, "RUNS")
    baseline = support.run_pyc(pyc_path)
    assert support.run_pyc(mine).stdout == baseline.stdout
    assert support.run_pyc(theirs).stdout == baseline.stdout


def test_upstream_harness_itself_preserves_behaviour(oracle, roomy):
    """Guard against a broken shim: upstream's own output must still run."""
    pyc_path = support.compile_source(roomy)
    theirs, _ = oracle.embed(pyc_path, "SANITY")
    baseline = support.run_pyc(pyc_path)
    patched = support.run_pyc(theirs)
    assert patched.returncode == 0
    assert patched.stdout == baseline.stdout
