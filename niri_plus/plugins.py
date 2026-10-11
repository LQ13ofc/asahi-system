"""Closed, root-owned registry for optional Niri+ integrations."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import tempfile
from typing import Any

from . import install, quickshell

PLUGIN_STATE = pathlib.Path("/var/lib/niri-plus/plugins.json")
REGISTRY = {
    "quickshell": {
        "repository": "https://github.com/LQ13ofc/quickshell-.git",
        "lock": "integration/quickshell.lock.json",
        "description": "Niri+ Quickshell visual integration",
    },
}
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class PluginError(RuntimeError):
    pass


def _path(root: pathlib.Path, relative: pathlib.Path = PLUGIN_STATE) -> pathlib.Path:
    return relative if root == pathlib.Path("/") else root / relative.as_posix().lstrip("/")


def _read_json(path: pathlib.Path, *, root: pathlib.Path) -> dict[str, Any] | None:
    current = path.parent
    while current != current.parent:
        if current.is_symlink():
            raise PluginError(f"plugin state parent is a symlink: {current}")
        if current == (pathlib.Path("/") if root == pathlib.Path("/") else root):
            break
        current = current.parent
    if path.is_symlink():
        raise PluginError(f"plugin state is a symlink: {path}")
    if not path.exists():
        return None
    try:
        metadata = path.stat()
        if not path.is_file() or (root == pathlib.Path("/") and metadata.st_uid != 0) or metadata.st_mode & 0o022:
            raise PluginError(f"plugin state has unsafe owner, type, or permissions: {path}")
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("top level is not an object")
        return result
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, PluginError):
            raise
        raise PluginError(f"cannot read plugin state {path}: {exc}") from exc


def _write_json_atomic(path: pathlib.Path, value: dict[str, Any]) -> None:
    current = path.parent
    while current != current.parent:
        if current.is_symlink():
            raise PluginError(f"plugin state directory is a symlink: {current}")
        if current == pathlib.Path("/"):
            break
        current = current.parent
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if path.parent == pathlib.Path("/var/lib/niri-plus") and os.geteuid() == 0:
        metadata = path.parent.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise PluginError("/var/lib/niri-plus must be root-owned and not group/world writable")
    if path.is_symlink():
        raise PluginError(f"refusing to replace symlinked plugin state: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=".plugins-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validated_manifest(root: pathlib.Path) -> dict[str, Any] | None:
    manifest = _read_json(_path(root), root=root)
    if manifest is None:
        return None
    plugins = manifest.get("plugins")
    if manifest.get("schema_version") != 1 or not isinstance(plugins, dict) or set(plugins) - set(REGISTRY):
        raise PluginError("plugin manifest schema or registry is invalid")
    quickshell_state = plugins.get("quickshell")
    if quickshell_state is not None:
        if (not isinstance(quickshell_state, dict)
                or quickshell_state.get("repository") != REGISTRY["quickshell"]["repository"]
                or not _COMMIT.fullmatch(str(quickshell_state.get("commit", "")))
                or not isinstance(quickshell_state.get("engine_nevra"), str)
                or not quickshell_state["engine_nevra"]):
            raise PluginError("Quickshell plugin manifest is incomplete or untrusted")
    return manifest


def _legacy_quickshell_is_valid(root: pathlib.Path) -> bool:
    """Recognize only the old managed install whose pin, payload and unit state agree."""
    bootstrap_path = _path(root, pathlib.Path("/var/lib/niri-plus/bootstrap.json"))
    bootstrap = _read_json(bootstrap_path, root=root)
    if not bootstrap:
        return False
    expected = bootstrap.get("quickshell_expected_commit")
    if (not isinstance(expected, str) or not _COMMIT.fullmatch(expected)
            or bootstrap.get("quickshell_repository") != REGISTRY["quickshell"]["repository"]):
        return False
    data = _path(root, pathlib.Path("/usr/local/share/niri-plus"))
    runtime_check = quickshell._runtime_snapshot(root, data, expected)
    if not runtime_check or runtime_check.get("status") != "OK":
        return False
    try:
        state = install.load_state(root)
    except install.InstallError:
        return False
    entries = state.get("entries", {})
    unit = "/usr/lib/systemd/user/asahi-quickshell.service"
    wants = "/usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service"
    if not _integration_entries_valid(root, entries):
        return False
    return True


def _integration_entries_valid(root: pathlib.Path, entries: dict[str, Any]) -> bool:
    unit = "/usr/lib/systemd/user/asahi-quickshell.service"
    wants = "/usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service"
    if unit not in entries or wants not in entries:
        return False
    unit_path = install.prefixed(root, unit)
    wants_path = install.prefixed(root, wants)
    unit_entry = entries[unit]
    wants_entry = entries[wants]
    try:
        if (not unit_path.is_file() or unit_path.is_symlink()
                or hashlib.sha256(unit_path.read_bytes()).hexdigest() != unit_entry.get("installed_sha256")):
            return False
        if not wants_path.is_symlink() or os.readlink(wants_path) != wants_entry.get("installed_target"):
            return False
    except OSError:
        return False
    return True


def quickshell_state(root: pathlib.Path = pathlib.Path("/")) -> tuple[str, dict[str, Any] | None]:
    """Return installed / legacy / absent, failing closed on a corrupt manifest."""
    manifest = _validated_manifest(root)
    if manifest is not None:
        record = manifest["plugins"].get("quickshell")
        if record is None:
            return "absent", None
        bootstrap = _read_json(_path(root, pathlib.Path("/var/lib/niri-plus/bootstrap.json")), root=root)
        if not bootstrap:
            return "installed-inconsistent", record
        if bootstrap.get("quickshell_expected_commit") != record["commit"]:
            return "installed-inconsistent", record
        data = _path(root, pathlib.Path("/usr/local/share/niri-plus"))
        runtime_check = quickshell._runtime_snapshot(root, data, record["commit"])
        if not runtime_check or runtime_check.get("status") != "OK":
            return "installed-corrupt", record
        lock = quickshell.load_lock(data)
        if (lock.get("commit") != record["commit"]
                or lock.get("repository") != record["repository"]):
            return "installed-inconsistent", record
        try:
            install_state = install.load_state(root)
        except install.InstallError as exc:
            return "installed-corrupt", {**record, "diagnostic": f"Niri+ install state invalid: {exc}"}
        if (lock.get("engine", {}).get("nevra") != record.get("engine_nevra")
                or not _integration_entries_valid(root, install_state.get("entries", {}))):
            return "installed-corrupt", record
        return "installed", record
    if _legacy_quickshell_is_valid(root):
        bootstrap = _read_json(_path(root, pathlib.Path("/var/lib/niri-plus/bootstrap.json")), root=root) or {}
        return "legacy", {
            "repository": REGISTRY["quickshell"]["repository"],
            "commit": bootstrap["quickshell_expected_commit"],
            "engine_nevra": quickshell.load_lock(_path(root, pathlib.Path("/usr/local/share/niri-plus"))).get("engine", {}).get("nevra", "unknown"),
        }
    # A pin without a validated package manifest is ambiguous: do not infer
    # ownership merely because `qs` or a directory exists.
    bootstrap_path = _path(root, pathlib.Path("/var/lib/niri-plus/bootstrap.json"))
    bootstrap = _read_json(bootstrap_path, root=root)
    if bootstrap and bootstrap.get("quickshell_expected_commit"):
        raise PluginError("legacy Quickshell state is present but cannot be validated; refusing automatic ownership migration")
    return "absent", None


def write_snapshot_manifest(root: pathlib.Path, action: str, commit: str, engine_nevra: str,
                            version: str, *, prior_state: str) -> None:
    """Commit the trusted plugin record inside the host filesystem transaction."""
    if action not in {"none", "install", "update", "migrate"}:
        raise PluginError(f"unsupported plugin transaction action: {action}")
    path = _path(root)
    previous = _validated_manifest(root)
    if action == "none":
        return
    if not _COMMIT.fullmatch(commit) or not engine_nevra or not version:
        raise PluginError("snapshot plugin metadata is incomplete")
    if action == "install" and prior_state.startswith("installed"):
        raise PluginError("Quickshell is already registered as a Niri+ plugin")
    if action == "update" and not prior_state.startswith("installed"):
        raise PluginError("automatic Quickshell update requires a validated installed plugin")
    if action == "migrate" and prior_state != "legacy":
        raise PluginError("legacy migration requires a fully validated 0.1.9 integration")
    record = {
        "repository": REGISTRY["quickshell"]["repository"],
        "commit": commit,
        "engine_nevra": engine_nevra,
        "niri_plus_version": version,
    }
    plugins = dict((previous or {}).get("plugins", {}))
    plugins["quickshell"] = record
    _write_json_atomic(path, {"schema_version": 1, "plugins": plugins})


def remove_manifest(root: pathlib.Path) -> None:
    path = _path(root)
    manifest = _validated_manifest(root)
    if manifest is None:
        return  # Validated 0.1.9 integration has no separate plugin manifest.
    if "quickshell" not in manifest.get("plugins", {}):
        raise PluginError("Quickshell is not registered as a Niri+ plugin")
    remaining = dict(manifest["plugins"])
    remaining.pop("quickshell")
    if remaining:
        _write_json_atomic(path, {"schema_version": 1, "plugins": remaining})
    else:
        path.unlink()


def render_list(root: pathlib.Path = pathlib.Path("/")) -> str:
    state, record = quickshell_state(root)
    if state == "legacy":
        display = "LEGACY_MIGRATION_AVAILABLE"
    elif state == "installed":
        display = "INSTALLED" if state == "installed" else "NOT_INSTALLED"
    elif state.startswith("installed-"):
        display = "WARNING_" + state.removeprefix("installed-").upper()
    else:
        display = "NOT_INSTALLED"
    suffix = f" @ {record['commit']}" if record else ""
    return f"quickshell\t{display}{suffix}"
