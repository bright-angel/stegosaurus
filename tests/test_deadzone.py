"""Dead zone discovery: safety invariants, ordering, capacity."""

from __future__ import annotations

import math
import opcode

import support

from stegosaurus import analyse
from stegosaurus.codegraph import CodeNode
from stegosaurus.deadzone import count_dead_zones, iter_dead_zones

CACHE = opcode.opmap.get("CACHE")
HAVE_ARGUMENT = opcode.HAVE_ARGUMENT


def _root(path):
    return CodeNode.from_code(support.load_code(support.compile_source(path)))


def test_slots_only_touch_ignored_operands(roomy):
    root = _root(roomy)
    slots = list(iter_dead_zones(root))
    assert slots, "carrier should expose at least one slot"
    for slot in slots:
        assert slot.offset % 2 == 1, "slot must be the second byte of an instruction"
        assert slot.opcode < HAVE_ARGUMENT, f"{slot.opname} takes an argument"
        assert CACHE is None or slot.opcode != CACHE, "inline cache slots are not free space"


def test_slots_initially_zero(roomy):
    """A freshly compiled carrier has no stale operand bytes to trip extraction."""
    root = _root(roomy)
    assert all(slot.value == 0 for slot in iter_dead_zones(root))


def test_slots_reference_the_mutable_tree(roomy):
    root = _root(roomy)
    nodes = {id(node.bytecode) for node in root.walk()}
    for slot in iter_dead_zones(root):
        assert id(slot.node.bytecode) in nodes
        assert slot.node.bytecode is slot.node.bytecode


def test_slot_order_is_reverse_preorder(roomy):
    """Embedding order must be stable: nested code objects first, module last."""
    root = _root(roomy)
    names = [node.qualname for node in root.walk()]
    with_slots = {slot.node.qualname for slot in iter_dead_zones(root)}
    expected = [name for name in reversed(names) if name in with_slots]

    observed = []
    for slot in iter_dead_zones(root):
        if not observed or observed[-1] != slot.node.qualname:
            observed.append(slot.node.qualname)
    assert observed == expected


def test_capacity_matches_analysis(roomy):
    root = _root(roomy)
    expected = count_dead_zones(root)
    assert expected == len(list(iter_dead_zones(root)))
    assert analyse(roomy).capacity == expected


def test_inline_caches_are_only_opt_in(roomy):
    root = _root(roomy)
    strict = count_dead_zones(root)
    permissive = count_dead_zones(root, include_inline_caches=True)
    assert permissive >= strict
    if CACHE is not None:
        assert permissive > strict, "Python 3.11+ emits CACHE slots"
        permissive_slots = iter_dead_zones(root, include_inline_caches=True)
        cache_slots = [slot for slot in permissive_slots if slot.opcode == CACHE]
        assert cache_slots


def test_explode_skips_printable_runs(big_carrier):
    root = _root(big_carrier)
    base = count_dead_zones(root)
    everything = count_dead_zones(root, explode_after=math.inf)
    guarded = count_dead_zones(root, explode_after=1)
    assert base == everything
    assert guarded < base, "the guard must drop slots that follow printable bytes"


def test_explode_never_increases_capacity(big_carrier):
    root = _root(big_carrier)
    counts = [count_dead_zones(root, explode_after=n) for n in (1, 2, 4, 8, 16)]
    assert counts == sorted(counts), counts


def test_explode_property_holds_for_every_slot(big_carrier):
    """A yielded slot is never preceded by ``N`` printable bytes."""
    import string

    printable = set(string.printable.encode("ascii"))
    node = next(n for n in _root(big_carrier).walk() if len(n.bytecode) > 8)
    for threshold in (1, 2, 3):
        for slot in __import__("stegosaurus").iter_node_slots(node, explode_after=threshold):
            window = node.bytecode[slot.offset - threshold + 1 : slot.offset + 1]
            assert not all(byte in printable for byte in window), (threshold, slot)


def test_explode_guard_is_exercised_by_a_crafted_node(roomy):
    """Direct proof the guard fires, independent of what the compiler emits."""
    import opcode as opcode_module
    import string

    from stegosaurus import iter_node_slots

    printable = frozenset(string.printable.encode("ascii"))
    # An arg-less opcode whose byte is printable, so the crafted bytecode is one
    # long printable run (the payload would otherwise be readable with `strings`).
    printable_opcode = next(
        value
        for value in sorted(opcode_module.opmap.values())
        if 0 < value < HAVE_ARGUMENT and value in printable
    )

    node = next(n for n in _root(roomy).walk() if len(n.bytecode) >= 16)
    node.bytecode[:] = bytes([printable_opcode, 0x41]) * (len(node.bytecode) // 2)
    base = len(list(iter_node_slots(node)))
    assert base == len(node.bytecode) // 2

    # One printable byte is always enough to trip the guard here; two still
    # removes slots, which is what the -e option is for.
    assert list(iter_node_slots(node, explode_after=1)) == []
    assert len(list(iter_node_slots(node, explode_after=2))) < base
    assert len(list(iter_node_slots(node, explode_after=math.inf))) == base


def test_trivial_script_has_almost_no_capacity():
    """Argument-taking instructions leave no room: capacity tracks opcode shape."""
    root = CodeNode.from_code(compile("x = 1\n", "<test>", "exec"))
    assert count_dead_zones(root) <= 1  # only an implicit RETURN_VALUE, before 3.11


def test_dead_zone_write_validates_range(roomy):
    slot = next(iter(iter_dead_zones(_root(roomy))))
    slot.write(0xFF)
    assert slot.value == 0xFF
    try:
        slot.write(256)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("write() must reject values above 255")
