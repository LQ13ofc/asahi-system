"""Read-only Quickshell pin, checkout, package and session diagnostics."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from typing import Callable

from .host import M1_REQUIRED, NOT_CONFIGURED, NOT_INSTALLED, OK, UNAVAILABLE, WARNING, run

Runner = Callable[..., subprocess.CompletedProcess[str]]


def data_dir() -> pathlib.Path:
    configured = os.environ.get("NIRI_PLUS_DATA_DIR")
    return pathlib.Path(configured) if configured else pathlib.Path(__file__).resolve().parents[1]


def load_lock(base: pathlib.Path | None = None) -> dict:
    path = (base or data_dir()) / "integration/quickshell.lock.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema_version") != 1 or len(value.get("commit", "")) != 40:
            raise ValueError("unsupported Quickshell lock")
        return value
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def checkout_path(root: pathlib.Path, base: pathlib.Path) -> pathlib.Path:
    runtime = root / "usr/local/share/niri-plus/quickshell"
    if runtime.exists() or runtime.is_symlink():
        return runtime
    # In a source checkout the submodule lives next to the system repository.
    return base / "external/quickshell"


def git_value(path: pathlib.Path, args: list[str], runner: Runner) -> str | None:
    # The checkout is deliberately owned by the user, while install may run as
    # root. Trust only this exact path for the individual read-only Git probe.
    result = run(["git", "-c", f"safe.directory={path}", "-C", str(path), *args], runner)
    if not result or result.returncode != 0:
        return None
    return (result.stdout or "").strip()


def _qs_version(root: pathlib.Path, runner: Runner) -> tuple[str, str]:
    binary = "/usr/bin/qs" if root == pathlib.Path("/") else str(root / "usr/bin/qs")
    if not pathlib.Path(binary).is_file():
        return NOT_INSTALLED, "not installed"
    result = run([binary, "--version"], runner)
    version = ((result.stdout or result.stderr).strip().splitlines() or ["version unavailable"])[0] if result else "version unavailable"
    return (OK if result and result.returncode == 0 else WARNING), version


def _rpm_version(root: pathlib.Path, runner: Runner) -> tuple[str, str]:
    if root != pathlib.Path("/"):
        return UNAVAILABLE, "simulated host"
    result = run(["rpm", "-q", "--qf", "%{EVR}.%{ARCH}", "quickshell"], runner)
    if not result:
        return UNAVAILABLE, "rpm unavailable"
    if result.returncode != 0:
        return NOT_INSTALLED, "not installed"
    return OK, (result.stdout or "").strip()


def _unit_state(root: pathlib.Path, runner: Runner) -> tuple[str, str]:
    if not (root / "usr/lib/systemd/user/asahi-quickshell.service").is_file():
        return NOT_INSTALLED, "unit absent"
    if root != pathlib.Path("/"):
        return UNAVAILABLE, "simulated host"
    enabled = run(["systemctl", "--user", "is-enabled", "asahi-quickshell.service"], runner)
    active = run(["systemctl", "--user", "is-active", "asahi-quickshell.service"], runner)
    enabled_text = (enabled.stdout or "").strip() if enabled else "unavailable"
    active_text = (active.stdout or "").strip() if active else "unavailable"
    if active_text == "active":
        return OK, f"enabled={enabled_text}, active"
    if enabled_text in ("enabled", "enabled-runtime", "static"):
        return OK, f"enabled={enabled_text}, inactive (outside Niri session)"
    return WARNING, f"enabled={enabled_text}, active={active_text}"


def report(root: pathlib.Path = pathlib.Path("/"), runner: Runner = subprocess.run,
           base: pathlib.Path | None = None) -> dict:
    base = base or data_dir()
    lock = load_lock(base)
    expected = lock.get("commit")
    checkout = checkout_path(root, base)
    checkout_present = checkout.is_dir()
    installed_commit = git_value(checkout, ["rev-parse", "--verify", "HEAD"], runner) if checkout_present else None
    remote = git_value(checkout, ["remote", "get-url", "origin"], runner) if checkout_present else None
    dirty_output = git_value(checkout, ["status", "--porcelain", "--untracked-files=all"], runner) if checkout_present else None
    dirty = bool(dirty_output)
    version_status, version = _qs_version(root, runner)
    package_status, package_version = _rpm_version(root, runner)
    lifecycle, lifecycle_detail = _unit_state(root, runner)
    if not lock:
        checkout_status = WARNING
    elif not checkout_present:
        checkout_status = NOT_INSTALLED
    elif not installed_commit:
        checkout_status = WARNING
    elif installed_commit != expected or dirty or (remote and remote.rstrip("/").removesuffix(".git") != lock.get("repository", "").rstrip("/").removesuffix(".git")):
        checkout_status = WARNING
    else:
        checkout_status = OK
    if checkout_status != OK:
        overall_status = checkout_status
    elif version_status != OK:
        overall_status = version_status
    elif lifecycle == NOT_INSTALLED:
        overall_status = NOT_CONFIGURED
    else:
        overall_status = OK
    return {
        "status": overall_status,
        "checkout_status": checkout_status,
        "version_status": version_status,
        "version": version,
        "package_status": package_status,
        "package_version": package_version,
        "repository": lock.get("repository", "unknown"),
        "installed_commit": installed_commit or NOT_INSTALLED,
        "expected_commit": expected or "unknown",
        "dirty": dirty,
        "checkout": str(checkout),
        "lifecycle": lifecycle,
        "lifecycle_detail": lifecycle_detail,
        "compatibility": lock.get("engine", {}).get("compatibility", M1_REQUIRED),
        "warnings": ["Quickshell rendering, D-Bus features and memory cost require M1_REQUIRED validation."],
    }


def process_diagnostics() -> dict:
    matches = []
    try:
        proc = pathlib.Path("/proc")
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                args = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            except OSError:
                continue
            if args and ("qs -c barra" in args or "qs --path /usr/local/share/niri-plus/quickshell/shell.qml" in args):
                matches.append({"pid": int(entry.name), "command": args})
    except OSError:
        return {"status": UNAVAILABLE, "count": None, "processes": []}
    return {"status": WARNING if len(matches) > 1 else OK, "count": len(matches), "processes": matches}


def doctor_report(root: pathlib.Path = pathlib.Path("/"), runner: Runner = subprocess.run,
                  base: pathlib.Path | None = None) -> dict:
    info = report(root, runner, base)
    unit_file = root / "usr/lib/systemd/user/asahi-quickshell.service"
    duplicate = process_diagnostics() if root == pathlib.Path("/") else {"status": UNAVAILABLE, "count": None, "processes": []}
    unit_state = "NOT_INSTALLED" if not unit_file.is_file() else info["lifecycle"]
    logs = "UNAVAILABLE"
    restarts = None
    if root == pathlib.Path("/"):
        show = run(["systemctl", "--user", "show", "asahi-quickshell.service", "--property=Result,ExecMainStatus,NRestarts,ActiveState"], runner)
        if show and show.returncode == 0:
            fields = dict(line.split("=", 1) for line in (show.stdout or "").splitlines() if "=" in line)
            restarts = fields.get("NRestarts")
            info["crash_state"] = fields.get("Result", "unknown")
        journal = run(["journalctl", "--user", "-u", "asahi-quickshell.service", "-b", "-n", "20", "--no-pager"], runner)
        if journal and journal.returncode == 0:
            logs = "AVAILABLE" if (journal.stdout or "").strip() else "EMPTY"
    niri = bool(os.environ.get("NIRI_SOCKET")) or os.environ.get("XDG_CURRENT_DESKTOP", "").lower() == "niri"
    return {
        **info,
        "binary": info["version_status"],
        "checkout_config": info["status"],
        "unit": unit_state,
        "duplicate_processes": duplicate,
        "restart_count": restarts,
        "logs": logs,
        "niri_integration": OK if niri or root != pathlib.Path("/") else "NOT_CONFIGURED",
    }
