#!/usr/bin/env python
"""Emit one interpreter's view of the carriers as JSON.

Used by ``cross_validate.py``; not meant to be run by hand (it imports the test
helpers so that both sides measure exactly the same carriers).
"""

from __future__ import annotations

import json
import py_compile
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import support  # noqa: E402  (path setup must run first)

from stegosaurus import analyse, layout, pyc  # noqa: E402
from stegosaurus.codegraph import CodeNode  # noqa: E402

try:
    import _upstream

    ORACLE = _upstream.load_oracle()
except Exception:  # noqa: BLE001 - a missing oracle is a normal outcome
    ORACLE = None


def measure(path: Path) -> dict:
    compiled = Path(py_compile.compile(str(path), doraise=True))
    loaded = pyc.load(compiled)
    nodes = list(CodeNode.from_code(loaded.code).walk())
    anchors = layout.locate_code_blobs(loaded.raw, loaded.code_offset)
    unique = {(anchor.offset, anchor.length) for anchor in anchors}

    entry = {
        "capacity": analyse(compiled).capacity,
        "code_objects": len(nodes),
        "blobs": len(anchors),
        "shared_blobs": len(anchors) - len(unique),
        "file_size": len(loaded.raw),
    }
    if ORACLE is not None:
        entry["upstream_capacity"] = ORACLE.capacity(compiled)
        entry["upstream_size"] = ORACLE.embed(compiled, "SIZE-CHECK")[0].stat().st_size
    return entry


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        synthetic = support.make_synthetic_carrier(tmp_path, loops=20)
        carriers = {
            "tests/data/carrier.py": ROOT / "tests" / "data" / "carrier.py",
            "tests/data/roomy.py": ROOT / "tests" / "data" / "roomy.py",
            "generated (20 loops)": synthetic,
            "stdlib json/encoder.py": Path(sysconfig.get_paths()["stdlib"]) / "json" / "encoder.py",
        }

        results = {}
        for label, path in carriers.items():
            try:
                results[label] = measure(path)
            except Exception as exc:  # noqa: BLE001
                results[label] = {"error": f"{type(exc).__name__}: {exc}"}

    print(
        json.dumps(
            {
                "python": ".".join(str(part) for part in sys.version_info[:3]),
                "short": ".".join(str(part) for part in sys.version_info[:2]),
                "magic": pyc.interpreter_magic().hex(),
                "upstream": ORACLE is not None,
                "carriers": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
