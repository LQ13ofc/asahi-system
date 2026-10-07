#!/usr/bin/env python3
"""Check the tracked MacBook baseline without changing the host or its data."""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASELINE_RELATIVE = Path("hardware/mba-m1-8gb/baseline")
EXPECTED_RELEASE = "44"
EXPECTED_ARCHITECTURE = "aarch64"
REQUIRED_FILES = (
    "boot-blame.txt",
    "boot.txt",
    "cpu.txt",
    "disks.txt",
    "free.txt",
    "generated.txt",
    "meminfo.txt",
    "os-release.txt",
    "packages.txt",
    "processes.txt",
    "repos.txt",
    "swap.txt",
    "system-services.txt",
    "uname.txt",
    "user-services.txt",
    "zram.txt",
    "zswap.txt",
)


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        parts = shlex.split(raw_value, comments=False, posix=True)
        if len(parts) != 1:
            raise ValueError(f"invalid os-release value for {key}")
        values[key] = parts[0]
    return values


def _count_active_services(text: str) -> int:
    return len(re.findall(r"\.service\s+loaded\s+active\b", text))


def inspect_baseline(repository_root: Path = REPOSITORY_ROOT) -> tuple[list[str], dict[str, str]]:
    baseline = Path(repository_root) / BASELINE_RELATIVE
    errors: list[str] = []
    summary: dict[str, str] = {}

    if not baseline.is_dir():
        return [f"missing baseline directory: {baseline}"], summary

    missing = [name for name in REQUIRED_FILES if not (baseline / name).is_file()]
    if missing:
        return [f"missing baseline file: {name}" for name in missing], summary

    contents = {name: (baseline / name).read_text(encoding="utf-8") for name in REQUIRED_FILES}
    empty = [name for name, value in contents.items() if not value.strip() and name != "zram.txt"]
    errors.extend(f"baseline file is unexpectedly empty: {name}" for name in empty)

    try:
        release = parse_os_release(contents["os-release.txt"])
    except ValueError as error:
        errors.append(str(error))
        release = {}

    if release.get("ID") != "fedora-asahi-remix":
        errors.append("os-release.txt must identify Fedora Asahi Remix")
    if release.get("VERSION_ID") != EXPECTED_RELEASE:
        errors.append(f"os-release.txt must report Fedora version {EXPECTED_RELEASE}")

    uname = contents["uname.txt"]
    cpu = contents["cpu.txt"]
    if not re.search(rf"\b{EXPECTED_ARCHITECTURE}\b", uname):
        errors.append(f"uname.txt must report {EXPECTED_ARCHITECTURE}")
    cpu_arch = re.search(r"^Architecture:\s*(\S+)", cpu, re.MULTILINE)
    if not cpu_arch or cpu_arch.group(1) != EXPECTED_ARCHITECTURE:
        errors.append(f"cpu.txt must report architecture {EXPECTED_ARCHITECTURE}")
    if "Apple" not in cpu or not re.search(r"M1\b", cpu):
        errors.append("cpu.txt must identify the Apple M1")

    mem_total = re.search(r"^MemTotal:\s*(\d+)\s+kB\s*$", contents["meminfo.txt"], re.MULTILINE)
    if not mem_total:
        errors.append("meminfo.txt must contain MemTotal in kB")
    else:
        mem_gib = int(mem_total.group(1)) / 1024 / 1024
        summary["linux_visible_memory"] = f"{mem_gib:.2f} GiB"

    packages = [line.strip() for line in contents["packages.txt"].splitlines() if line.strip()]
    if not packages:
        errors.append("packages.txt must contain at least one package")
    elif len(packages) != len(set(packages)):
        errors.append("packages.txt contains duplicate package entries")

    process_rows = sum(bool(re.match(r"^\s*\d+\s", line)) for line in contents["processes.txt"].splitlines())
    summary.update(
        release=release.get("PRETTY_NAME", "unknown"),
        architecture=EXPECTED_ARCHITECTURE,
        kernel=uname.split()[2] if len(uname.split()) > 2 else "unknown",
        package_count=str(len(packages)),
        system_service_count=str(_count_active_services(contents["system-services.txt"])),
        user_service_count=str(_count_active_services(contents["user-services.txt"])),
        process_rows=str(process_rows),
        capture=contents["generated.txt"].strip(),
    )
    return errors, summary


def main() -> int:
    errors, summary = inspect_baseline()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Baseline validation: OK")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

