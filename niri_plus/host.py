"""Read-only host probes shared by status and doctor."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
from typing import Callable

OK = "OK"
NOT_INSTALLED = "NOT_INSTALLED"
NOT_CONFIGURED = "NOT_CONFIGURED"
UNAVAILABLE = "UNAVAILABLE"
WARNING = "WARNING"
M1_REQUIRED = "M1_REQUIRED"

Runner = Callable[..., subprocess.CompletedProcess[str]]


def read_os_release(root: pathlib.Path = pathlib.Path("/")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in (root / "etc/os-release").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"').strip("'")
    except OSError:
        return values
    return values


def run(args: list[str], runner: Runner = subprocess.run) -> subprocess.CompletedProcess[str] | None:
    try:
        return runner(args, check=False, text=True, capture_output=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return None


def find_binary(root: pathlib.Path, name: str) -> str | None:
    if root == pathlib.Path("/"):
        return shutil.which(name)
    for directory in ("usr/bin", "usr/local/bin", "usr/sbin", "usr/local/sbin", "bin", "sbin"):
        candidate = root / directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def command_version(name: str, runner: Runner = subprocess.run,
                    root: pathlib.Path = pathlib.Path("/")) -> tuple[str, str | None]:
    path = find_binary(root, name)
    if not path:
        return NOT_INSTALLED, None
    result = run([path, "--version"], runner)
    if result and result.returncode == 0:
        return OK, (result.stdout or result.stderr).strip().splitlines()[0]
    return OK, None


def package_status(package: str, runner: Runner = subprocess.run,
                   root: pathlib.Path = pathlib.Path("/")) -> str:
    if root != pathlib.Path("/"):
        return UNAVAILABLE
    if not shutil.which("rpm"):
        return UNAVAILABLE
    result = run(["rpm", "-q", package], runner)
    return OK if result and result.returncode == 0 else NOT_INSTALLED


def service_status(service: str, runner: Runner = subprocess.run, user: bool = False,
                   root: pathlib.Path = pathlib.Path("/")) -> str:
    if root != pathlib.Path("/"):
        return UNAVAILABLE
    args = ["systemctl"]
    if user:
        args.append("--user")
    args.extend(["is-active", service])
    result = run(args, runner)
    if result and result.returncode == 0 and result.stdout.strip() == "active":
        return OK
    if result and result.stdout.strip() in ("inactive", "failed", "unknown"):
        return WARNING
    return UNAVAILABLE


def installed_file(root: pathlib.Path, absolute: str) -> bool:
    return (root / absolute.lstrip("/")).is_file()


def session_status(root: pathlib.Path) -> str:
    packaged = root / "usr/share/wayland-sessions/niri.desktop"
    legacy_duplicate = root / "usr/share/wayland-sessions/niri-performance.desktop"
    if legacy_duplicate.is_file():
        return WARNING
    if not packaged.is_file():
        return NOT_INSTALLED
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(packaged, encoding="utf-8")
        return OK if parser.get("Desktop Entry", "Name", fallback="") == "Niri" else WARNING
    except (configparser.Error, OSError):
        return WARNING


def installation_state(root: pathlib.Path) -> tuple[str, dict]:
    path = root / "var/lib/asahi-system/niri-performance/state.json"
    if not path.exists():
        return NOT_CONFIGURED, {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema_version") != 1 or not isinstance(state.get("entries"), dict):
            return WARNING, {}
        return OK, state
    except (OSError, ValueError, json.JSONDecodeError):
        return WARNING, {}


def host_report(root: pathlib.Path = pathlib.Path("/"), machine: str | None = None,
                runner: Runner = subprocess.run) -> dict:
    machine = machine or platform.machine()
    release = read_os_release(root)
    host = release.get("PRETTY_NAME") or "Unknown Linux"
    arch = machine
    state_status, state = installation_state(root)
    managed_files = {}
    for absolute, entry in state.get("entries", {}).items():
        expected = entry.get("installed_sha256")
        if not expected:
            continue
        try:
            actual = hashlib.sha256((root / absolute.lstrip("/")).read_bytes()).hexdigest()
            managed_files[absolute] = OK if actual == expected else WARNING
        except OSError:
            managed_files[absolute] = NOT_INSTALLED
    niri_state, niri_version = command_version("niri", runner, root)
    quickshell = OK if find_binary(root, "qs") else NOT_INSTALLED
    gamescope = OK if find_binary(root, "gamescope") else UNAVAILABLE
    steam = OK if find_binary(root, "steam") else NOT_INSTALLED
    report = {
        "host": host,
        "architecture": arch,
        "fedora_asahi": OK if release.get("ID") == "fedora-asahi-remix" else WARNING,
        "fedora_version": release.get("VERSION_ID", "Unknown"),
        "niri": {"status": niri_state, "version": niri_version or "version unavailable"},
        "session": session_status(root),
        "configuration": OK if installed_file(root, "/etc/niri/config.kdl") else NOT_CONFIGURED,
        "managed_file_checksums": managed_files,
        "polkit_unit": OK if installed_file(root, "/usr/lib/systemd/user/asahi-niri-polkit-agent.service") else NOT_INSTALLED,
        "portal_backend": OK if installed_file(root, "/usr/libexec/xdg-desktop-portal-gtk") else NOT_INSTALLED,
        "install_state": state_status,
        "known_good": state.get("known_good_version", NOT_CONFIGURED),
        "rollback": OK if state.get("entries") else NOT_CONFIGURED,
        "core": {
            "foot": package_status("foot", runner, root),
            "fuzzel": package_status("fuzzel", runner, root),
            "PolicyKit agent": package_status("lxqt-policykit", runner, root),
        },
        "system": {
            "PipeWire": service_status("pipewire.service", runner, user=True, root=root),
            "WirePlumber": service_status("wireplumber.service", runner, user=True, root=root),
            "NetworkManager": service_status("NetworkManager.service", runner, root=root),
            "speakersafetyd": service_status("speakersafetyd.service", runner, root=root),
        },
        "optional": {
            "Quickshell": quickshell,
            "Gamescope": gamescope,
            "Steam": steam,
        },
        "m1_checks": M1_REQUIRED,
        "warnings": _warnings(release, machine, state_status, gamescope),
    }
    for group in ("core", "system"):
        for component, status in report[group].items():
            if status not in (OK, NOT_INSTALLED):
                report["warnings"].append(f"{component} state is {status}.")
    for absolute, status in managed_files.items():
        if status != OK:
            report["warnings"].append(f"Managed file check failed for {absolute}: {status}.")
    report["warnings"].append("Real input, audio, Wi-Fi, suspend/resume and graphics behavior require M1_REQUIRED validation.")
    return report


def _warnings(release: dict[str, str], machine: str, state_status: str, gamescope: str) -> list[str]:
    warnings = []
    if release.get("ID") != "fedora-asahi-remix":
        warnings.append("This host is not Fedora Asahi Remix; install is restricted to that target.")
    if machine != "aarch64":
        warnings.append(f"Architecture {machine} is not the target aarch64 hardware.")
    if state_status == WARNING:
        warnings.append("Niri+ install state is malformed or unreadable.")
    if gamescope == UNAVAILABLE:
        warnings.append("Gamescope compatibility on Asahi is M1_REQUIRED; normal Niri use remains available.")
    else:
        warnings.append("Installed Gamescope compatibility on Asahi remains M1_REQUIRED.")
    return warnings
