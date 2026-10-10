"""CLI operations for the closed optional plugin registry."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

from . import install, install_command, install_transaction, plugins, quickshell, recovery


def run_install(name: str, *, dry_run: bool = False,
                root: pathlib.Path = pathlib.Path("/"), require_root: bool = True) -> int:
    if name not in plugins.REGISTRY:
        print(f"Unknown plugin: {name}")
        return 2
    if require_root and not dry_run and os.geteuid() != 0:
        print("Plugin installation requires root; use: sudo niri+ plugin install quickshell")
        return 2
    if not _core_installation_valid(root):
        print("Install the Niri+ core first with: sudo niri+ install")
        return 2
    try:
        state, _record = plugins.quickshell_state(root)
    except plugins.PluginError as exc:
        print(f"Plugin installation refused: {exc}")
        return 2
    if state == "installed":
        print("Quickshell is already installed as a Niri+ plugin; `sudo niri+ install` checks and updates it.")
        return 0
    action = "migrate" if state == "legacy" else "install"
    return install_command.run_install(dry_run, plugin_action=action)


def run_list(root: pathlib.Path = pathlib.Path("/")) -> int:
    try:
        print(plugins.render_list(root))
    except plugins.PluginError as exc:
        print(f"Plugin registry unavailable: {exc}")
        return 2
    return 0


def run_status(name: str, root: pathlib.Path = pathlib.Path("/")) -> int:
    if name not in plugins.REGISTRY:
        print(f"Unknown plugin: {name}")
        return 2
    try:
        state, record = plugins.quickshell_state(root)
    except plugins.PluginError as exc:
        print(f"quickshell: WARNING ({exc})")
        return 2
    label = ("LEGACY_MIGRATION_AVAILABLE" if state == "legacy" else
             "INSTALLED" if state == "installed" else
             "WARNING_" + state.removeprefix("installed-").upper() if state.startswith("installed-") else
             "NOT_INSTALLED")
    print(f"quickshell: {label}")
    data = plugins._path(root, pathlib.Path("/usr/local/share/niri-plus"))
    lock = quickshell.load_lock(data)
    expected_commit = lock.get("commit") or "UNAVAILABLE"
    print(f"  expected_commit: {expected_commit}")
    if record:
        print(f"  repository: {record['repository']}")
        print(f"  installed_commit: {record['commit']}")
        print(f"  engine: {record.get('engine_nevra', 'unknown')}")
    else:
        print("  installed_commit: NOT_INSTALLED")
    diagnostics = quickshell.report(root, base=data)
    print(f"  lifecycle: {diagnostics['lifecycle']} ({diagnostics['lifecycle_detail']})")
    process_info = quickshell.process_diagnostics()
    process_count = process_info.get("count")
    print(f"  processes: {process_count if process_count is not None else process_info.get('status', 'UNAVAILABLE')}")
    return 0


def _core_installation_valid(root: pathlib.Path) -> bool:
    bootstrap_path = plugins._path(root, pathlib.Path("/var/lib/niri-plus/bootstrap.json"))
    try:
        state = plugins._read_json(bootstrap_path, root=root)
        install_state = install.load_state(root)
    except (plugins.PluginError, install.InstallError):
        return False
    if not state or not state.get("source_commit") or not state.get("version"):
        return False
    data = plugins._path(root, pathlib.Path("/usr/local/share/niri-plus"))
    binary = plugins._path(root, pathlib.Path("/usr/local/bin/niri+"))
    version_file = data / "VERSION"
    return (version_file.is_file() and version_file.read_text(encoding="utf-8").strip() == state["version"]
            and binary.is_file() and install_state.get("applied_version") == state["version"])


def run_remove(name: str, root: pathlib.Path = pathlib.Path("/"), *, require_root: bool = True,
               running_processes: int | None = None) -> int:
    if name not in plugins.REGISTRY:
        print(f"Unknown plugin: {name}")
        return 2
    if require_root and os.geteuid() != 0:
        print("Plugin removal requires root; use: sudo niri+ plugin remove quickshell")
        return 2
    if not _core_installation_valid(root):
        print("Cannot remove a plugin without a validated Niri+ core installation.")
        return 2
    try:
        state, record = plugins.quickshell_state(root)
    except plugins.PluginError as exc:
        print(f"Plugin removal refused: {exc}")
        return 2
    if not (state.startswith("installed") or state == "legacy") or not record:
        print("Quickshell is not installed as a managed plugin; independent installs were left untouched.")
        return 0
    if running_processes is None:
        diagnostics = quickshell.process_diagnostics()
        running_processes = diagnostics.get("count") if diagnostics.get("status") != "UNAVAILABLE" else None
    if running_processes is None or running_processes:
        print("Quickshell is running or process state is unavailable; exit the Niri session and retry.")
        return 2

    data = plugins._path(root, pathlib.Path("/usr/local/share/niri-plus"))
    runtime = data / "quickshell"
    marker = data / "quickshell.snapshot.json"
    bootstrap_path = plugins._path(root, pathlib.Path("/var/lib/niri-plus/bootstrap.json"))
    state_path = install.prefixed(root, install.STATE_PATH)
    paths = [
        pathlib.Path("/usr/local/share/niri-plus/quickshell"),
        pathlib.Path("/usr/local/share/niri-plus/quickshell.snapshot.json"),
        pathlib.Path("/usr/lib/systemd/user/asahi-quickshell.service"),
        pathlib.Path("/usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service"),
        pathlib.Path("/var/lib/niri-plus/plugins.json"),
        pathlib.Path("/var/lib/niri-plus/bootstrap.json"),
        pathlib.Path(install.STATE_PATH),
    ]
    if root == pathlib.Path("/") and not runtime.exists() and not runtime.is_symlink():
        print("Managed Quickshell runtime is missing; refusing a partial removal.")
        return 2
    if not runtime.is_symlink():
        try:
            snapshot = quickshell._runtime_snapshot(root, data, record["commit"])
        except (OSError, ValueError):
            snapshot = None
        if not snapshot or snapshot.get("status") != "OK":
            print("Managed Quickshell payload failed integrity validation; refusing removal.")
            return 2
    try:
        # Keep the recovery point and filesystem transaction inside the same
        # process lock. This captures the exact pre-removal plugin/core state
        # and leaves a usable offline restore point before any managed file is
        # changed. FilesystemTransaction re-enters this lock in-process.
        with install_transaction.operation_lock(root):
            recovery.prepare_bundle(root, operation_locked=True)
            with install_transaction.FilesystemTransaction(paths, root=root) as transaction:
                selected = set(install.QUICKSHELL_MANAGED_FILES | install.QUICKSHELL_MANAGED_LINKS)
                install.rollback_files(root, selected_paths=selected)
                if runtime.is_symlink() or runtime.is_file():
                    runtime.unlink()
                elif runtime.is_dir():
                    # _runtime_snapshot already confirmed a closed, hash-matching
                    # tree. Never follow symlinks while deleting that owned tree.
                    shutil.rmtree(runtime)
                if marker.exists() or marker.is_symlink():
                    marker.unlink()
                plugins.remove_manifest(root)
                boot = plugins._read_json(bootstrap_path, root=root)
                if boot is not None:
                    boot["quickshell_expected_commit"] = None
                    boot["quickshell_source"] = None
                    plugins._write_json_atomic(bootstrap_path, boot)
                transaction.commit()
            if root == pathlib.Path("/"):
                install.reload_invoking_user_manager()
    except (install.InstallError, install_transaction.TransactionError, plugins.PluginError,
            recovery.RecoveryError, OSError) as exc:
        print(f"Plugin removal failed; managed paths were restored when possible: {exc}")
        return 2
    print("Removed only the Niri+ Quickshell integration. The RPM engine and independent qs installs were preserved.")
    return 0
