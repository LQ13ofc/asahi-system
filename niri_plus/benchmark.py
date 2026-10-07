"""Thin frontend to the Phase A read-only collector."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys


def data_dir() -> pathlib.Path:
    explicit = os.environ.get("NIRI_PLUS_DATA_DIR")
    if explicit:
        return pathlib.Path(explicit)
    return pathlib.Path(__file__).resolve().parents[1]


def run_benchmark(runs: int = 1, output: str | None = None,
                  runner=subprocess.run) -> int:
    collector = data_dir() / "scripts/collect-performance-baseline"
    if not collector.is_file():
        print(f"UNAVAILABLE: benchmark collector not found at {collector}", file=sys.stderr)
        return 2
    command = [sys.executable, str(collector), "--runs", str(runs)]
    if output:
        command.extend(["--output", output])
    return runner(command, check=False).returncode
