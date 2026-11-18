"""Shared helpers for the test suite."""

from __future__ import annotations

import io
import marshal
import py_compile
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from stegosaurus import pyc

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
DATA_DIR = TESTS_DIR / "data"

_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")


# --------------------------------------------------------------------------- #
# carriers
# --------------------------------------------------------------------------- #
def copy_data_carrier(tmp_path: Path, name: str) -> Path:
    """Copy a checked-in carrier script into ``tmp_path`` (mtime preserved)."""
    destination = tmp_path / name
    shutil.copy2(DATA_DIR / name, destination)
    return destination


def compile_source(path: Path) -> Path:
    """Compile ``path`` the way the tool does and return the ``.pyc`` path."""
    return Path(py_compile.compile(str(path), doraise=True))


def make_synthetic_carrier(tmp_path: Path, loops: int = 60, name: str = "synthetic.py") -> Path:
    """Generate a module made of arg-less-opcode-rich constructs.

    Every loop contributes ``GET_ITER``/``END_FOR``/``POP_TOP`` style slots, so
    capacity grows linearly with ``loops``.  The body length grows with the
    function index on purpose: CPython >= 3.10 merges byte-identical ``co_code``
    into a single shared blob, and this generator is meant to produce an
    ordinary, unshared carrier (sharing has its own test).
    """
    chunks = ['"""Generated carrier with many dead zones."""', "", "TOTAL = 0", ""]
    for index in range(loops):
        chunks.append(f"def step_{index}(values):")
        chunks.append("    total = 0")
        chunks.append("    for value in values:")
        chunks.append(f"        total += value + {index}")
        for _ in range(index):  # guarantees a unique co_code per function
            chunks.append("        total += 1")
        chunks.append("    print(total)")
        chunks.append("")
        chunks.append("")
    chunks.append("def main():")
    chunks.append("    for index in range(3):")
    chunks.append("        print(index)")
    chunks.append("")
    chunks.append("")
    chunks.append('if __name__ == "__main__":')
    chunks.append("    main()")

    path = tmp_path / name
    path.write_text("\n".join(chunks) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# inspection / execution
# --------------------------------------------------------------------------- #
def load_code(path: Path):
    """Root code object of a ``.pyc`` file."""
    return pyc.load(path).code


def run_pyc(path: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    """Execute ``path`` with the current interpreter."""
    return subprocess.run(
        [sys.executable, str(path)], capture_output=True, text=True, timeout=timeout
    )


def dis_text(path: Path) -> str:
    """Deterministic text disassembly of a ``.pyc`` file.

    Memory addresses of code objects differ between two parses of the same
    bytes, so they are normalised away.  Everything a reader of ``dis`` output
    would notice (opnames, arguments, offsets, constants) is kept.
    """
    code = load_code(path)
    buffer = io.StringIO()
    current = sys.stdout
    sys.stdout = buffer
    try:
        import dis

        dis.dis(code, depth=None)
    finally:
        sys.stdout = current
    return _ADDRESS.sub("0xADDR", buffer.getvalue())


def code_bytes(path: Path) -> bytes:
    """The marshal payload of a ``.pyc`` (header stripped)."""
    header, payload = pyc.split(path.read_bytes())
    return payload


def instruction_stream(path: Path) -> list:
    """Semantic fingerprint of every code object in a ``.pyc``.

    Unlike text disassembly this ignores addresses entirely, which makes it a
    good equality oracle for "the compiler did not change anything" checks.
    """
    import dis
    from types import CodeType

    def fingerprint(code):
        rows = [(code.co_name, code.co_firstlineno, code.co_argcount, len(code.co_code))]
        for instruction in dis.get_instructions(code):
            argument = instruction.argval
            if isinstance(argument, CodeType):
                argument = ("code", argument.co_name, argument.co_firstlineno)
            rows.append((instruction.offset, instruction.opname, instruction.arg, argument))
        for const in code.co_consts:
            if isinstance(const, CodeType):
                rows.extend(fingerprint(const))
        return rows

    return fingerprint(load_code(path))


def dump_code(path: Path):
    """Debug helper: python repr of the marshalled code object."""
    with path.open("rb") as handle:
        handle.read(16)
        return marshal.load(handle)


def wrap(text: str) -> str:
    return textwrap.dedent(text).strip() + "\n"
