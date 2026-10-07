"""Human-readable, read-only installation status."""

from __future__ import annotations

import pathlib
import platform
import subprocess
from .host import host_report


def render_status(version: str, root: pathlib.Path = pathlib.Path("/"),
                  machine: str | None = None, runner=subprocess.run) -> str:
    report = host_report(root, machine, runner)
    lines = [f"Niri+ {version}", f"Host: {report['host']} / {report['architecture']}",
             f"Fedora Asahi: {report['fedora_asahi']} (Fedora {report['fedora_version']})", "",
             "Core"]
    niri = report["niri"]
    lines.append(f"  {'Niri':22} {niri['status']} {niri['version']}")
    lines.extend(f"  {name:22} {value}" for name, value in (
        ("Session", report["session"]), ("Configuration", report["configuration"]),
        *report["core"].items()))
    lines.append("\nSystem")
    lines.extend(f"  {name:22} {value}" for name, value in report["system"].items())
    lines.append("\nOptional")
    lines.extend(f"  {name:22} {value}" for name, value in report["optional"].items())
    lines.extend([f"  {'Asahi runtime checks':22} {report['m1_checks']}",
                  f"\nKnown-good: {report['known_good']}", f"Rollback: {report['rollback']}"])
    if report["warnings"]:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in report["warnings"])
    overall = "HEALTHY" if report["session"] == "OK" and report["configuration"] == "OK" else "NOT_CONFIGURED"
    if report["warnings"] and overall == "HEALTHY":
        overall = "WARNING"
    lines.append(f"\nOverall: {overall}")
    return "\n".join(lines)
