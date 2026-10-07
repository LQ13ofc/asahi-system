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
    qs = report["quickshell"]
    lines.extend(["\nQuickshell", f"  {'Status':22} {qs['status']}",
                  f"  {'Binary/version':22} {qs['version_status']} {qs['version']}",
                  f"  {'RPM engine':22} {qs['package_status']} {qs['package_version']}",
                  f"  {'Repository':22} {qs['repository']}",
                  f"  {'Installed commit':22} {qs['installed_commit']}",
                  f"  {'Expected commit':22} {qs['expected_commit']}",
                  f"  {'Known-good commit':22} {qs['known_good_commit']}",
                  f"  {'Dirty checkout':22} {'WARNING' if qs['dirty'] else 'clean'}",
                  f"  {'Lifecycle':22} {qs['lifecycle']} ({qs['lifecycle_detail']})",
                  f"  {'Engine compatibility':22} {qs['compatibility']}"])
    lines.extend([f"  {'Asahi runtime checks':22} {report['m1_checks']}",
                  f"\nKnown-good: {report['known_good']}", f"Rollback: {report['rollback']}"])
    if report["warnings"]:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in report["warnings"])
    required = (report["session"], report["configuration"], report["quickshell"]["status"])
    if all(state == "OK" for state in required):
        overall = "HEALTHY"
    elif any(state in ("WARNING", "UNAVAILABLE") for state in required):
        overall = "WARNING"
    else:
        overall = "NOT_CONFIGURED"
    lines.append(f"\nOverall: {overall}")
    return "\n".join(lines)
