"""Read-only preflight for a controlled Niri+ candidate installation."""

from __future__ import annotations

import json
import pathlib
import platform
import shutil
import subprocess
from typing import Any

from . import install, plugins, quickshell, recovery


def inspect(root: pathlib.Path = pathlib.Path("/"), *,
            os_release: dict[str, str] | None = None, machine: str | None = None,
            runner=subprocess.run) -> dict[str, Any]:
    release = os_release if os_release is not None else install.parse_os_release(
        pathlib.Path("/etc/os-release") if root == pathlib.Path("/") else root / "etc/os-release"
    )
    architecture = machine or (platform.machine() if root == pathlib.Path("/") else "unknown")
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    host_errors = install.target_mismatches(release, architecture)
    add("target", "OK" if not host_errors else "UNAVAILABLE",
        f"{release.get('PRETTY_NAME', release.get('ID', 'unknown'))} / {architecture}"
        + ("; " + "; ".join(host_errors) if host_errors else ""))

    data = pathlib.Path("/usr/local/share/niri-plus") if root == pathlib.Path("/") else root / "usr/local/share/niri-plus"
    bootstrap_path = pathlib.Path("/var/lib/niri-plus/bootstrap.json") if root == pathlib.Path("/") else root / "var/lib/niri-plus/bootstrap.json"
    try:
        bootstrap = plugins._read_json(bootstrap_path, root=root)
        install_state = install.load_state(root)
        installed = bool(bootstrap and data.joinpath("VERSION").is_file()
                         and bootstrap.get("version") == data.joinpath("VERSION").read_text(encoding="utf-8").strip()
                         and install_state.get("applied_version") == bootstrap.get("version"))
        add("niri_plus", "OK" if installed else "NOT_INSTALLED",
            f"version={bootstrap.get('version') if bootstrap else 'absent'}; commit={bootstrap.get('source_commit') if bootstrap else 'absent'}")
    except (plugins.PluginError, install.InstallError, OSError) as exc:
        bootstrap = None
        add("niri_plus", "WARNING", f"managed state cannot be validated: {exc}")

    try:
        state, record = plugins.quickshell_state(root)
        plugin_status = ("OK" if state in {"installed", "legacy"} else
                         "WARNING" if state.startswith("installed-") else "NOT_INSTALLED")
        add("quickshell_plugin", plugin_status,
            f"{state}; commit={record.get('commit') if record else 'none'}")
    except plugins.PluginError as exc:
        add("quickshell_plugin", "WARNING", str(exc))

    lock_path = data / "integration/quickshell.lock.json"
    if lock_path.is_file():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            valid_pin = (lock.get("repository") == plugins.REGISTRY["quickshell"]["repository"]
                         and len(lock.get("commit", "")) == 40)
            add("plugin_lock", "OK" if valid_pin else "WARNING",
                f"Quickshell {lock.get('commit', 'unknown')}; engine {lock.get('engine', {}).get('nevra', 'unknown')}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            add("plugin_lock", "WARNING", str(exc))
    else:
        add("plugin_lock", "UNAVAILABLE", "installed lockfile is absent")

    plasma_candidates = (
        pathlib.Path("/usr/share/wayland-sessions/plasma.desktop"),
        pathlib.Path("/usr/share/xsessions/plasma.desktop"),
    )
    plasma = any((path if root == pathlib.Path("/") else root / path.as_posix().lstrip("/")).is_file()
                 for path in plasma_candidates)
    add("plasma_recovery", "OK" if plasma else "WARNING",
        "display-manager Plasma entry found" if plasma else "Plasma recovery entry not found")

    protected = [pathlib.Path("/etc/sddm.conf"), pathlib.Path("/boot/efi"), pathlib.Path("/etc/niri")]
    safe = True
    details = []
    for path in protected:
        target = path if root == pathlib.Path("/") else root / path.as_posix().lstrip("/")
        if target.is_symlink():
            safe = False
            details.append(f"symlink: {path}")
    add("protected_paths", "OK" if safe else "WARNING",
        "SDDM, boot and Niri path parents checked read-only" if safe else "; ".join(details))

    try:
        free = shutil.disk_usage(root).free
        add("disk_space", "OK" if free >= 512 * 1024 * 1024 else "WARNING",
            f"{free // (1024 * 1024)} MiB free; 512 MiB minimum")
    except OSError as exc:
        add("disk_space", "UNAVAILABLE", str(exc))

    recovery_status = recovery.status(root)
    recovery_check = ("OK" if recovery_status.startswith("Recovery bundle: READY") else
                      "WARNING" if "CORRUPT" in recovery_status else "NOT_PREPARED")
    add("offline_recovery", recovery_check, recovery_status.removeprefix("Recovery bundle: "))

    if root == pathlib.Path("/"):
        try:
            session = runner(["systemctl", "--user", "is-active", "graphical-session.target"],
                             check=False, capture_output=True, text=True, timeout=3)
            add("session_services", "OK" if session.returncode == 0 else "NOT_ACTIVE",
                (session.stdout or session.stderr or "unknown").strip())
        except (OSError, subprocess.SubprocessError) as exc:
            add("session_services", "UNAVAILABLE", str(exc))
        for label, command in (
            ("NetworkManager", ["systemctl", "is-active", "NetworkManager.service"]),
            ("PipeWire", ["systemctl", "--user", "is-active", "pipewire.service"]),
            ("WirePlumber", ["systemctl", "--user", "is-active", "wireplumber.service"]),
            ("speaker_safety", ["systemctl", "is-active", "speakersafetyd.service"]),
        ):
            try:
                result = runner(command, check=False, capture_output=True, text=True, timeout=3)
                active = result.returncode == 0 and (result.stdout or "").strip() == "active"
                add(f"service_{label}", "OK" if active else "WARNING",
                    (result.stdout or result.stderr or "unknown").strip())
            except (OSError, subprocess.SubprocessError) as exc:
                add(f"service_{label}", "UNAVAILABLE", str(exc))
    else:
        add("session_services", "UNAVAILABLE", "not queried in simulated root")
        for label in ("NetworkManager", "PipeWire", "WirePlumber", "speaker_safety"):
            add(f"service_{label}", "UNAVAILABLE", "not queried in simulated root")

    installed_plugin = any(item["name"] == "quickshell_plugin" and item["status"] == "OK" for item in checks)
    if installed_plugin and not any(item["name"] == "plugin_lock" and item["status"] == "OK" for item in checks):
        add("compatibility", "WARNING", "installed Quickshell plugin has no valid compatible lock")
    else:
        source_commit = (bootstrap or {}).get("source_commit") if bootstrap else None
        compatibility_status = "OK" if source_commit and len(source_commit) == 40 else "NOT_CONFIGURED"
        add("compatibility", compatibility_status,
            f"managed source commit {source_commit or 'not installed'}; no branch or mutable working tree used")

    critical = {"target", "plasma_recovery", "protected_paths", "disk_space", "offline_recovery", "compatibility",
                "service_NetworkManager", "service_PipeWire", "service_WirePlumber", "service_speaker_safety"}
    blocking = any(item["name"] in critical and item["status"] not in {"OK"} for item in checks)
    return {"status": "READY" if not blocking else "NOT_READY", "checks": checks}


def render(root: pathlib.Path = pathlib.Path("/"), **kwargs) -> str:
    report = inspect(root, **kwargs)
    lines = ["Niri+ read-only preflight", ""]
    lines.extend(f"{item['status']:16} {item['name']}: {item['detail']}" for item in report["checks"])
    lines.extend(["", f"Overall: {report['status']}"])
    return "\n".join(lines)
