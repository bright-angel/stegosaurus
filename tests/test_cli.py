"""Command line behaviour, including exit codes and message compatibility."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import support

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_TOO_LARGE = 2
EXIT_CARRIER = 3


def cli(*arguments, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "stegosaurus", *arguments],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        timeout=180,
    )


def test_version():
    result = cli("--version")
    assert result.returncode == EXIT_OK
    assert result.stdout.startswith("stegosaurus 2.")


def test_help_lists_the_documented_options():
    result = cli("--help")
    assert result.returncode == EXIT_OK
    for flag in ("-p", "--payload", "-r", "-s", "-v", "-x", "-e", "--payload-file", "-o"):
        assert flag in result.stdout


def test_report(carrier):
    result = cli(str(carrier), "-r")
    assert result.returncode == EXIT_OK
    assert result.stdout.strip().startswith("Carrier can support a payload of ")
    assert result.stdout.strip().endswith(" bytes")


def test_embed_then_extract(roomy):
    embedded = cli(str(roomy), "-s", "-p", "root pwd: 5+3g05aW")
    assert embedded.returncode == EXIT_OK
    assert embedded.stdout.strip() == "Payload embedded in carrier"

    pyc_path = support.compile_source(roomy)
    side = pyc_path.with_name(f"{pyc_path.stem}-stegosaurus{pyc_path.suffix}")
    extracted = cli(str(side), "-x")
    assert extracted.returncode == EXIT_OK
    assert extracted.stdout.strip() == "Extracted payload: root pwd: 5+3g05aW"


def test_extract_reports_empty_carrier(carrier):
    pyc_path = support.compile_source(carrier)
    result = cli(str(pyc_path), "-x")
    assert result.returncode == EXIT_OK
    assert result.stdout.strip() == "Extracted payload:"


def test_verbose_adds_progress(carrier):
    result = cli(str(carrier), "-r", "-v")
    assert result.returncode == EXIT_OK
    assert "Found" in result.stderr and "bytes available" in result.stderr


def test_double_verbose_lists_every_slot(carrier):
    result = cli(str(carrier), "-r", "-vv")
    assert result.returncode == EXIT_OK
    assert "RETURN_VALUE" in result.stderr


def test_payload_file_is_binary_safe(carrier, tmp_path):
    payload = tmp_path / "blob.bin"
    payload.write_bytes(b"\xff\xfe\x80")  # not valid UTF-8
    assert cli(str(carrier), "-s", "--payload-file", str(payload)).returncode == EXIT_OK

    pyc_path = support.compile_source(carrier)
    side = pyc_path.with_name(f"{pyc_path.stem}-stegosaurus{pyc_path.suffix}")
    result = cli(str(side), "-x")
    assert result.stdout.strip() == r"Extracted payload: \xff\xfe\x80 (raw bytes: 3)"


def test_output_option(carrier, tmp_path):
    target = tmp_path / "elsewhere.pyc"
    result = cli(str(carrier), "-o", str(target), "-p", "HELLO")
    assert result.returncode == EXIT_OK
    assert target.exists()
    assert cli(str(target), "-x").stdout.strip() == "Extracted payload: HELLO"


def test_missing_payload_is_a_usage_error(carrier):
    result = cli(str(carrier))
    assert result.returncode == EXIT_USAGE
    assert "Fatal error" in result.stderr
    assert "Use -h or --help for usage" in result.stderr


def test_both_payload_sources_are_rejected(carrier, tmp_path):
    payload = tmp_path / "p.bin"
    payload.write_bytes(b"x")
    result = cli(str(carrier), "-p", "a", "--payload-file", str(payload))
    assert result.returncode == EXIT_USAGE


def test_unsupported_suffix_is_a_usage_error(tmp_path):
    stray = tmp_path / "thing.txt"
    stray.write_text("x", encoding="utf-8")
    assert cli(str(stray), "-r").returncode == EXIT_USAGE


def test_zero_explode_is_rejected(carrier):
    result = cli(str(carrier), "-r", "-e", "0")
    assert result.returncode == EXIT_USAGE
    assert "positive integers" in result.stderr


def test_missing_carrier_exit_code(tmp_path):
    result = cli(str(tmp_path / "absent.py"), "-r")
    assert result.returncode == EXIT_CARRIER
    assert "does not exist" in result.stderr


def test_oversized_payload_exit_code(roomy):
    limit = int(cli(str(roomy), "-r").stdout.split()[-2])
    result = cli(str(roomy), "-s", "-p", "A" * (limit + 1))
    assert result.returncode == EXIT_TOO_LARGE
    assert f"only support a payload of {limit} bytes" in result.stderr


def test_payload_ignored_warning(carrier):
    result = cli(str(carrier), "-x", "-p", "ignored")
    assert result.returncode == EXIT_OK
    assert "ignored" in result.stderr


def test_console_script_entry_point(carrier):
    script = Path(sys.executable).with_name("stegosaurus")
    if not script.exists():  # pragma: no cover - platform dependent
        script = script.with_suffix(".exe")
    if not script.exists():  # pragma: no cover - not installed as a wheel
        pytest.skip("console script not installed")
    result = subprocess.run([str(script), str(carrier), "-r"], capture_output=True, text=True)
    assert result.returncode == EXIT_OK
    assert "Carrier can support" in result.stdout
