"""Read-only diagnostic report; it never attempts repairs."""

from __future__ import annotations

import pathlib
import subprocess
from .host import host_report


def render_doctor(root: pathlib.Path = pathlib.Path("/"), machine: str | None = None,
                  runner=subprocess.run) -> str:
    report = host_report(root, machine, runner)
    lines = ["Niri+ doctor (read-only)", f"Host: {report['host']} / {report['architecture']}"]
    for group, entries in (("Core", report["core"]), ("Optional", report["optional"]), ("System", report["system"])):
        lines.append(f"\n{group}")
        lines.extend(f"  {name}: {state}" for name, state in entries.items())
    lines.extend([f"\nNiri: {report['niri']['status']} ({report['niri']['version']})",
                  f"Session: {report['session']}", f"Configuration: {report['configuration']}",
                  f"PolicyKit systemd user unit: {report['polkit_unit']}",
                  f"GTK portal backend: {report['portal_backend']}",
                  f"Install state: {report['install_state']}", f"Rollback: {report['rollback']}",
                  f"GPU/Asahi hardware: {report['m1_checks']}"])
    if report["managed_file_checksums"]:
        lines.append("Managed file checksums:")
        lines.extend(f"  {path}: {state}" for path, state in report["managed_file_checksums"].items())
    lines.append("Gamescope, Steam, muvm and FEX runtime compatibility: M1_REQUIRED")
    if report["warnings"]:
        lines.extend(["Warnings:", *[f"  - {item}" for item in report["warnings"]]])
    lines.append("No repair or system changes were attempted.")
    return "\n".join(lines)
