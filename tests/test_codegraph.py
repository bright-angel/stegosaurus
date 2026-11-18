"""The mutable code-object tree."""

from __future__ import annotations

from types import CodeType

import support

from stegosaurus.codegraph import CodeNode


def _code(name: str, tmp_path) -> CodeType:
    return support.load_code(support.compile_source(support.copy_data_carrier(tmp_path, name)))


def test_tree_mirrors_the_code_object_graph(roomy):
    code = support.load_code(support.compile_source(roomy))
    root = CodeNode.from_code(code)

    flat = [node.original for node in root.walk()]
    expected = []

    def collect(some_code):
        expected.append(some_code)
        for const in some_code.co_consts:
            if isinstance(const, CodeType):
                collect(const)

    collect(code)
    assert flat == expected  # same objects, same pre-order


def test_consts_keep_position_and_order(roomy):
    code = support.load_code(support.compile_source(roomy))
    root = CodeNode.from_code(code)
    assert len(root.consts) == len(code.co_consts)
    for wrapped, original in zip(root.consts, code.co_consts):
        if isinstance(original, CodeType):
            assert wrapped.original is original
        else:
            assert wrapped is original


def test_rebuild_preserves_every_field_except_bytecode(roomy):
    code = support.load_code(support.compile_source(roomy))
    root = CodeNode.from_code(code)
    rebuilt = root.rebuild()

    assert rebuilt.co_code == code.co_code
    assert rebuilt.co_consts == code.co_consts
    for attribute in (
        "co_name",
        "co_qualname",
        "co_filename",
        "co_firstlineno",
        "co_argcount",
        "co_flags",
        "co_varnames",
        "co_names",
        "co_linetable",
        "co_exceptiontable",
    ):
        if hasattr(CodeType, attribute):
            assert getattr(rebuilt, attribute) == getattr(code, attribute)


def test_rebuild_applies_mutated_bytecode(roomy):
    root = CodeNode.from_code(support.load_code(support.compile_source(roomy)))
    node = next(n for n in root.walk() if n.original.co_name == "tally")
    original = bytes(node.bytecode)
    node.bytecode[-1] = 0x42  # last byte of the trailing RETURN_VALUE's ignored operand
    rebuilt = root.rebuild()

    child = next(c for c in rebuilt.co_consts if isinstance(c, CodeType) and c.co_name == "tally")
    assert child.co_code[:-1] == original[:-1]
    assert child.co_code[-1] == 0x42


def test_rebuilt_module_still_runs(roomy):
    """Filling every real dead zone with garbage must not change behaviour.

    Only slots reported by :func:`iter_dead_zones` are touched: writing into
    inline cache entries instead crashes the interpreter (they hold type
    pointers and counters, see ``test_inline_caches_are_only_opt_in``).
    """
    from stegosaurus.deadzone import iter_dead_zones

    code = support.load_code(support.compile_source(roomy))
    root = CodeNode.from_code(code)
    for slot in iter_dead_zones(root):
        slot.write(0x55)
    namespace = {"__name__": "__main__"}
    exec(root.rebuild(), namespace)
    assert namespace["__name__"] == "__main__"


def test_is_modified_tracks_mutation(roomy):
    root = CodeNode.from_code(support.load_code(support.compile_source(roomy)))
    assert not root.is_modified
    root.bytecode[1] = 1
    assert root.is_modified


def test_walk_covers_nested_code_objects():
    """Nested functions and lambdas live inside a parent's constants."""
    code = compile("def outer():\n    return lambda: 1\n", "<test>", "exec")
    root = CodeNode.from_code(code)
    names = [node.original.co_name for node in root.walk()]
    assert names[0] == "<module>"
    assert "outer" in names
    assert "<lambda>" in names
    assert len(names) == 3
