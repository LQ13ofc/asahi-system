"""Read-only diagnostic report; it never attempts repairs."""

from __future__ import annotations

import pathlib
import subprocess
from . import host, quickshell
from .host import host_report


def render_doctor(root: pathlib.Path = pathlib.Path("/"), machine: str | None = None,
                  runner=subprocess.run) -> str:
    report = host_report(root, machine, runner)
    lines = ["Niri+ doctor (read-only)", f"Host: {report['host']} / {report['architecture']}"]
    for group, entries in (("Core", report["core"]), ("Optional", report["optional"]), ("System", report["system"])):
        lines.append(f"\n{group}")
        lines.extend(f"  {name}: {state}" for name, state in entries.items())
    lines.extend([f"\nNiri: {report['niri']['status']} ({report['niri']['version']})",
                  f"Session: {report['session']} (RPM owner: {report['session_package']})",
                  f"Configuration/includes: {report['configuration']}",
                  f"Niri config validator: {report['configuration_validation']}",
                  f"Launcher Command+Space → Fuzzel: {report['launcher']}",
                  f"Fuzzel executable: {report['launcher_binary']}",
                  f"Niri Wayland readiness gate: {report['wayland_readiness']}",
                  f"Niri Wayland clients runtime: {report['wayland_clients_runtime'][0]}",
                  f"PolicyKit systemd user unit: {report['polkit_unit']}",
                  f"GTK portal backend: {report['portal_backend']}",
                  f"Install state: {report['install_state']}", f"Rollback: {report['rollback']} / backups: {report['rollback_state']}",
                  f"GPU/Asahi hardware: {report['m1_checks']}"])
    source = report["source_checkout"]
    lines.extend(["\nNiri+ source checkout (read-only)",
                  f"  Status: {source['status']}", f"  Path: {source['source_root']}",
                  f"  Origin/branch: {source['origin']} / {source['branch']}",
                  f"  HEAD/cached origin/main: {source['head']} / {source['upstream_head']}",
                  f"  Working tree: {source['dirty']}; source version: {source['version']}"])
    kde_state, kde_units = report["kde_isolation"]
    lines.extend(["\nKDE/Plasma isolation", f"  State: {kde_state}",
                  f"  Active KDE units/processes: {', '.join(kde_units) if kde_units else 'none detected'}"])
    runtime_state, runtime_issues = report["wayland_clients_runtime"]
    lines.append("  Wayland/client startup: " + (", ".join(runtime_issues) if runtime_issues else runtime_state))
    qs_enabled = report["quickshell"].get("state_pin_status") != host.NOT_CONFIGURED
    qs = {**report["quickshell"], **quickshell.doctor_report(root, runner, enabled=qs_enabled)}
    lines.extend(["\nQuickshell integration", f"  Binary/version: {qs['version_status']} ({qs['version']})",
                  f"  RPM engine: {qs['package_status']} ({qs['package_version']}, expected {qs['package_expected']})",
                  f"  Checkout/config: {qs['checkout_status']} ({qs['installed_commit']} expected {qs['expected_commit']})",
                  f"  State pin: {qs['state_pin_status']} ({qs['state_expected_commit']})",
                  f"  Runtime snapshot: {qs['runtime_link_status']}",
                  f"  Known-good commit: {qs['known_good_commit']}; dirty: {qs['dirty']}",
                  f"  Repository: {qs['installed_repository']} (expected {qs['repository']})", f"  Lifecycle unit: {qs['lifecycle']} ({qs['lifecycle_detail']})",
                  f"  Processes: {qs['duplicate_processes']['count']} total, "
                  f"{qs['duplicate_processes'].get('managed_count')} managed "
                  f"({qs['duplicate_processes']['status']})",
                  f"  Crash result: {qs.get('crash_state', 'UNAVAILABLE')}; restarts: {qs['restart_count']}",
                  f"  Logs: {qs['logs']}", f"  Niri session environment: {qs['niri_integration']}",
                  f"  systemd --user imported Niri environment: {qs['systemd_session_environment']}"])
    if report["managed_file_checksums"]:
        lines.append("Managed file checksums:")
        lines.extend(f"  {path}: {state}" for path, state in report["managed_file_checksums"].items())
    if report.get("managed_links"):
        lines.append("Managed symlinks:")
        lines.extend(f"  {path}: {state}" for path, state in report["managed_links"].items())
    lines.append("Gamescope, Steam, muvm and FEX runtime compatibility: M1_REQUIRED")
    if report["warnings"]:
        lines.extend(["Warnings:", *[f"  - {item}" for item in report["warnings"]]])
    lines.append("No repair or system changes were attempted.")
    return "\n".join(lines)
