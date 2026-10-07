#!/usr/bin/env python3
"""Install/rollback the reversible Fedora Asahi Niri session.

No argument performs a dry-run. --apply is limited to Fedora Asahi Remix 44
aarch64 and never edits Plasma, display-manager configuration, kernel, or boot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any


_installed_data = pathlib.Path("/usr/local/share/niri-plus")
REPO = pathlib.Path(os.environ.get(
    "NIRI_PLUS_DATA_DIR",
    _installed_data if (_installed_data / "VERSION").is_file() else pathlib.Path(__file__).resolve().parents[1],
))
PACKAGES = ("niri", "foot", "fuzzel", "xdg-desktop-portal-gtk", "lxqt-policykit",
            "python3-dbus", "python3-gobject")
QUICKSHELL_PACKAGE = "quickshell"
BASE_SERVICES = ("pipewire", "pipewire-pulseaudio", "wireplumber", "NetworkManager")
LEGACY_MANAGED_PATHS = {
    "/usr/share/wayland-sessions/niri-performance.desktop",
    "/usr/local/bin/asahi-niri-session",
    "/usr/local/share/asahi-system/niri/config.kdl",
    "/usr/local/share/asahi-system/niri/keybinds.kdl",
    "/usr/local/share/asahi-system/niri/outputs.kdl",
    "/usr/local/share/asahi-system/niri/rules.kdl",
    "/usr/local/share/asahi-system/niri/autostart.kdl",
}
MANAGED_FILES = {
    "/usr/lib/systemd/user/asahi-niri-polkit-agent.service": REPO / "sessions/systemd/asahi-niri-polkit-agent.service",
    "/usr/lib/systemd/user/asahi-niri-wayland-ready.service": REPO / "sessions/systemd/asahi-niri-wayland-ready.service",
    "/usr/lib/systemd/user/asahi-quickshell.service": REPO / "sessions/systemd/asahi-quickshell.service",
    "/usr/lib/systemd/user/app-org.kde.xwaylandvideobridge@autostart.service.d/10-niri-session.conf": REPO / "sessions/systemd/autostart-filters/app-org.kde.xwaylandvideobridge@autostart.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/app-org.kde.discover.notifier@autostart.service.d/10-niri-session.conf": REPO / "sessions/systemd/autostart-filters/app-org.kde.discover.notifier@autostart.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/app-org.kde.kalendarac@autostart.service.d/10-niri-session.conf": REPO / "sessions/systemd/autostart-filters/app-org.kde.kalendarac@autostart.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/app-org.kde.kdeconnect.daemon@autostart.service.d/10-niri-session.conf": REPO / "sessions/systemd/autostart-filters/app-org.kde.kdeconnect.daemon@autostart.service.d/10-niri-session.conf",
    "/etc/niri/config.kdl": REPO / "niri/config.kdl",
    "/etc/niri/keybinds.kdl": REPO / "niri/keybinds.kdl",
    "/etc/niri/outputs.kdl": REPO / "niri/outputs.kdl",
    "/etc/niri/rules.kdl": REPO / "niri/rules.kdl",
    "/etc/niri/autostart.kdl": REPO / "niri/autostart.kdl",
}
KDE_SESSION_ONLY_UNITS = (
    "akonadi_control.service",
    "kde-baloo.service",
    "kunifiedpush-distributor.service",
    "plasma-gmenudbusmenuproxy.service",
    "plasma-kaccess.service",
    "plasma-kactivitymanagerd.service",
    "plasma-kded6.service",
    "plasma-ksmserver.service",
    "plasma-kwin_wayland.service",
    "plasma-plasmashell.service",
    "plasma-polkit-agent.service",
    "plasma-powerdevil.service",
    "plasma-xdg-desktop-portal-kde.service",
    "plasma-xembedsniproxy.service",
)
KDE_AUTOSTART_UNITS = (
    "app-org.kde.discover.notifier@autostart.service",
    "app-org.kde.kalendarac@autostart.service",
    "app-org.kde.kdeconnect.daemon@autostart.service",
    "app-org.kde.xwaylandvideobridge@autostart.service",
)
for _kde_unit in (*KDE_SESSION_ONLY_UNITS, *KDE_AUTOSTART_UNITS):
    MANAGED_FILES[f"/usr/lib/systemd/user/{_kde_unit}.d/10-niri-session.conf"] = REPO / "sessions/systemd/kde-session-only.conf"
MANAGED_LINKS = {
    "/usr/lib/systemd/user/graphical-session.target.wants/asahi-niri-polkit-agent.service": "../asahi-niri-polkit-agent.service",
    "/usr/lib/systemd/user/graphical-session.target.wants/asahi-niri-wayland-ready.service": "../asahi-niri-wayland-ready.service",
    "/usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service": "../asahi-quickshell.service",
}
STATE_PATH = "/var/lib/asahi-system/niri-performance/state.json"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


class InstallError(Exception):
    pass


def target_mismatches(os_release: dict[str, str], machine: str) -> list[str]:
    errors = []
    if os_release.get("ID") != "fedora-asahi-remix":
        errors.append("OS ID must be fedora-asahi-remix")
    if os_release.get("VERSION_ID") != "44":
        errors.append("VERSION_ID must be 44")
    if machine != "aarch64":
        errors.append("architecture must be aarch64")
    return errors


def parse_os_release(path: pathlib.Path = pathlib.Path("/etc/os-release")) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"').strip("'")
    except OSError as exc:
        raise InstallError(f"cannot read {path}: {exc}") from exc
    return result


def content_for(source: pathlib.Path) -> bytes:
    return source.read_bytes()


def prefixed(root: pathlib.Path, absolute: str) -> pathlib.Path:
    return root / absolute.lstrip("/")


def ensure_no_symlink_parents(root: pathlib.Path, path: pathlib.Path) -> None:
    """Refuse managed writes/reads through a symlinked parent directory."""
    root = root.resolve()
    try:
        relative = path.absolute().relative_to(root)
    except ValueError as exc:
        raise InstallError(f"managed path escapes its root: {path}") from exc
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise InstallError(f"managed path has a symlinked parent: {current}")


def backup_existing(root: pathlib.Path, target: pathlib.Path, relative: str, state: dict[str, Any]) -> None:
    ensure_no_symlink_parents(root, target)
    entries = state.setdefault("entries", {})
    if relative in entries:
        if entries[relative]["kind"] != "preserve":
            return
        del entries[relative]
    if target.is_symlink():
        entries[relative] = {"kind": "symlink", "backup": None, "original_target": os.readlink(target)}
    elif target.exists():
        backup_rel = f"/var/lib/asahi-system/niri-performance/backups{relative}"
        backup_path = prefixed(root, backup_rel)
        ensure_no_symlink_parents(root, backup_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if backup_path.exists() or backup_path.is_symlink():
            raise InstallError(f"refusing to overwrite an unowned backup: {backup_path}")
        shutil.copy2(target, backup_path)
        entries[relative] = {"kind": "file", "backup": backup_rel, "original_target": None}
    else:
        entries[relative] = {"kind": "absent", "backup": None, "original_target": None}


def write_state(root: pathlib.Path, state: dict[str, Any]) -> None:
    path = prefixed(root, STATE_PATH)
    ensure_no_symlink_parents(root, path)
    if path.is_symlink():
        raise InstallError(f"install state is a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(state, output, indent=2, sort_keys=True)
            output.write("\n")
        os.replace(temp_name, path)
        # status/doctor are intentionally usable without root. The state contains
        # only package names, managed paths, hashes and version metadata; keep it
        # world-readable while retaining root-only write access.
        path.chmod(0o644)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_state(root: pathlib.Path) -> dict[str, Any]:
    path = prefixed(root, STATE_PATH)
    ensure_no_symlink_parents(root, path)
    if path.is_symlink():
        raise InstallError(f"install state is a symlink: {path}")
    if not path.exists():
        return {"schema_version": 1, "entries": {}}
    try:
        if not path.is_file():
            raise ValueError("install state is not a regular file")
        if root == pathlib.Path("/"):
            metadata = path.stat()
            if metadata.st_uid != 0 or metadata.st_mode & 0o022 or metadata.st_mode & 0o044 != 0o044:
                raise ValueError("install state has unsafe ownership or permissions")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data.get("entries"), dict):
            raise ValueError("unknown state schema")
        allowed = set(MANAGED_FILES) | set(MANAGED_LINKS) | LEGACY_MANAGED_PATHS
        for relative, entry in data["entries"].items():
            if relative not in allowed or not isinstance(entry, dict):
                raise ValueError(f"unknown managed path in state: {relative}")
            kind = entry.get("kind")
            if kind not in {"preserve", "file", "symlink", "absent"}:
                raise ValueError(f"invalid state entry for {relative}")
            backup = entry.get("backup")
            expected_backup = f"/var/lib/asahi-system/niri-performance/backups{relative}"
            if kind == "file" and backup != expected_backup:
                raise ValueError(f"invalid backup path for {relative}")
            if kind != "file" and backup is not None:
                raise ValueError(f"unexpected backup path for {relative}")
            if kind == "symlink" and not isinstance(entry.get("original_target"), str):
                raise ValueError(f"missing original symlink target for {relative}")
            installed_hash = entry.get("installed_sha256")
            if installed_hash is not None and (not isinstance(installed_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", installed_hash) is None):
                raise ValueError(f"invalid managed file checksum for {relative}")
            installed_target = entry.get("installed_target")
            if installed_target is not None and not isinstance(installed_target, str):
                raise ValueError(f"invalid managed symlink target for {relative}")
        packages = data.get("packages_installed_by_us", [])
        allowed_packages = set((*PACKAGES, QUICKSHELL_PACKAGE))
        if (not isinstance(packages, list) or any(not isinstance(package, str) for package in packages)
                or any(package not in allowed_packages for package in packages)
                or len(packages) != len(set(packages))):
            raise ValueError("invalid explicit package ownership list")
        for field in ("applied_version", "verified_good_version", "known_good_version"):
            value = data.get(field)
            if value is not None and (not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value)):
                raise ValueError(f"invalid {field} in install state")
        quickshell_state = data.get("quickshell", {})
        if not isinstance(quickshell_state, dict):
            raise ValueError("invalid Quickshell state")
        for field in ("expected_commit", "known_good_commit"):
            value = quickshell_state.get(field)
            if value is not None and (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None):
                raise ValueError(f"invalid Quickshell {field}")
        return data
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot read install state {path}: {exc}") from exc


def render_plan() -> None:
    print("Niri+ install plan (dry-run; no host changes)")
    print("Explicit packages:")
    for package in PACKAGES:
        print(f"  - {package}")
    lock_path = REPO / "integration/quickshell.lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        print(f"  - {lock['engine']['nevra']} (assinatura verificada, COPR temporário; aarch64)")
        print(f"Quickshell visual pin: {lock['repository']} @ {lock['commit']}")
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        print("  - Quickshell runtime (pin ausente; install apply recusará este checkout)")
    print("Niri hard dependency resolved by DNF: xwayland-satellite >= 0.7 (on-demand Xwayland integration)")
    print("Display-manager session: reuse Fedora's packaged Niri entry (no duplicate custom entry)")
    print("System Niri defaults: /etc/niri/config.kdl")
    print("DNF option: --setopt=install_weak_deps=False")
    print("Existing baseline components checked, not installed or enabled:")
    for package in BASE_SERVICES:
        print(f"  - {package}")
    print("Managed destinations:")
    for path in (*MANAGED_FILES, *MANAGED_LINKS):
        print(f"  - {path}")
    print("Plasma, SDDM configuration, global services, kernel, boot, and drivers are not changed.")
    print("KDE XDG autostart filters apply only when XDG_CURRENT_DESKTOP=KDE; no process is killed and no component is globally disabled.")


def install_files(root: pathlib.Path) -> tuple[dict[str, Any], bool]:
    state = load_state(root)
    changed = False
    try:
        for relative, source in MANAGED_FILES.items():
            target = prefixed(root, relative)
            ensure_no_symlink_parents(root, target)
            desired = content_for(source)
            if target.exists() and not target.is_symlink():
                if not target.is_file():
                    raise InstallError(f"refusing to replace non-file destination {target}")
                if target.read_bytes() == desired:
                    entry = state.setdefault("entries", {}).setdefault(relative, {"kind": "preserve", "backup": None, "original_target": None})
                    entry["installed_sha256"] = hashlib.sha256(desired).hexdigest()
                    continue
            backup_existing(root, target, relative, state)
            write_state(root, state)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                target.unlink()
            target.write_bytes(desired)
            target.chmod(0o644)
            state["entries"][relative]["installed_sha256"] = hashlib.sha256(desired).hexdigest()
            write_state(root, state)
            changed = True
        for relative, link_target in MANAGED_LINKS.items():
            target = prefixed(root, relative)
            ensure_no_symlink_parents(root, target)
            if target.is_symlink() and os.readlink(target) == link_target:
                entry = state.setdefault("entries", {}).setdefault(relative, {"kind": "preserve", "backup": None, "original_target": None})
                entry["installed_target"] = link_target
                continue
            backup_existing(root, target, relative, state)
            write_state(root, state)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(link_target)
            state["entries"][relative]["installed_target"] = link_target
            write_state(root, state)
            changed = True
        write_state(root, state)
    except (OSError, InstallError) as exc:
        rollback_files(root)
        if isinstance(exc, InstallError):
            raise
        raise InstallError(f"file installation failed; prior files were restored: {exc}") from exc
    return state, changed


def rollback_files(root: pathlib.Path) -> list[str]:
    state = load_state(root)
    entries = state.get("entries", {})
    messages = []

    # Validate backups, edited-file destinations and every managed parent before
    # mutating anything. A later conflict must not leave a half-rolled-back set.
    pending_edits: dict[str, tuple[pathlib.Path, bool, str | None]] = {}
    for relative, entry in entries.items():
        target = prefixed(root, relative)
        ensure_no_symlink_parents(root, target)
        if target.exists() and target.is_dir() and not target.is_symlink():
            raise InstallError(f"refusing to recursively remove unexpected directory {target}")
        if entry["kind"] == "file":
            backup_value = entry.get("backup")
            if not backup_value:
                raise InstallError(f"required backup is missing for {relative}")
            backup = prefixed(root, backup_value)
            ensure_no_symlink_parents(root, backup)
            if backup.is_symlink() or not backup.is_file():
                raise InstallError(f"required backup is missing or unsafe: {backup}")
        edited = False
        if target.is_symlink():
            edited = bool(entry.get("installed_target") and os.readlink(target) != entry["installed_target"])
        elif target.is_file():
            installed_hash = entry.get("installed_sha256")
            edited = installed_hash is None or hashlib.sha256(target.read_bytes()).hexdigest() != installed_hash
        elif entry["kind"] == "absent" and target.exists():
            edited = True
        if edited:
            edited_rel = f"/var/lib/asahi-system/niri-performance/rollback-edits{relative}"
            saved_edit = prefixed(root, edited_rel)
            ensure_no_symlink_parents(root, saved_edit)
            if saved_edit.exists() or saved_edit.is_symlink():
                raise InstallError(f"rollback edit destination already exists: {saved_edit}")
            pending_edits[relative] = (saved_edit, target.is_symlink(), edited_rel)
    state_path = prefixed(root, STATE_PATH)
    ensure_no_symlink_parents(root, state_path)

    for relative, entry in reversed(list(entries.items())):
        target = prefixed(root, relative)
        if entry["kind"] == "preserve":
            messages.append(f"left pre-existing identical file unchanged: {relative}")
            continue
        if relative in pending_edits:
            saved_edit, was_symlink, edited_rel = pending_edits[relative]
            saved_edit.parent.mkdir(parents=True, exist_ok=True)
            if was_symlink:
                saved_edit.symlink_to(os.readlink(target))
            else:
                shutil.copy2(target, saved_edit)
            messages.append(f"saved post-install edits at {edited_rel}")
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.exists() and target.is_dir():
            raise InstallError(f"refusing to recursively remove unexpected directory {target}")
        if entry["kind"] == "file":
            backup = prefixed(root, entry["backup"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
            messages.append(f"restored {relative}")
        elif entry["kind"] == "symlink":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(entry["original_target"])
            messages.append(f"restored symlink {relative}")
        else:
            messages.append(f"removed {relative}")
    if state_path.exists():
        state_path.unlink()
    return messages


def installed_rpm_packages() -> set[str]:
    return installed_rpm_packages_for((*PACKAGES, QUICKSHELL_PACKAGE))


def install_locked_packages(engine: dict[str, str]) -> None:
    if shutil.which("dnf") is None:
        raise InstallError("dnf was not found")
    if not engine.get("nevra") or not engine.get("repository") or not engine.get("gpg_key"):
        raise InstallError("Quickshell engine package lock is incomplete")
    try:
        subprocess.run([
            "dnf", "install", "-y", "--setopt=install_weak_deps=False",
            f"--repofrompath=niri-plus-quickshell,{engine['repository']}", "--enablerepo=niri-plus-quickshell",
            "--setopt=niri-plus-quickshell.gpgcheck=1", f"--setopt=niri-plus-quickshell.gpgkey={engine['gpg_key']}",
            *PACKAGES, engine["nevra"],
        ], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InstallError(f"DNF package transaction failed: {exc}") from exc


def apply_install(root: pathlib.Path = pathlib.Path("/"), os_release: dict[str, str] | None = None,
                  machine: str | None = None, *, defer_packages: bool = False) -> None:
    release = os_release if os_release is not None else parse_os_release()
    arch = machine if machine is not None else platform.machine()
    errors = target_mismatches(release, arch)
    if errors:
        raise InstallError("incompatible host: " + "; ".join(errors))
    if os.geteuid() != 0:
        raise InstallError("--apply requires root; use sudo")
    if root != pathlib.Path("/"):
        raise InstallError("test roots are not accepted by the installer CLI")
    if shutil.which("dnf") is None:
        raise InstallError("dnf was not found")
    from . import quickshell
    qs = quickshell.report()
    if qs["checkout_status"] != "OK":
        raise InstallError(f"Quickshell checkout does not match the pinned commit ({qs['checkout_status']}; expected {qs['expected_commit']}, got {qs['installed_commit']}); initialize the pinned submodule and rerun bootstrap")
    lock = quickshell.load_lock()
    engine = lock.get("engine", {})
    if not engine.get("nevra") or not engine.get("repository") or not engine.get("gpg_key"):
        raise InstallError("Quickshell engine package lock is incomplete")
    state = load_state(root)
    legacy_entries = set(state.get("entries", {})) & LEGACY_MANAGED_PATHS
    if legacy_entries:
        tracked_packages = state.get("packages_installed_by_us", [])
        print("Migrating the legacy custom Niri session to Fedora's packaged Niri session.")
        rollback_files(root)
        state = load_state(root)
        if tracked_packages:
            state["packages_installed_by_us"] = tracked_packages
            write_state(root, state)
    installed_before = installed_rpm_packages()
    state["packages_installed_by_us"] = sorted(
        set(state.get("packages_installed_by_us", [])) | (set((*PACKAGES, QUICKSHELL_PACKAGE)) - installed_before)
    )
    write_state(root, state)
    _, changed = install_files(root)
    if not defer_packages:
        try:
            install_locked_packages(engine)
        except InstallError as exc:
            rollback_files(root)
            retained = load_state(root)
            retained["packages_installed_by_us"] = state.get("packages_installed_by_us", [])
            write_state(root, retained)
            raise InstallError(f"DNF installation failed; session files were rolled back: {exc}") from exc
    state = load_state(root)
    write_state(root, state)
    if "known_good_version" in state and "applied_version" not in state:
        state["applied_version"] = state.pop("known_good_version")
    state.pop("known_good_version", None)
    state["applied_version"] = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    state.setdefault("quickshell", {})["expected_commit"] = lock["commit"]
    state["quickshell"].setdefault("known_good_commit", None)
    write_state(root, state)
    if not defer_packages:
        reload_invoking_user_manager()
    if not defer_packages:
        print("Installed the Niri session. Existing user services remain untouched.")
        if not changed:
            print("Session files were already current (idempotent run).")
        missing = set(BASE_SERVICES) - installed_rpm_packages_for(BASE_SERVICES)
        if missing:
            print("WARNING: baseline audio/network packages not found; no service or package changes were made for them: " + ", ".join(sorted(missing)), file=sys.stderr)
        print("Quickshell runtime/API and visual behavior remain M1_REQUIRED until a Niri login passes health checks.")


def reload_invoking_user_manager() -> None:
    """Reload only the caller's user unit definitions; never start/stop a unit."""
    uid = os.environ.get("SUDO_UID")
    if not uid or uid == "0":
        print("NOTE: user systemd manager was not reloaded; it will read the unit files at next login.")
        return
    runtime = pathlib.Path("/run/user") / uid
    bus = runtime / "bus"
    try:
        account = pwd.getpwuid(int(uid))
    except (KeyError, ValueError):
        print("WARNING: could not identify invoking user; unit definitions reload at next login.", file=sys.stderr)
        return
    if not bus.exists():
        print("NOTE: no invoking-user D-Bus session; unit definitions reload at next login.")
        return
    env = ["env", f"XDG_RUNTIME_DIR={runtime}", f"DBUS_SESSION_BUS_ADDRESS=unix:path={bus}", "systemctl", "--user", "daemon-reload"]
    try:
        subprocess.run(["runuser", "-u", account.pw_name, "--", *env], check=True, stdout=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        print("WARNING: could not reload the invoking user's systemd manager; unit definitions reload at next login.", file=sys.stderr)


def installed_rpm_packages_for(names: tuple[str, ...]) -> set[str]:
    present = set()
    for package in names:
        if subprocess.run(["rpm", "-q", package], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            present.add(package)
    return present


def rollback(root: pathlib.Path = pathlib.Path("/"), remove_packages: bool = False,
             require_root: bool = True) -> None:
    if require_root and os.geteuid() != 0:
        raise InstallError("--rollback requires root; use sudo")
    state = load_state(root)
    messages = rollback_files(root)
    for message in messages:
        print(message)
    state["entries"] = {}
    if remove_packages:
        packages = state.get("packages_installed_by_us", [])
        if not isinstance(packages, list) or any(package not in (*PACKAGES, QUICKSHELL_PACKAGE) for package in packages):
            raise InstallError("install state contains an unexpected package name; refusing package removal")
        # Keep package ownership on disk until DNF confirms the removal. If it
        # fails, a later uninstall can still retry without touching user files.
        if packages:
            write_state(root, state)
        if packages:
            print("Removing only explicit packages recorded as absent before install; dependencies are retained.")
            subprocess.run(["dnf", "remove", "--noautoremove", "-y", *packages], check=True)
        else:
            print("No explicit package is recorded as newly installed; no packages removed.")
        state_path = prefixed(root, STATE_PATH)
        if state_path.exists():
            state_path.unlink()
    else:
        if state.get("packages_installed_by_us"):
            write_state(root, state)
        else:
            state_path = prefixed(root, STATE_PATH)
            if state_path.exists():
                state_path.unlink()
        print("Packages remain installed. Pass --remove-packages to remove only packages recorded as newly installed; dependencies remain.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="print the plan only (default)")
    action.add_argument("--apply", action="store_true", help="install after strict host checks; requires root")
    action.add_argument("--rollback", action="store_true", help="restore backed-up files and remove session files")
    parser.add_argument("--remove-packages", action="store_true", help="with --rollback, remove only explicit packages newly installed by this installer")
    args = parser.parse_args(argv)
    if args.remove_packages and not args.rollback:
        parser.error("--remove-packages requires --rollback")
    try:
        if args.apply:
            apply_install()
        elif args.rollback:
            rollback(remove_packages=args.remove_packages)
        else:
            render_plan()
            errors = target_mismatches(parse_os_release(), platform.machine())
            if errors:
                print("Apply will refuse this host: " + "; ".join(errors), file=sys.stderr)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed with exit {exc.returncode}: {exc.cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
