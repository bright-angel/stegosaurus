# stegosaurus

Steganography for CPython bytecode: embed a payload inside a `.pyc` file so that
the carrier keeps **exactly** its original size, its disassembly and its runtime
behaviour. Extracting the payload does not require running the carrier.

This is a modern rewrite of [AngelKitty/stegosaurus](https://github.com/AngelKitty/stegosaurus)
(ISC, Jon Herron, 2017, Python 3.6 only). The algorithm is unchanged; the
implementation is rebuilt around the current interpreter's APIs, and the result
is cross-validated against the original on every Python from 3.10 to 3.14.

(`example.py` below is `tests/data/roomy.py`: a small script with loops and
generators, which is what creates the spare bytes. The original's 20-byte
example carrier holds 12 bytes on Python 3.13 and 21 on 3.10 — capacity is a
property of the interpreter's bytecode, see the table further down.)

```console
$ stegosaurus example.py -r
Carrier can support a payload of 30 bytes

$ stegosaurus example.py -s --payload "root pwd: 5+3g05aW"
Payload embedded in carrier

$ ls -l __pycache__/example.cpython-313*.pyc
-rw-r--r--  1526  example.cpython-313-stegosaurus.pyc
-rw-r--r--  1526  example.cpython-313.pyc      # same size

$ stegosaurus __pycache__/example.cpython-313-stegosaurus.pyc -x
Extracted payload: root pwd: 5+3g05aW
```

## Requirements

Python 3.8+ to install, 3.10–3.14 exercised by CI-style cross-validation.
[uv](https://docs.astral.sh/uv/) manages the environment:

```console
$ uv sync                # creates .venv with Python 3.13 and installs the project
$ uv run stegosaurus -h  # or: uv run python -m stegosaurus -h
```

Install into any environment with `uv tool install .`, `pipx install .` or
`pip install .`.

## Usage

```
stegosaurus CARRIER [-p PAYLOAD | --payload-file PATH] [-r] [-s] [-x] [-e N] [-o PATH] [-v] [-V]
```

| Option | Meaning |
| --- | --- |
| `carrier` | `.py`, `.pyc` or `.pyo`. A `.py` file is compiled first (into `__pycache__`). |
| `-p, --payload` | Text to embed (encoded as UTF-8). |
| `--payload-file` | Raw file to embed, byte for byte — binary safe. *(new)* |
| `-r, --report` | Print how many bytes the carrier can hold. |
| `-s, --side-by-side` | Write `<name>-stegosaurus.pyc` instead of overwriting. |
| `-x, --extract` | Read the payload back out. |
| `-e N, --explode N` | Skip slots that follow `N` printable bytes, so the payload never surfaces as a readable string. |
| `-o, --output` | Write the result to an explicit path. *(new)* |
| `-v, -vv` | Progress / per-slot logging on stderr. |
| `-V, --version` | Version. *(new)* |

Every original flag keeps its original meaning and message text. Exit codes:
`0` success, `1` usage error, `2` payload too large, `3` carrier problem.

### Library

```python
from stegosaurus import analyse, embed, extract

print(analyse("example.py").capacity)          # 30
result = embed("example.py", "root pwd: 5+3g05aW", side_by_side=True)
print(extract(result.output))                  # b'root pwd: 5+3g05aW'
embed("example.py", b"\xeb\x2a\x5e\x89\x76", output="agent.pyc")  # raw bytes
```

`embed` accepts `str`, `bytes`, or a `Path` (read as raw bytes) — the path form
is how binary payloads get in without shell quoting games.

## How it works

Since Python 3.6 every instruction is two bytes. Instructions that take no
argument ignore their second byte, so that byte is free space:

```
  0  RESUME                   0
  2  LOAD_CONST               1 (5)
  4  BINARY_ADD                  <- the 00 after BINARY_ADD is never read
  6  LOAD_FAST                0 (n)
  8  BINARY_ADD                  <- free space
 10  LOAD_CONST               2 (3)
 12  BINARY_SUBTRACT            <- free space
 14  RETURN_VALUE               <- free space
```

`stegosaurus` writes the payload into those bytes, in a stable order (nested code
objects first, module scope last), and zero-fills the rest so extraction stops
in the right place. `dis` shows no difference because it never looks at an
argument that is not read.

Three details make the modern version differ from the original:

* **Inline caches are excluded.** Python 3.11 introduced `CACHE` instructions,
  whose second byte holds a type pointer, a version tag or a counter. They pass
  the "no argument" test but writing there corrupts specialisation — an
  experiment that fills every zero operand byte in a 3.13 carrier crashes the
  interpreter with an access violation. Only real instructions are used.
* **Slots are file positions.** CPython de-duplicates byte-identical `co_code`
  in the marshal stream, so several code objects can share one run of bytes.
  Such a run is offered once, which keeps extraction unambiguous and the file
  size intact.
* **Nothing is re-serialised.** The writer locates each `co_code` inside the
  marshal stream and patches those bytes in place. No `marshal.dumps`, no
  header rewrite: bytes outside the payload area are untouched by construction.

## Guarantees, and where they are tested

| Guarantee | Test |
| --- | --- |
| File size is unchanged | `test_file_size_never_changes`, `test_our_patch_is_surgical` |
| Only dead-zone bytes change | `test_our_patch_is_surgical` |
| Verified code objects keep their `co_code` and `co_consts` | `test_rebuild_preserves_every_field_except_bytecode` |
| `dis` output is identical | `test_disassembly_is_unchanged` |
| Runtime behaviour and stdout are identical | `test_stdout_is_unchanged` |
| Imports and exceptions still work | `test_patched_module_is_importable`, `test_exceptions_still_propagate` |
| Payload is not visible to `strings` | `test_payload_is_not_a_readable_string` |
| Round trip for text, UTF-8 and binary payloads | `test_stego.py` |

## Differences from the original

| Area | Original (2017) | This rewrite |
| --- | --- | --- |
| Interpreter support | 3.6 only: reads a 12-byte `.pyc` header and calls `types.CodeType(...)` with 15 positional arguments, both changed in 3.7/3.8 | 3.8+, validated on 3.10–3.14 via `CodeType.replace()` and PEP 552 header parsing |
| File size | Rebuilds code objects and re-marshals them; measured here, that changed the carrier's size in 13 of 20 cases, up to **+285 bytes** | Byte-level patching; size is invariant by construction |
| `CACHE` slots (3.11+) | Written to, corrupting inline caches | Excluded; opt-in via `include_inline_caches=True` for parity experiments |
| Shared `co_code` blobs | Counted once per code object, so the stream could write two payload bytes to one file position | Counted once per physical run |
| Payload length check | Compares character count, then encodes to UTF-8 (a multi-byte payload passes the check and overflows) | Compares encoded bytes |
| Extraction output | `UnicodeDecodeError` on binary payloads | Printable UTF-8 as is, otherwise escaped (`\xff\xfe… (raw bytes: 3)`) |
| Binary payloads | Only via `"\xeb\x2a…"` string escapes | `--payload-file` and `bytes`/`Path` in the API |
| Structure | One 250-line script | `src/` layout, typed modules, console script, 104 tests |

## Cross-validation against the original

The original is not vendored; it is cloned at its last commit:

```console
$ python scripts/fetch_upstream.py       # clones into upstream-ref/ (git-ignored)
$ uv run python scripts/cross_validate.py
$ open reports/cross-validation.md
```

`scripts/cross_validate.py` runs the whole suite plus the 27 cross-validation
tests on each interpreter that `uv` can find, and writes a report. The
cross-validation compares, against the upstream code itself:

1. the **slot stream** — every `(code object, offset)` pair, in payload order;
2. **capacity** counts, including `-e` thresholds;
3. the resulting **`co_code` of every code object** after embedding;
4. **mutual extraction** — upstream reads our output, we read upstream's;
5. CLI messages and side-by-side naming.

Upstream's slot logic runs verbatim from its source file
(`tests/_upstream.py`); only two interpreter details are shimmed, because they
changed after 2017: the 12→16 byte header read, and the `types.CodeType`
construction replaced by the equivalent `CodeType.replace()` recursion.

Latest run — full suite and cross-validation suite both green:

| Python | magic | tests | cross-validation |
| --- | --- | --- | --- |
| 3.10.20 | `6f0d0d0a` | 103 passed | 27 passed |
| 3.11.15 | `a70d0d0a` | 103 passed | 27 passed |
| 3.12.13 | `cb0d0d0a` | 103 passed | 27 passed |
| 3.13.14 | `f30d0d0a` | 104 passed | 27 passed |
| 3.14.6 | `2b0e0d0a` | 103 passed | 27 passed |

Capacity agrees with upstream in 19 of 20 measured carrier/version pairs (the
exception is a carrier with a shared `co_code`, where upstream double-counts).
Capacity depends heavily on the interpreter, because inline caches consume the
arg-less instructions:

| Carrier | 3.10 | 3.11 | 3.12 | 3.13 | 3.14 |
| --- | --- | --- | --- | --- | --- |
| `tests/data/carrier.py` | 21 | 11 | 10 | 14 | 18 |
| `tests/data/roomy.py` | 30 | 25 | 22 | 30 | 34 |
| generated, 20 loops | 296 | 67 | 65 | 107 | 131 |
| stdlib `json/encoder.py` | 182 | 212 | 172 | 225 | 350 |

## Limits

* The payload fits in the spare operand bytes only. Python 3.11/3.12 emit the
  most caches and therefore the least room; larger carriers (or the `-e` guard
  off) give more. Distributing a payload over several carriers is still on the
  original's TODO list.
* A `.pyc` can only be read by the interpreter that produced it (marshal is
  version specific). This tool refuses foreign bytecode with a clear message
  instead of guessing.
* Payload and extraction must use the same `-e` value; a different threshold
  changes the slot stream.
* `.pyo` is accepted for command-line compatibility, but the format was removed
  in Python 3.5 — treat such files as ordinary `.pyc`.
* Steganography is not encryption. Anyone with this tool can read the payload.

## Development

```console
$ uv sync                                  # environment
$ uv run pytest                            # 104 tests
$ uv run ruff check src tests              # lint
$ uv run ruff format src tests             # format
$ uv run python scripts/cross_validate.py  # full matrix + report
```

Layout:

```
src/stegosaurus/
  pyc.py        PEP 552 header parsing, load, atomic write
  layout.py     marshal stream walker: where each co_code lives
  codegraph.py  mutable tree over nested code objects
  deadzone.py   slot discovery and ordering
  stego.py      analyse / embed / extract
  cli.py        argument parsing, exit codes, messages
tests/          unit, behaviour, CLI and cross-validation suites
scripts/        fetch_upstream.py, cross_validate.py, _probe.py
```

## License

ISC, as the original. See `LICENSE`: Jon Herron's 2017 notice is preserved.
Original project: <https://github.com/AngelKitty/stegosaurus>.
