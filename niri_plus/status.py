"""Human-readable, read-only installation status."""

from __future__ import annotations

import pathlib
import subprocess
from . import gaming, host
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
        ("Niri config validator", report["configuration_validation"]),
        ("Packaged session", report["session_package"]), ("Launcher (Command+Space)", report["launcher"]),
        ("Fuzzel executable", report["launcher_binary"]),
        ("Wayland readiness", report.get("wayland_readiness", host.NOT_CONFIGURED)),
        ("Wayland/client runtime", report.get("wayland_clients_runtime", ("UNAVAILABLE", []))[0]),
        *report["core"].items()))
    lines.append("\nSystem")
    lines.extend(f"  {name:22} {value}" for name, value in report["system"].items())
    lines.append("\nOptional")
    lines.extend(f"  {name:22} {value}" for name, value in report["optional"].items())
    lines.extend(["\nGaming Mode", *[f"  {line}" for line in gaming.render_status().splitlines()[1:]]])
    qs = report["quickshell"]
    lines.extend(["\nQuickshell", f"  {'Status':22} {qs['status']}",
                  f"  {'Binary/version':22} {qs['version_status']} {qs['version']}",
                  f"  {'RPM engine':22} {qs['package_status']} {qs['package_version']} (expected {qs['package_expected']})",
                  f"  {'Installed repository':22} {qs['installed_repository']}",
                  f"  {'Expected repository':22} {qs['repository']}",
                  f"  {'Installed commit':22} {qs['installed_commit']}",
                  f"  {'Expected commit':22} {qs['expected_commit']}",
                  f"  {'Installed state pin':22} {qs['state_pin_status']} ({qs['state_expected_commit']})",
                  f"  {'Known-good commit':22} {qs['known_good_commit']}",
                  f"  {'Dirty checkout':22} {'clean' if qs['dirty'] is False else 'WARNING' if qs['dirty'] is True else 'UNAVAILABLE'}",
                  f"  {'Runtime snapshot':22} {qs['runtime_link_status']}",
                  f"  {'Lifecycle':22} {qs['lifecycle']} ({qs['lifecycle_detail']})",
                  f"  {'Engine compatibility':22} {qs['compatibility']}"])
    lines.extend([f"  {'Asahi runtime checks':22} {report['m1_checks']}",
                  f"\nApplied Niri+ version: {report['applied_version']}",
                  f"Known-good version: {report['known_good']}", f"Rollback: {report['rollback']}",
                  f"Source checkout: {report['source_checkout']['status']} "
                  f"({report['source_checkout']['branch']} @ {report['source_checkout']['head']})",
                  f"KDE/Plasma isolation: {report['kde_isolation'][0]}"])
    process_info = qs["processes"]
    process_count = process_info["count"] if process_info["count"] is not None else process_info["status"]
    managed_count = process_info.get("managed_count")
    if managed_count is not None:
        process_count = f"{process_count} ({managed_count} managed)"
    lines.append(f"Quickshell processes: {process_count}")
    if report["warnings"]:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in report["warnings"])
    overall = overall_state(report, host.niri_session_active())
    lines.append(f"\nOverall: {overall}")
    return "\n".join(lines)


def overall_state(report: dict, niri_active: bool = False) -> str:
    required = [report["fedora_asahi"], "OK" if report["architecture"] == "aarch64" else "WARNING",
                "OK" if report["fedora_version"] == "44" else "WARNING", report["niri"]["status"], report["session"],
                report["session_package"], report["configuration"], report["launcher"],
                report["configuration_validation"], report["launcher_binary"],
                report["install_state"], report["rollback_state"], report.get("wayland_readiness", host.NOT_CONFIGURED),
                report["source_checkout"]["status"],
                *report["core"].values(), *report["system"].values()]
    quickshell_configured = report["quickshell"].get("state_pin_status") != host.NOT_CONFIGURED
    if quickshell_configured:
        required.append(report["quickshell"]["status"])
    required.extend(report["managed_file_checksums"].values())
    required.extend(report.get("managed_links", {}).values())
    if niri_active:
        required.append(report["wayland_clients_runtime"][0])
    qs_process_count = report["quickshell"]["processes"]["count"]
    if niri_active:
        if quickshell_configured:
            managed_count = report["quickshell"]["processes"].get("managed_count")
            required.append("OK" if qs_process_count == managed_count == 1 else "WARNING")
        elif qs_process_count not in (0, None):
            required.append("WARNING")
    elif qs_process_count not in (0, None):
        required.append("WARNING")
    if all(state == "OK" for state in required) and report["kde_isolation"][0] in ("OK", "NOT_APPLICABLE"):
        # Static checks cannot certify audio, input, networking, suspend/resume
        # or GPU behavior on the Apple hardware.
        return "M1_REQUIRED"
    if any(state in ("WARNING", "UNAVAILABLE") for state in required) or report["kde_isolation"][0] == "WARNING":
        return "WARNING"
    return "NOT_CONFIGURED"
