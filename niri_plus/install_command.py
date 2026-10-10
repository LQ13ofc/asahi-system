"""Public install command backed by the reversible Phase B installer."""

from __future__ import annotations

import os
import platform
import pathlib
import json
import re
import subprocess
from . import install, plugins, quickshell, source_update


def _version_key(value: str) -> tuple:
    match = re.fullmatch(r"(\d+(?:\.\d+){2})(?:[-+]([0-9A-Za-z.-]+))?", value.strip())
    if not match:
        raise source_update.SourceUpdateError(f"Niri+ version is not valid semantic version text: {value!r}")
    numeric = tuple(int(part) for part in match.group(1).split("."))
    suffix = match.group(2)
    # A release candidate sorts below the same final version.
    return (*numeric, 0 if suffix else 1, suffix or "")


def _refuse_downgrade(snapshot_root: pathlib.Path, root: pathlib.Path = pathlib.Path("/"),
                      system_commit: str | None = None) -> None:
    installed_version = (pathlib.Path("/usr/local/share/niri-plus/VERSION") if root == pathlib.Path("/")
                         else root / "usr/local/share/niri-plus/VERSION")
    if not installed_version.is_file():
        return
    old = installed_version.read_text(encoding="utf-8").strip()
    new = (snapshot_root / "VERSION").read_text(encoding="utf-8").strip()
    if _version_key(new) < _version_key(old):
        raise source_update.SourceUpdateError(
            f"trusted production source version {new} is older than installed Niri+ {old}; refusing downgrade"
        )
    if _version_key(new) == _version_key(old) and system_commit:
        state_path = (pathlib.Path("/var/lib/niri-plus/bootstrap.json") if root == pathlib.Path("/")
                      else root / "var/lib/niri-plus/bootstrap.json")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        installed_commit = state.get("source_commit") if isinstance(state, dict) else None
        if installed_commit and installed_commit != system_commit:
            raise source_update.SourceUpdateError(
                f"Niri+ {new} is already installed from a different source commit; "
                "version this source change before replacing it"
            )


def run_install(dry_run: bool = False, *, plugin_action: str | None = None) -> int:
    return _run_install(dry_run, plugin_action=plugin_action)


def _run_install(dry_run: bool = False, *, plugin_action: str | None = None) -> int:
    try:
        if plugin_action is None:
            plugin_state, _record = plugins.quickshell_state()
            include_quickshell = plugin_state.startswith("installed") or plugin_state == "legacy"
            if plugin_state.startswith("installed"):
                plugin_action = "update"
            elif plugin_state == "legacy":
                plugin_action = "migrate"
            else:
                plugin_action = "none"
        else:
            plugin_state, _record = plugins.quickshell_state()
            include_quickshell = plugin_action in {"install", "update", "migrate"}
            if plugin_action == "install":
                if plugin_state.startswith("installed") or plugin_state == "legacy":
                    raise plugins.PluginError("Quickshell is already managed; use `sudo niri+ install` to update it")
                unmanaged_runtime = pathlib.Path("/usr/local/share/niri-plus/quickshell")
                if unmanaged_runtime.exists() or unmanaged_runtime.is_symlink():
                    raise plugins.PluginError("an unmanaged Quickshell runtime already exists; refusing to take ownership")
            elif plugin_action == "update" and not plugin_state.startswith("installed"):
                raise plugins.PluginError("Quickshell update requires a validated installed plugin")
            elif plugin_action == "migrate" and plugin_state != "legacy":
                raise plugins.PluginError("Quickshell migration requires a validated legacy integration")
    except plugins.PluginError as exc:
        print(f"Install refused: {exc}")
        return 2
    if dry_run:
        errors = install.target_mismatches(install.parse_os_release(), platform.machine())
        if errors:
            install.render_plan(include_quickshell=include_quickshell)
            print(f"Quickshell plugin action: {plugin_action}")
            print("Install would refuse this host: " + "; ".join(errors))
            print("No remote commits were resolved for an incompatible target.")
            return 0
        try:
            source_root = source_update.discover_source_root()
            with source_update.resolved_snapshot(source_root, include_quickshell=include_quickshell) as snapshot:
                _refuse_downgrade(snapshot.root, system_commit=snapshot.system_commit)
                install.render_plan(include_quickshell=include_quickshell, data_dir=snapshot.root)
                print(f"Resolved Niri+ commit: {snapshot.system_commit}")
                if snapshot.include_quickshell:
                    print(f"Resolved Quickshell commit: {snapshot.quickshell_commit}")
                else:
                    print("Quickshell: not managed; repository was not fetched.")
                print(f"Quickshell plugin action: {plugin_action}")
                print("Dry-run complete; no host files, packages or services were changed.")
            return 0
        except (install.InstallError, source_update.SourceUpdateError, plugins.PluginError,
                subprocess.CalledProcessError, OSError) as exc:
            print(f"Dry-run could not validate the trusted update: {exc}")
            return 2
    if os.geteuid() != 0:
        print("Install requires root; use: sudo niri+ install")
        return 2
    if include_quickshell and plugin_action in {"update", "migrate"}:
        process_state = quickshell.process_diagnostics()
        if process_state.get("status") == quickshell.UNAVAILABLE or process_state.get("count") is None:
            print("Install refused: Quickshell process state is unavailable; exit the graphical session and retry.")
            return 2
        if process_state["count"]:
            print("Install refused: Quickshell is running; exit the graphical session before updating the managed plugin.")
            return 2
    try:
        errors = install.target_mismatches(install.parse_os_release(), platform.machine())
        if errors:
            print("Install refused before updating Niri+: incompatible host: " + "; ".join(errors))
            return 2
        source_root = source_update.discover_source_root()
        with source_update.resolved_snapshot(source_root, include_quickshell=include_quickshell) as snapshot:
            _refuse_downgrade(snapshot.root, system_commit=snapshot.system_commit)
            if snapshot.include_quickshell:
                print(f"Resolved Niri+ {snapshot.system_commit[:12]} and Quickshell {snapshot.quickshell_commit[:12]}.")
            else:
                print(f"Resolved Niri+ {snapshot.system_commit[:12]} (Niri-only; Quickshell was not fetched).")
            source_update.install_from_snapshot(snapshot, plugin_action=plugin_action)
    except (install.InstallError, source_update.SourceUpdateError, plugins.PluginError,
            subprocess.CalledProcessError, OSError) as exc:
        print(f"Install failed: {exc}")
        return 2
    return 0
