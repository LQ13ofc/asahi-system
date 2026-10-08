"""Read-only Quickshell pin, checkout, package and session diagnostics."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from typing import Callable

from . import host
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
    # Read-only probes must also run as the checkout owner. `safe.directory`
    # alone would let root execute repository-configured filters during status.
    from .source_update import SourceUpdateError, _run_as_owner
    try:
        result = _run_as_owner(
            path,
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "-C", str(path), *args],
            runner,
            check=False,
            timeout=4,
        )
    except SourceUpdateError:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout or ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value.strip()


def _qs_version(root: pathlib.Path, runner: Runner) -> tuple[str, str]:
    binary = "/usr/bin/qs" if root == pathlib.Path("/") else str(root / "usr/bin/qs")
    if not pathlib.Path(binary).is_file():
        return NOT_INSTALLED, "not installed"
    result = run([binary, "--version"], runner)
    lines = (result.stdout or result.stderr).strip().splitlines() if result else []
    version = lines[0] if lines else "version unavailable"
    return (OK if result and result.returncode == 0 and lines else WARNING), version


def _rpm_version(root: pathlib.Path, runner: Runner) -> tuple[str, str]:
    if root != pathlib.Path("/"):
        return UNAVAILABLE, "simulated host"
    # RPM treats an absent epoch as zero for version comparison, but %{EVR}
    # omits that implicit zero. Render a canonical EVR with an explicit epoch
    # so the installed package and the lockfile use the same representation.
    query_format = "%|EPOCH?{%{EPOCH}:}:{0:}|%{VERSION}-%{RELEASE}.%{ARCH}"
    result = run(["rpm", "-q", "--qf", query_format, "quickshell"], runner)
    if not result:
        return UNAVAILABLE, "rpm unavailable"
    if result.returncode != 0:
        return NOT_INSTALLED, "not installed"
    return OK, (result.stdout or "").strip()


def _unit_state(root: pathlib.Path, runner: Runner) -> tuple[str, str]:
    unit_path = root / "usr/lib/systemd/user/asahi-quickshell.service"
    if not unit_path.is_file():
        return NOT_INSTALLED, "unit absent"
    try:
        unit = unit_path.read_text(encoding="utf-8")
    except OSError:
        return WARNING, "unit unreadable"
    required = (
        "ConditionEnvironment=XDG_CURRENT_DESKTOP=niri",
        "Requires=asahi-niri-wayland-ready.service",
        "After=asahi-niri-wayland-ready.service",
        "PartOf=graphical-session.target",
        "StartLimitIntervalSec=60",
        "StartLimitBurst=5",
        "Restart=on-failure",
        "RestartSec=2s",
        "ExecStart=/usr/bin/qs --path /usr/local/share/niri-plus/quickshell/shell.qml",
        "WantedBy=graphical-session.target",
    )
    missing = [line for line in required if line not in unit.splitlines()]
    if missing:
        return WARNING, "unit contract missing: " + ", ".join(missing)
    if root != pathlib.Path("/"):
        return UNAVAILABLE, "simulated host"
    enabled = run(["systemctl", "--user", "is-enabled", "asahi-quickshell.service"], runner)
    active = run(["systemctl", "--user", "is-active", "asahi-quickshell.service"], runner)
    enabled_text = (enabled.stdout or "").strip() if enabled else "unavailable"
    active_text = (active.stdout or "").strip() if active else "unavailable"
    if active_text == "active":
        details = run(["systemctl", "--user", "show", "asahi-quickshell.service",
                       "--property=Result,NRestarts,ActiveState"], runner)
        return active_unit_diagnostics(enabled_text, details)
    if active_text == "inactive" and enabled_text in ("enabled", "enabled-runtime", "static"):
        if host.niri_session_active():
            environment = run(["systemctl", "--user", "show-environment"], runner)
            manager_has_niri = bool(environment and "XDG_CURRENT_DESKTOP=niri" in (environment.stdout or "").splitlines())
            if not manager_has_niri:
                return WARNING, "Niri process environment is active, but systemd --user has no XDG_CURRENT_DESKTOP=niri"
            return WARNING, "eligible Niri unit is inactive"
        return OK, f"enabled={enabled_text}, inactive (outside Niri session)"
    return WARNING, f"enabled={enabled_text}, active={active_text}"


def active_unit_diagnostics(enabled_text: str, details: subprocess.CompletedProcess[str] | None) -> tuple[str, str]:
    if not details or details.returncode != 0:
        return WARNING, "active, but systemd restart state is unavailable"
    fields = dict(line.split("=", 1) for line in (details.stdout or "").splitlines() if "=" in line)
    try:
        restart_count = int(fields.get("NRestarts", "0"))
    except ValueError:
        return WARNING, "active, but systemd restart count is unreadable"
    if fields.get("ActiveState") == "failed" or fields.get("Result", "success") not in ("success", "exit-code"):
        return WARNING, f"active after systemd failure ({fields.get('Result', 'unknown')})"
    if restart_count:
        return WARNING, f"active after {restart_count} automatic restart(s)"
    return OK, f"enabled={enabled_text}, active"


def report(root: pathlib.Path = pathlib.Path("/"), runner: Runner = subprocess.run,
           base: pathlib.Path | None = None) -> dict:
    base = base or data_dir()
    lock = load_lock(base)
    expected = lock.get("commit")
    engine = lock.get("engine", {})
    locked_nevra = engine.get("nevra", "")
    package_expected = locked_nevra.removeprefix("quickshell-") + "." + engine.get("architecture", "aarch64") if locked_nevra else "unknown"
    checkout = checkout_path(root, base)
    checkout_present = checkout.is_dir()
    runtime_manifest = _runtime_snapshot(root, base, expected) if checkout_present else None
    if runtime_manifest:
        installed_commit = runtime_manifest["commit"]
        remote = runtime_manifest["repository"]
        dirty = runtime_manifest["status"] != OK
    else:
        installed_commit = git_value(checkout, ["rev-parse", "--verify", "HEAD"], runner) if checkout_present else None
        remote = git_value(checkout, ["remote", "get-url", "origin"], runner) if checkout_present else None
        dirty_output = git_value(checkout, ["status", "--porcelain", "--untracked-files=all"], runner) if checkout_present else None
        dirty = None if dirty_output is None else bool(dirty_output)
    version_status, version = _qs_version(root, runner)
    package_status, package_version = _rpm_version(root, runner)
    if package_status == OK and package_version != package_expected:
        package_status = WARNING
    lifecycle, lifecycle_detail = _unit_state(root, runner)
    if not lock:
        checkout_status = WARNING
    elif not checkout_present:
        checkout_status = NOT_INSTALLED
    elif runtime_manifest:
        checkout_status = runtime_manifest["status"]
    elif not installed_commit:
        checkout_status = WARNING
    elif (installed_commit != expected or dirty is not False or not remote
          or remote.rstrip("/").removesuffix(".git").lower() != lock.get("repository", "").rstrip("/").removesuffix(".git").lower()):
        checkout_status = WARNING
    else:
        checkout_status = OK
    if checkout_status != OK:
        overall_status = checkout_status
    elif version_status != OK:
        overall_status = version_status
    elif package_status != OK:
        overall_status = package_status
    elif lifecycle != OK:
        overall_status = NOT_CONFIGURED if lifecycle == NOT_INSTALLED else lifecycle
    else:
        overall_status = OK
    info = {
        "status": overall_status,
        "checkout_status": checkout_status,
        "version_status": version_status,
        "version": version,
        "package_status": package_status,
        "package_version": package_version,
        "package_expected": package_expected,
        "repository": lock.get("repository", "unknown"),
        "installed_repository": remote or "unknown",
        "installed_commit": installed_commit or NOT_INSTALLED,
        "expected_commit": expected or "unknown",
        "dirty": dirty if dirty is not None else "UNAVAILABLE",
        "checkout": str(checkout),
        "runtime_link_status": _runtime_link_status(root),
        "lifecycle": lifecycle,
        "lifecycle_detail": lifecycle_detail,
        "compatibility": engine.get("compatibility", M1_REQUIRED),
        "warnings": ["Quickshell rendering, D-Bus features and memory cost require M1_REQUIRED validation."],
    }
    if info["runtime_link_status"] not in (OK, UNAVAILABLE):
        info["status"] = WARNING
    return info


def _runtime_link_status(root: pathlib.Path) -> str:
    runtime = root / "usr/local/share/niri-plus/quickshell"
    state_path = root / "var/lib/niri-plus/bootstrap.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        expected_commit = state.get("quickshell_expected_commit")
        legacy_source = state.get("quickshell_source")
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return WARNING
    if runtime.is_dir() and not runtime.is_symlink():
        marker = root / "usr/local/share/niri-plus/quickshell.snapshot.json"
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            return OK if data.get("commit") == expected_commit and (runtime / "shell.qml").is_file() else WARNING
        except (OSError, ValueError, json.JSONDecodeError):
            return WARNING
    if not runtime.is_symlink():
        return WARNING if runtime.exists() else NOT_INSTALLED
    try:
        if not expected_commit and legacy_source:
            expected = pathlib.Path(legacy_source).resolve()
        else:
            expected = pathlib.Path(legacy_source or "").resolve()
        return OK if runtime.resolve(strict=True) == expected else WARNING
    except (OSError, KeyError):
        return WARNING


def _runtime_snapshot(root: pathlib.Path, base: pathlib.Path, expected: str | None) -> dict | None:
    marker = root / "usr/local/share/niri-plus/quickshell.snapshot.json"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    repository = data.get("repository", "")
    commit = data.get("commit", "")
    if not expected or commit != expected or repository.rstrip("/").removesuffix(".git").lower() != str(load_lock(base).get("repository", "")).rstrip("/").removesuffix(".git").lower():
        return {"status": WARNING, "repository": repository, "commit": commit}
    checkout = root / "usr/local/share/niri-plus/quickshell"
    expected_paths: set[str] = set()
    from .source_update import _git_hash
    try:
        for entry in data.get("files", []):
            rel = pathlib.PurePosixPath(str(entry["path"]))
            if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
                raise ValueError("unsafe Quickshell snapshot path")
            path = checkout.joinpath(*rel.parts)
            key = rel.as_posix()
            if key in expected_paths:
                raise ValueError("duplicate Quickshell path")
            expected_paths.add(key)
            if entry["mode"] == 0o120000:
                blob = os.readlink(path).encode("utf-8")
            else:
                blob = path.read_bytes()
            if _git_hash(b"blob", blob) != entry["oid"]:
                raise ValueError(f"Quickshell file differs from pin: {key}")
            actual_mode = 0o120000 if path.is_symlink() else 0o100755 if path.stat().st_mode & 0o111 else 0o100644
            if actual_mode != entry["mode"]:
                raise ValueError(f"Quickshell mode differs from pin: {key}")
        actual = set()
        for base_path, dirs, files in os.walk(checkout, followlinks=False):
            for name in dirs + files:
                path = pathlib.Path(base_path) / name
                if path.is_symlink() or path.is_file():
                    actual.add(path.relative_to(checkout).as_posix())
        if actual != expected_paths:
            raise ValueError("Quickshell runtime contains missing or unpinned files")
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": WARNING, "repository": repository, "commit": commit}
    return {"status": OK, "repository": repository, "commit": commit}


def process_diagnostics(proc_root: pathlib.Path = pathlib.Path("/proc")) -> dict:
    matches = []
    try:
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                argv = [part.decode(errors="replace") for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
            except OSError:
                continue
            if not argv or pathlib.Path(argv[0]).name != "qs":
                continue
            # Count any Quickshell process in the Niri user session, including
            # an accidental manual `qs -c barra` alongside the managed unit.
            matches.append({
                "pid": int(entry.name),
                "command": " ".join(argv),
                "managed_invocation": argv[:3] == [
                    "/usr/bin/qs", "--path", "/usr/local/share/niri-plus/quickshell/shell.qml"
                ],
            })
    except OSError:
        return {"status": UNAVAILABLE, "count": None, "managed_count": None, "processes": []}
    matches.sort(key=lambda item: item["pid"])
    managed_count = sum(1 for item in matches if item["managed_invocation"])
    process_state = OK if not matches or (len(matches) == 1 and managed_count == 1) else WARNING
    return {"status": process_state, "count": len(matches),
            "managed_count": managed_count, "processes": matches}


def doctor_report(root: pathlib.Path = pathlib.Path("/"), runner: Runner = subprocess.run,
                  base: pathlib.Path | None = None) -> dict:
    info = report(root, runner, base)
    unit_file = root / "usr/lib/systemd/user/asahi-quickshell.service"
    duplicate = process_diagnostics() if root == pathlib.Path("/") else {
        "status": UNAVAILABLE, "count": None, "managed_count": None, "processes": []
    }
    unit_state = "NOT_INSTALLED" if not unit_file.is_file() else info["lifecycle"]
    logs = "UNAVAILABLE"
    restarts = None
    if root == pathlib.Path("/"):
        show = run(["systemctl", "--user", "show", "asahi-quickshell.service", "--property=Result,ExecMainStatus,NRestarts,ActiveState"], runner)
        if show and show.returncode == 0:
            fields = dict(line.split("=", 1) for line in (show.stdout or "").splitlines() if "=" in line)
            restarts = fields.get("NRestarts")
            info["crash_state"] = fields.get("Result", "unknown")
            active_state = fields.get("ActiveState", "unknown")
            if active_state == "failed":
                info["lifecycle"] = WARNING
                info["status"] = WARNING
                info["crash_state"] = f"failed/{info['crash_state']}"
        journal = run(["journalctl", "--user", "-u", "asahi-quickshell.service", "-b", "-n", "20", "--no-pager"], runner)
        if journal and journal.returncode == 0:
            logs = "AVAILABLE" if (journal.stdout or "").strip() else "EMPTY"
    niri = host.niri_session_active()
    manager_environment = "UNAVAILABLE"
    if root == pathlib.Path("/"):
        environment = run(["systemctl", "--user", "show-environment"], runner)
        if environment and environment.returncode == 0:
            manager_environment = OK if "XDG_CURRENT_DESKTOP=niri" in (environment.stdout or "").splitlines() else WARNING
    if ((niri and (duplicate.get("count") != 1 or duplicate.get("managed_count") != 1
                   or info["lifecycle"] != OK))
            or (not niri and (duplicate.get("count") or 0) > 0)):
        info["status"] = WARNING
    return {
        **info,
        "binary": info["version_status"],
        "checkout_config": info["status"],
        "unit": unit_state,
        "duplicate_processes": duplicate,
        "restart_count": restarts,
        "logs": logs,
        "niri_integration": OK if niri or root != pathlib.Path("/") else "NOT_CONFIGURED",
        "systemd_session_environment": manager_environment,
    }
