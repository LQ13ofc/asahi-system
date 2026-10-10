#!/usr/bin/python3 -I
"""Protected local recovery bundle for Niri+ managed files (stdlib only)."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import pathlib
import posixpath
import re
import shutil
import secrets
import stat
import subprocess
import tarfile
import tempfile
import time
from contextlib import contextmanager
from typing import Any

RECOVERY_DIR = pathlib.Path("/var/lib/niri-plus/recovery")
MANIFEST_NAME = "NIRI_PLUS_RECOVERY.json"
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
QUICKSHELL_PACKAGE = "quickshell"
QUICKSHELL_REPOSITORY = "https://github.com/LQ13ofc/quickshell-.git"
SYSTEM_PACKAGES = (
    "niri", "foot", "fuzzel", "xdg-desktop-portal-gtk", "lxqt-policykit",
    "python3-dbus", "python3-gobject",
)
MANAGED_ROOTS = tuple(pathlib.Path(value) for value in (
    "/etc/niri/autostart.kdl", "/etc/niri/config.kdl", "/etc/niri/keybinds.kdl",
    "/etc/niri/outputs.kdl", "/etc/niri/rules.kdl", "/usr/local/bin/niri+",
    "/usr/local/lib/niri-plus", "/usr/local/libexec/niri-plus-recover",
    "/usr/local/share/niri-plus", "/var/lib/niri-plus/bootstrap-backups",
    "/var/lib/niri-plus/bootstrap.json", "/var/lib/niri-plus/plugins.json",
    "/usr/lib/systemd/user/asahi-niri-polkit-agent.service",
    "/usr/lib/systemd/user/asahi-niri-wayland-ready.service",
    "/usr/lib/systemd/user/asahi-quickshell.service",
    "/var/lib/asahi-system/niri-performance/backups",
    "/var/lib/asahi-system/niri-performance/rollback-edits",
    "/var/lib/asahi-system/niri-performance/state.json",
    "/usr/lib/systemd/user/akonadi_control.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/app-org.kde.discover.notifier@autostart.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/app-org.kde.kalendarac@autostart.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/app-org.kde.kdeconnect.daemon@autostart.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/app-org.kde.xwaylandvideobridge@autostart.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/graphical-session.target.wants/asahi-niri-polkit-agent.service",
    "/usr/lib/systemd/user/graphical-session.target.wants/asahi-niri-wayland-ready.service",
    "/usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service",
    "/usr/lib/systemd/user/kde-baloo.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/kunifiedpush-distributor.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/plasma-gmenudbusmenuproxy.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/plasma-kaccess.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/plasma-kactivitymanagerd.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/plasma-kded6.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/plasma-ksmserver.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/plasma-kwin_wayland.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/plasma-plasmashell.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/plasma-polkit-agent.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/plasma-powerdevil.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/plasma-xdg-desktop-portal-kde.service.d/10-niri-session.conf",
    "/usr/lib/systemd/user/plasma-xembedsniproxy.service.d/10-niri-session.conf",
))


class RecoveryError(RuntimeError):
    pass


def _host(root: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    return path if root == pathlib.Path("/") else root / path.as_posix().lstrip("/")


def managed_roots() -> list[pathlib.Path]:
    return sorted(MANAGED_ROOTS, key=lambda p: (len(p.parts), p.as_posix()))


def _is_under_allowed(name: str, roots: list[str]) -> bool:
    return any(name == root or name.startswith(root.rstrip("/") + "/") for root in roots)


def _package_inventory(runner=subprocess.run) -> dict[str, Any]:
    names = sorted(set((*SYSTEM_PACKAGES, QUICKSHELL_PACKAGE)))
    try:
        result = runner(["rpm", "-q", "--qf", "%{NAME}\t%|EPOCH?{%{EPOCH}:}:{0:}|%{VERSION}-%{RELEASE}.%{ARCH}\n", *names],
                        check=False, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "UNAVAILABLE", "packages": [], "detail": str(exc)}
    packages = []
    for line in (result.stdout or "").splitlines():
        fields = line.split("\t", 1)
        if len(fields) == 2 and fields[0] in names:
            try:
                requirements = runner(["rpm", "-q", "--requires", fields[0]],
                                      check=False, capture_output=True, text=True, timeout=3)
            except (OSError, subprocess.SubprocessError) as exc:
                requirements = None
                requirement_detail = str(exc)
            else:
                requirement_detail = None
            packages.append({
                "name": fields[0], "nevra": fields[1],
                "requires": ([line for line in (requirements.stdout or "").splitlines() if line.strip()]
                             if requirements is not None and requirements.returncode == 0 else None),
                "requires_status": ("AVAILABLE" if requirements is not None and requirements.returncode == 0
                                    else "UNAVAILABLE"),
                "requires_detail": requirement_detail,
            })
    return {"status": "AVAILABLE" if result.returncode == 0 else "PARTIAL", "packages": packages}


def _cached_quickshell_rpm(root: pathlib.Path, expected_nevra: str, runner=subprocess.run) -> pathlib.Path | None:
    for cache in (pathlib.Path("/var/cache/dnf"), pathlib.Path("/var/cache/libdnf5")):
        host_cache = _host(root, cache)
        if not host_cache.is_dir() or host_cache.is_symlink():
            continue
        for candidate in host_cache.rglob("*quickshell*.rpm"):
            try:
                metadata = candidate.lstat()
                if not stat.S_ISREG(metadata.st_mode) or candidate.is_symlink() or metadata.st_nlink != 1:
                    continue
                if root == pathlib.Path("/") and (metadata.st_uid != 0 or metadata.st_mode & 0o022):
                    continue
                result = runner(
                    ["rpm", "-qp", "--qf", "%|EPOCH?{%{EPOCH}:}:{0:}|%{VERSION}-%{RELEASE}.%{ARCH}", str(candidate)],
                    check=False, capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and (result.stdout or "").strip() == expected_nevra:
                    signature = runner(
                        ["rpmkeys", "--checksig", "--verbose", str(candidate)],
                        check=False, capture_output=True, text=True, timeout=10,
                    )
                    if _rpm_signature_is_trusted(signature):
                        return candidate
            except (OSError, subprocess.SubprocessError):
                continue
    return None


def has_rpm_payload(bundle: pathlib.Path, package: str, nevra: str,
                    root: pathlib.Path = pathlib.Path("/")) -> bool:
    manifest, _records = _verify_bundle(bundle, root)
    return any(item.get("name") == package and item.get("nevra") == nevra
               for item in manifest.get("rpm_payloads", []))


def _safe_root(path: pathlib.Path, root: pathlib.Path) -> None:
    host_path = _host(root, path)
    current = pathlib.Path("/")
    for part in host_path.absolute().parts[1:-1]:
        current /= part
        if current.is_symlink():
            raise RecoveryError(f"managed recovery path has symlinked component: {current}")


def _recovery_store(root: pathlib.Path, *, create: bool = False) -> pathlib.Path:
    store = _host(root, RECOVERY_DIR)
    _safe_root(RECOVERY_DIR, root)
    if not store.exists() and not store.is_symlink() and create:
        store.mkdir(parents=True, mode=0o700)
    if store.is_symlink() or not store.is_dir():
        raise RecoveryError("recovery storage is missing, symlinked, or not a directory")
    metadata = store.stat()
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RecoveryError("recovery storage must have mode 0700")
    if root == pathlib.Path("/"):
        if metadata.st_uid != 0:
            raise RecoveryError("recovery storage must be root-owned mode 0700")
        parent = store.parent
        while parent == pathlib.Path("/var/lib/niri-plus") or parent == pathlib.Path("/var/lib") or parent == pathlib.Path("/var"):
            parent_metadata = parent.stat()
            if parent_metadata.st_uid != 0 or parent_metadata.st_mode & 0o022:
                raise RecoveryError(f"recovery storage parent is not root-controlled: {parent}")
            parent = parent.parent
    return store


@contextmanager
def _operation_lock(root: pathlib.Path):
    lock_path = pathlib.Path("/run/niri-plus-operation.lock")
    if root != pathlib.Path("/"):
        lock_path = root / lock_path.as_posix().lstrip("/")
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    elif (not lock_path.parent.is_dir() or lock_path.parent.is_symlink()
          or lock_path.parent.stat().st_uid != 0 or lock_path.parent.stat().st_mode & 0o022):
        raise RecoveryError("safe system operation-lock directory is unavailable")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or (root == pathlib.Path("/") and metadata.st_uid != 0)):
            os.close(descriptor)
            raise RecoveryError("Niri+ operation lock is not a protected regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise RecoveryError("another Niri+ install, rollback or recovery is already running") from exc
    except OSError as exc:
        raise RecoveryError(f"cannot acquire Niri+ operation lock: {exc}") from exc
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _mode(path: pathlib.Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _stream_sha256(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _file_sha256(path: pathlib.Path) -> str:
    with path.open("rb") as stream:
        return _stream_sha256(stream)


def _rpm_signature_is_trusted(result: subprocess.CompletedProcess) -> bool:
    """Require rpmkeys to affirm a signature, not merely a successful digest check."""
    if result.returncode != 0:
        return False
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    if re.search(r"\b(?:NOKEY|NOTTRUSTED|NOT OK|BAD|FAILED)\b", output, re.IGNORECASE):
        return False
    summary = re.search(r"\bdigests\s+signatures\s+OK\b", output, re.IGNORECASE)
    verbose_signature = re.search(r"\bSignature\b[^\n]*:\s*OK\b", output, re.IGNORECASE)
    verbose_digest = re.search(r"\bdigest\b[^\n]*:\s*OK\b", output, re.IGNORECASE)
    # rpmkeys -v versions differ: some emit a one-line summary; others list
    # each header/payload digest and the GPG signature separately. Digest-only
    # output is never sufficient for an offline package restore.
    return bool(summary or (verbose_signature and verbose_digest))


def _read_managed_json(root: pathlib.Path, path: pathlib.Path, label: str, default: dict) -> dict:
    _safe_root(path, root)
    target = _host(root, path)
    if not target.exists() and not target.is_symlink():
        return default
    try:
        metadata = target.lstat()
    except OSError as exc:
        raise RecoveryError(f"cannot inspect {label} before recovery: {exc}") from exc
    if (not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1
            or metadata.st_mode & 0o022):
        raise RecoveryError(f"{label} is not a safe regular managed file")
    if root == pathlib.Path("/") and metadata.st_uid != 0:
        raise RecoveryError(f"{label} is not root-owned")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryError(f"{label} cannot be validated; recovery preparation stopped") from exc
    if not isinstance(value, dict):
        raise RecoveryError(f"{label} must contain a JSON object")
    return value


def _member_record(member: tarfile.TarInfo, archive: tarfile.TarFile) -> dict[str, Any]:
    kind = "file" if member.isfile() else "directory" if member.isdir() else "symlink" if member.issym() else "unsupported"
    if kind == "file":
        stream = archive.extractfile(member)
        if stream is None:
            raise RecoveryError(f"recovery archive member cannot be read: {member.name}")
        digest = _stream_sha256(stream)
    elif kind == "symlink":
        digest = hashlib.sha256(member.linkname.encode("utf-8")).hexdigest()
    elif kind == "directory":
        digest = hashlib.sha256(b"").hexdigest()
    else:
        raise RecoveryError(f"unsupported file type in recovery archive: {member.name}")
    return {"name": _canonical_member_name(member.name), "type": kind, "mode": member.mode, "uid": member.uid,
            "gid": member.gid, "size": member.size, "linkname": member.linkname if kind == "symlink" else None,
            "sha256": digest}


def _canonical_member_name(name: str) -> str:
    if not isinstance(name, str) or "\x00" in name or name.startswith("/"):
        raise RecoveryError("recovery archive contains an absolute or invalid path")
    normalized = posixpath.normpath(name.rstrip("/"))
    if normalized in {"", ".", ".."} or normalized.startswith("../") or normalized != name.rstrip("/"):
        raise RecoveryError(f"recovery archive path is not canonical: {name}")
    return normalized


def _verify_bundle(bundle: pathlib.Path, root: pathlib.Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    store = _recovery_store(root)
    try:
        if bundle.parent.resolve(strict=True) != store.resolve(strict=True):
            raise RecoveryError("recovery bundle must be stored in the protected recovery directory")
    except OSError as exc:
        raise RecoveryError(f"recovery storage cannot be resolved safely: {exc}") from exc
    if bundle.is_symlink() or not bundle.is_file():
        raise RecoveryError(f"recovery bundle is missing or is a symlink: {bundle}")
    sidecar = bundle.with_suffix(bundle.suffix + ".sha256")
    if sidecar.is_symlink() or not sidecar.is_file():
        raise RecoveryError("recovery checksum sidecar is missing or unsafe")
    for path in (bundle, sidecar):
        metadata = path.stat()
        if metadata.st_nlink != 1:
            raise RecoveryError(f"recovery file has an unexpected hard-link count: {path}")
        if root == pathlib.Path("/") and metadata.st_uid != 0:
            raise RecoveryError(f"recovery file is not root-owned: {path}")
        if metadata.st_mode & 0o077:
            raise RecoveryError(f"recovery file permissions are too broad: {path}")
    if bundle.stat().st_size > MAX_ARCHIVE_BYTES:
        raise RecoveryError("recovery bundle exceeds the 2 GiB safety limit")
    with bundle.open("rb") as stream:
        digest = _stream_sha256(stream)
    if sidecar.stat().st_size > 256:
        raise RecoveryError("recovery checksum sidecar is oversized")
    sidecar_fields = sidecar.read_text(encoding="ascii").strip().split("  ")
    if (len(sidecar_fields) != 2 or re.fullmatch(r"[0-9a-f]{64}", sidecar_fields[0]) is None
            or sidecar_fields[1] != bundle.name):
        raise RecoveryError("recovery checksum sidecar has invalid format")
    expected_digest = sidecar_fields[0]
    if digest != expected_digest:
        raise RecoveryError("recovery bundle checksum does not match its protected sidecar")
    try:
        with tarfile.open(bundle, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > 250_000:
                raise RecoveryError("recovery bundle has too many filesystem entries")
            manifest_member = [member for member in members if member.name == MANIFEST_NAME]
            if len(manifest_member) != 1 or not manifest_member[0].isfile():
                raise RecoveryError("recovery bundle manifest is missing or ambiguous")
            if manifest_member[0].size > MAX_MANIFEST_BYTES:
                raise RecoveryError("recovery manifest is oversized")
            manifest_stream = archive.extractfile(manifest_member[0])
            if manifest_stream is None:
                raise RecoveryError("recovery manifest cannot be read")
            manifest = json.loads(manifest_stream.read().decode("utf-8"))
            if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                    or not isinstance(manifest.get("roots"), list)):
                raise RecoveryError("unsupported recovery manifest schema")
            roots = manifest["roots"]
            if roots != [path.as_posix().lstrip("/") for path in managed_roots()]:
                raise RecoveryError("recovery bundle managed path set is not recognized")
            records = []
            seen = set()
            expanded_size = 0
            for member in members:
                if member.name == MANIFEST_NAME:
                    continue
                name = _canonical_member_name(member.name)
                package_payload = name.startswith("recovery-rpms/") and name.count("/") == 1
                if name in seen or not (_is_under_allowed(name, roots) or package_payload):
                    raise RecoveryError(f"duplicate or unowned path in recovery bundle: {name}")
                seen.add(name)
                if member.mode & 0o7000:
                    raise RecoveryError(f"special permission bits in recovery bundle: {name}")
                if member.isfile():
                    expanded_size += member.size
                    if expanded_size > MAX_ARCHIVE_BYTES:
                        raise RecoveryError("recovery payload expands beyond the 2 GiB safety limit")
                record = _member_record(member, archive)
                if package_payload and not name.endswith(".rpm"):
                    raise RecoveryError("recovery package payload is not an RPM")
                if record["type"] == "symlink":
                    if package_payload:
                        raise RecoveryError("RPM rollback payload must be a regular file")
                    if member.linkname.startswith("/") or "\x00" in member.linkname:
                        raise RecoveryError(f"unsafe symlink in recovery bundle: {name}")
                    resolved_link = posixpath.normpath(posixpath.join(posixpath.dirname(name), member.linkname))
                    if not _is_under_allowed(resolved_link, roots):
                        raise RecoveryError(f"symlink escapes Niri+ managed paths: {name}")
                records.append(record)
            absent_roots = manifest.get("absent_roots")
            if (not isinstance(absent_roots, list) or len(absent_roots) != len(set(absent_roots))
                    or any(not isinstance(root_name, str) or root_name not in roots for root_name in absent_roots)):
                raise RecoveryError("recovery manifest has invalid absent-root metadata")
            root_names = {record["name"] for record in records}
            if any((root_name in root_names) == (root_name in absent_roots) for root_name in roots):
                raise RecoveryError("recovery archive does not account for every managed root exactly once")
            expected = manifest.get("members")
            if not isinstance(expected, list) or sorted(expected, key=lambda item: item["name"]) != sorted(records, key=lambda item: item["name"]):
                raise RecoveryError("recovery payload does not match its embedded manifest")
            rpm_payloads = manifest.get("rpm_payloads", [])
            if not isinstance(rpm_payloads, list) or any(not isinstance(payload, dict) for payload in rpm_payloads):
                raise RecoveryError("recovery RPM payload metadata is invalid")
            for payload in rpm_payloads:
                record = next((item for item in records if item["name"] == payload.get("member")), None)
                if (not record or record["type"] != "file" or record["sha256"] != payload.get("sha256")
                        or not payload.get("name") or not payload.get("nevra")):
                    raise RecoveryError("recovery RPM payload does not match its metadata")
            rpm_inventory = manifest.get("rpm_inventory")
            if (not isinstance(rpm_inventory, dict) or not isinstance(rpm_inventory.get("packages"), list)
                    or any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                           or not isinstance(item.get("nevra"), str)
                           or item["name"] not in (*SYSTEM_PACKAGES, QUICKSHELL_PACKAGE)
                           or (item.get("requires") is not None and
                               (not isinstance(item.get("requires"), list)
                                or any(not isinstance(value, str) for value in item["requires"])))
                           for item in rpm_inventory["packages"])):
                raise RecoveryError("recovery RPM inventory metadata is invalid")
            package_names = [item["name"] for item in rpm_inventory["packages"]]
            if len(package_names) != len(set(package_names)):
                raise RecoveryError("recovery RPM inventory contains duplicate package records")
            package_ownership = manifest.get("package_ownership")
            if (not isinstance(package_ownership, list)
                    or any(not isinstance(name, str) or name not in (*SYSTEM_PACKAGES, QUICKSHELL_PACKAGE)
                           for name in package_ownership)):
                raise RecoveryError("recovery package ownership metadata is invalid")
            plugins = manifest.get("plugins")
            if not isinstance(plugins, dict) or plugins.get("schema_version") != 1 or not isinstance(plugins.get("plugins"), dict):
                raise RecoveryError("recovery plugin manifest metadata is invalid")
            if any(name != "quickshell" for name in plugins["plugins"]):
                raise RecoveryError("recovery bundle contains an unsupported plugin")
            plugin_record = plugins["plugins"].get("quickshell")
            if plugin_record is not None and (
                    not isinstance(plugin_record, dict)
                    or plugin_record.get("repository") != QUICKSHELL_REPOSITORY
                    or re.fullmatch(r"[0-9a-f]{40}", str(plugin_record.get("commit", ""))) is None
                    or not isinstance(plugin_record.get("engine_nevra"), str)):
                raise RecoveryError("recovery Quickshell plugin pin metadata is invalid")
            if plugin_record is not None and plugin_record["commit"] != manifest.get("quickshell_commit"):
                raise RecoveryError("recovery plugin manifest and bootstrap Quickshell pin disagree")
            if (not isinstance(manifest.get("niri_plus_version"), str)
                    or (manifest.get("asahi_system_commit") is not None
                        and re.fullmatch(r"[0-9a-f]{40}", str(manifest["asahi_system_commit"])) is None)
                    or (manifest.get("quickshell_commit") is not None
                        and re.fullmatch(r"[0-9a-f]{40}", str(manifest["quickshell_commit"])) is None)):
                raise RecoveryError("recovery version/commit metadata is invalid")
            record_names = {record["name"]: record["type"] for record in records}
            for record in records:
                parent = pathlib.PurePosixPath(record["name"]).parent
                while parent.as_posix() != ".":
                    if record_names.get(parent.as_posix()) == "symlink":
                        raise RecoveryError(f"recovery path traverses a symlink: {record['name']}")
                    parent = parent.parent
            return manifest, records
    except (OSError, tarfile.TarError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError(f"cannot validate recovery bundle: {exc}") from exc


def prepare_bundle(root: pathlib.Path = pathlib.Path("/"), *, runner=subprocess.run,
                   operation_locked: bool = False) -> pathlib.Path:
    """Create a root-owned bundle before a modifying install transaction."""
    if not operation_locked:
        with _operation_lock(root):
            return prepare_bundle(root, runner=runner, operation_locked=True)
    if root == pathlib.Path("/") and os.geteuid() != 0:
        raise RecoveryError("recovery preparation requires root")
    store = _recovery_store(root, create=True)
    roots = managed_roots()
    for path in roots:
        _safe_root(path, root)
    for managed in (pathlib.Path("/usr/local/lib/niri-plus"), pathlib.Path("/usr/local/share/niri-plus")):
        current = _host(root, managed)
        if current.exists() and root == pathlib.Path("/"):
            if current.is_symlink() or current.stat().st_uid != 0 or current.stat().st_mode & 0o022:
                raise RecoveryError(f"Niri+ destination ownership is unsafe: {current}")
    reserve = 32 * 1024 * 1024
    if shutil.disk_usage(store).free < reserve:
        raise RecoveryError("less than 32 MiB is available for protected recovery metadata")

    stamp = (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
             + f"-{time.time_ns()}-{os.getpid()}-{secrets.token_hex(4)}")
    destination = store / f"recovery-{stamp}.tar.gz"
    descriptor, temporary = tempfile.mkstemp(prefix=".recovery-", suffix=".tar.gz", dir=store)
    os.close(descriptor)
    payload_descriptor, payload_temporary = tempfile.mkstemp(prefix=".recovery-payload-", suffix=".tar.gz", dir=store)
    os.close(payload_descriptor)
    sidecar_tmp = None
    try:
        rpm_inventory = _package_inventory(runner)
        rpm_payloads = []
        current_qs_nevra = next((item["nevra"] for item in rpm_inventory.get("packages", [])
                                 if item.get("name") == QUICKSHELL_PACKAGE), None)
        current_qs_rpm = (_cached_quickshell_rpm(root, current_qs_nevra, runner)
                          if current_qs_nevra else None)
        with tarfile.open(payload_temporary, "w:gz", format=tarfile.PAX_FORMAT) as payload_archive:
            absent_roots = []
            for path in roots:
                target = _host(root, path)
                arcname = path.as_posix().lstrip("/")
                if not target.exists() and not target.is_symlink():
                    absent_roots.append(arcname)
                    continue
                payload_archive.add(target, arcname=arcname, recursive=True)
            if current_qs_rpm is not None:
                rpm_member = "recovery-rpms/" + current_qs_rpm.name
                payload_archive.add(current_qs_rpm, arcname=rpm_member, recursive=False)
                rpm_payloads.append({
                    "name": QUICKSHELL_PACKAGE,
                    "nevra": current_qs_nevra,
                    "member": rpm_member,
                    "sha256": _file_sha256(current_qs_rpm),
                })
        with tarfile.open(payload_temporary, "r:gz") as check:
            members = [_member_record(member, check) for member in check.getmembers()]
        state = _read_managed_json(root, pathlib.Path("/var/lib/niri-plus/bootstrap.json"),
                                   "Niri+ bootstrap state", {})
        plugin_manifest = _read_managed_json(root, pathlib.Path("/var/lib/niri-plus/plugins.json"),
                                             "Niri+ plugin manifest", {"schema_version": 1, "plugins": {}})
        if plugin_manifest.get("schema_version") != 1 or not isinstance(plugin_manifest.get("plugins"), dict):
            raise RecoveryError("Niri+ plugin manifest cannot be safely preserved")
        install_state = _read_managed_json(root, pathlib.Path("/var/lib/asahi-system/niri-performance/state.json"),
                                           "Niri+ package ownership state", {})
        version_path = _host(root, pathlib.Path("/usr/local/share/niri-plus/VERSION"))
        if version_path.is_symlink():
            raise RecoveryError("Niri+ VERSION is a symlink; recovery preparation stopped")
        if version_path.exists():
            version_metadata = version_path.lstat()
            if (not stat.S_ISREG(version_metadata.st_mode) or version_metadata.st_nlink != 1
                    or version_metadata.st_mode & 0o022
                    or (root == pathlib.Path("/") and version_metadata.st_uid != 0)):
                raise RecoveryError("Niri+ VERSION file is not safely owned for recovery")
        version = version_path.read_text(encoding="utf-8").strip() if version_path.is_file() else "not-installed"
        package_ownership = install_state.get("packages_installed_by_us", [])
        if (not isinstance(package_ownership, list)
                or any(not isinstance(name, str) or name not in (*SYSTEM_PACKAGES, QUICKSHELL_PACKAGE)
                       for name in package_ownership)):
            raise RecoveryError("Niri+ package ownership state is invalid")
        manifest = {
            "schema_version": 1,
            "created_utc": stamp,
            "niri_plus_version": version,
            "asahi_system_commit": state.get("source_commit"),
            "source_channel": state.get("source_channel"),
            "quickshell_commit": state.get("quickshell_expected_commit"),
            "plugins": plugin_manifest,
            "package_ownership": package_ownership,
            "rpm_inventory": rpm_inventory,
            "rpm_payloads": rpm_payloads,
            "roots": [path.as_posix().lstrip("/") for path in roots],
            "absent_roots": absent_roots,
            "members": members,
            "scope": "Niri+ managed files/state and cached Quickshell RPM when available; other RPM transactions are inventoried, not reverted.",
        }
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            with tarfile.open(payload_temporary, "r:gz") as source_archive:
                for member in source_archive.getmembers():
                    stream = source_archive.extractfile(member) if member.isfile() else None
                    archive.addfile(member, stream)
            info = tarfile.TarInfo(MANIFEST_NAME)
            info.size = len(payload)
            info.mode = 0o600
            info.uid = 0
            info.gid = 0
            archive.addfile(info, io.BytesIO(payload))
        os.chmod(temporary, 0o600)
        with open(temporary, "rb") as stream:
            os.fsync(stream.fileno())
        digest = _file_sha256(pathlib.Path(temporary))
        sidecar = destination.with_suffix(destination.suffix + ".sha256")
        sidecar_fd, sidecar_tmp = tempfile.mkstemp(prefix=".recovery-checksum-", dir=store)
        with os.fdopen(sidecar_fd, "w", encoding="ascii") as output:
            output.write(digest + "  " + destination.name + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(sidecar_tmp, 0o600)
        if root == pathlib.Path("/"):
            os.chown(sidecar_tmp, 0, 0)
            os.chown(temporary, 0, 0)
        os.replace(sidecar_tmp, sidecar)
        sidecar_tmp = None
        os.replace(temporary, destination)
        directory_fd = os.open(store, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        try:
            _verify_bundle(destination, root)
        except RecoveryError:
            destination.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
            raise
        if root == pathlib.Path("/"):
            os.chown(sidecar, 0, 0)
        return destination
    finally:
        if os.path.exists(payload_temporary):
            os.unlink(payload_temporary)
        if os.path.exists(temporary):
            os.unlink(temporary)
        if sidecar_tmp and os.path.exists(sidecar_tmp):
            os.unlink(sidecar_tmp)


def latest_bundle(root: pathlib.Path = pathlib.Path("/")) -> pathlib.Path | None:
    try:
        store = _recovery_store(root)
    except RecoveryError:
        return None
    bundles = sorted(store.glob("recovery-*.tar.gz"), key=lambda path: path.name)
    return bundles[-1] if bundles else None


def status(root: pathlib.Path = pathlib.Path("/")) -> str:
    candidate_store = _host(root, RECOVERY_DIR)
    if not candidate_store.exists() and not candidate_store.is_symlink():
        return "Recovery bundle: NOT_PREPARED"
    try:
        store = _recovery_store(root)
    except RecoveryError as exc:
        return f"Recovery bundle: CORRUPT ({exc})"
    bundle = latest_bundle(root)
    if bundle is None:
        if any(store.glob("recovery-*.tar.gz")):
            return "Recovery bundle: CORRUPT (no protected recovery archive passed integrity validation)"
        return "Recovery bundle: NOT_PREPARED"
    try:
        manifest, _ = _verify_bundle(bundle, root)
    except RecoveryError as exc:
        return f"Recovery bundle: CORRUPT ({exc})"
    return (f"Recovery bundle: READY ({bundle.name}); Niri+ {manifest['niri_plus_version']}; "
            f"asahi-system {manifest.get('asahi_system_commit') or 'not-installed'}; "
            f"Quickshell {manifest.get('quickshell_commit') or 'not-installed'}; "
            f"packages inventoried ({len(manifest.get('rpm_payloads', []))} cached RPM payloads protected)")


def _write_staged_member(archive: tarfile.TarFile, member: tarfile.TarInfo, stage: pathlib.Path) -> pathlib.Path:
    target = stage.joinpath(*pathlib.PurePosixPath(member.name).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    if member.isdir():
        target.mkdir(exist_ok=True)
    elif member.issym():
        target.symlink_to(member.linkname)
    elif member.isfile():
        stream = archive.extractfile(member)
        if stream is None:
            raise RecoveryError(f"cannot stage recovery member: {member.name}")
        with target.open("xb") as output:
            shutil.copyfileobj(stream, output)
        target.chmod(member.mode & 0o777)
    else:
        raise RecoveryError(f"unsupported recovery member: {member.name}")
    return target


class RecoveryTransactionError(RuntimeError):
    pass


class _RecoveryFilesystemTransaction:
    """Minimal stdlib-only transaction so emergency restore has no CLI dependency."""

    def __init__(self, paths: list[pathlib.Path], *, root: pathlib.Path):
        self.root = root
        self.paths = paths
        self.temp: tempfile.TemporaryDirectory[str] | None = None
        self.snapshot: pathlib.Path | None = None
        self.existed: dict[str, bool] = {}
        self.committed = False

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="niri-plus-recovery-transaction-")
        self.snapshot = pathlib.Path(self.temp.name)
        for index, absolute in enumerate(self.paths):
            target = _host(self.root, absolute)
            _safe_root(absolute, self.root)
            saved = self.snapshot / str(index)
            exists = target.exists() or target.is_symlink()
            self.existed[absolute.as_posix()] = exists
            if exists:
                if target.is_symlink():
                    saved.symlink_to(os.readlink(target), target_is_directory=target.is_dir())
                elif target.is_dir():
                    shutil.copytree(target, saved, symlinks=True, copy_function=shutil.copy2)
                else:
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, saved, follow_symlinks=False)
        return self

    def commit(self):
        self.committed = True

    def rollback(self):
        if self.snapshot is None:
            return
        failures = []
        for index, absolute in reversed(list(enumerate(self.paths))):
            target = _host(self.root, absolute)
            saved = self.snapshot / str(index)
            try:
                _safe_root(absolute, self.root)
                if target.is_symlink() or (target.exists() and not target.is_dir()):
                    target.unlink()
                elif target.exists():
                    shutil.rmtree(target)
                if self.existed[absolute.as_posix()]:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if saved.is_symlink():
                        target.symlink_to(os.readlink(saved), target_is_directory=saved.is_dir())
                    elif saved.is_dir():
                        shutil.copytree(saved, target, symlinks=True, copy_function=shutil.copy2)
                    else:
                        shutil.copy2(saved, target, follow_symlinks=False)
            except OSError as exc:
                failures.append(f"{target}: {exc}")
        if failures:
            raise RecoveryTransactionError("could not restore prior files: " + "; ".join(failures))

    def __exit__(self, exc_type, exc, tb):
        rollback_error = None
        if exc_type is not None or not self.committed:
            try:
                self.rollback()
            except RecoveryTransactionError as error:
                rollback_error = error
        if self.temp is not None:
            self.temp.cleanup()
            self.temp = None
            self.snapshot = None
        if rollback_error is not None:
            if exc is not None:
                raise rollback_error from exc
            raise rollback_error
        return False


def restore(bundle: pathlib.Path | None = None, root: pathlib.Path = pathlib.Path("/"), *,
            runner=subprocess.run, restore_packages: bool | None = None,
            operation_locked: bool = False,
            extra_remove_packages: list[str] | None = None) -> str:
    if not operation_locked:
        with _operation_lock(root):
            return restore(bundle, root, runner=runner, restore_packages=restore_packages,
                           operation_locked=True, extra_remove_packages=extra_remove_packages)
    if root == pathlib.Path("/") and os.geteuid() != 0:
        raise RecoveryError("offline recovery requires root; start from Plasma/TTY and use sudo")
    bundle = bundle or latest_bundle(root)
    if bundle is None:
        raise RecoveryError("no local Niri+ recovery bundle is available")
    store = _recovery_store(root)
    manifest, _records = _verify_bundle(bundle, root)
    store = _host(root, RECOVERY_DIR)
    stage = pathlib.Path(tempfile.mkdtemp(prefix=".restore-stage-", dir=store))
    preserved = store / ("preserved-edits-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{os.getpid()}")
    try:
        with tarfile.open(bundle, "r:gz") as archive:
            members = [member for member in archive.getmembers() if member.name != MANIFEST_NAME]
            for member in sorted(members, key=lambda item: (item.name.count("/"), not item.isdir(), item.name)):
                _write_staged_member(archive, member, stage)
        previous_packages = {item["name"]: item["nevra"]
                             for item in manifest.get("rpm_inventory", {}).get("packages", [])}
        current_inventory = _package_inventory(runner)
        current_packages = {item["name"]: item["nevra"] for item in current_inventory.get("packages", [])}
        previous_qs = previous_packages.get(QUICKSHELL_PACKAGE)
        current_qs = current_packages.get(QUICKSHELL_PACKAGE)
        rpm_to_restore = None
        should_restore_packages = root == pathlib.Path("/") if restore_packages is None else restore_packages
        packages_to_remove: list[str] = []
        package_cleanup_deferred: list[str] = []
        if should_restore_packages:
            current_state = _read_managed_json(
                root, pathlib.Path("/var/lib/asahi-system/niri-performance/state.json"),
                "current Niri+ package ownership state", {},
            )
            current_owned = current_state.get("packages_installed_by_us", [])
            if (not isinstance(current_owned, list)
                    or any(not isinstance(name, str) or name not in (*SYSTEM_PACKAGES, QUICKSHELL_PACKAGE)
                           for name in current_owned)):
                raise RecoveryError("current Niri+ package ownership state is invalid")
            if (extra_remove_packages is not None
                    and (not isinstance(extra_remove_packages, list)
                         or any(not isinstance(name, str) or name not in SYSTEM_PACKAGES
                                for name in extra_remove_packages))):
                raise RecoveryError("failed-install package compensation list is invalid")
            newly_owned = set(current_owned) - set(manifest.get("package_ownership", []))
            # Preserve every pre-existing/shared RPM. A Quickshell engine is
            # eligible for compensation only if this Niri+ transaction newly
            # claimed it and the recovery inventory proves it was absent.
            packages_to_remove = sorted(
                ((newly_owned | set(extra_remove_packages or []))
                 & set((*SYSTEM_PACKAGES, QUICKSHELL_PACKAGE)) & set(current_packages))
                - set(previous_packages)
            )
            if packages_to_remove:
                try:
                    removal_check = runner(
                        ["dnf", "remove", "--cacheonly", "--noautoremove", "--assumeno", *packages_to_remove],
                        check=False, capture_output=True, text=True, timeout=60,
                    )
                except (OSError, subprocess.SubprocessError):
                    removal_check = None
                if removal_check is None or removal_check.returncode != 0:
                    # Removing newly installed base RPMs is cleanup, not a
                    # prerequisite for restoring the managed Niri+ files. If
                    # the offline DNF cache cannot prove a safe transaction,
                    # preserve those packages and continue the file recovery.
                    package_cleanup_deferred = packages_to_remove
                    packages_to_remove = []
        if should_restore_packages and previous_qs and current_qs != previous_qs:
            payload = next((item for item in manifest.get("rpm_payloads", [])
                            if item.get("name") == QUICKSHELL_PACKAGE and item.get("nevra") == previous_qs), None)
            if payload is None:
                raise RecoveryError("installed Quickshell RPM differs from the recovery point and no offline RPM payload is available")
            rpm_to_restore = stage.joinpath(*pathlib.PurePosixPath(payload["member"]).parts)
            signature = runner(["rpmkeys", "--checksig", "--verbose", str(rpm_to_restore)],
                               check=False, capture_output=True, text=True, timeout=15)
            if not _rpm_signature_is_trusted(signature):
                raise RecoveryError("cached Quickshell RPM signature validation failed")
            dry_rpm = runner(["rpm", "--test", "-Uvh", "--oldpackage", "--replacepkgs", str(rpm_to_restore)],
                             check=False, capture_output=True, text=True, timeout=30)
            if dry_rpm.returncode != 0:
                raise RecoveryError("offline Quickshell RPM restore preflight failed")
        # Preserve current managed Niri config before restoring a previous
        # known-good copy so post-install edits are recoverable after repair.
        config_paths = [
            pathlib.Path("/etc/niri/config.kdl"), pathlib.Path("/etc/niri/keybinds.kdl"),
            pathlib.Path("/etc/niri/outputs.kdl"), pathlib.Path("/etc/niri/rules.kdl"),
            pathlib.Path("/etc/niri/autostart.kdl"),
        ]
        current_configs = [(path, _host(root, path)) for path in config_paths]
        existing = [(path, current) for path, current in current_configs if current.is_file() and not current.is_symlink()]
        if existing:
            preserved.mkdir(mode=0o700)
            for path, current in existing:
                destination = preserved.joinpath(*path.parts[1:])
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(current, destination)
            (preserved / "README.txt").write_text(
                "Niri configuration present immediately before offline recovery. Review before copying back.\n",
                encoding="utf-8",
            )
            os.chmod(preserved / "README.txt", 0o600)
            if root == pathlib.Path("/"):
                for directory, _subdirs, _files in os.walk(preserved):
                    os.chown(directory, 0, 0)
                for path, _current in existing:
                    destination = preserved.joinpath(*path.parts[1:])
                    os.chown(destination, 0, 0)

        roots = [pathlib.Path("/") / item for item in manifest["roots"]]
        transaction = _RecoveryFilesystemTransaction(roots, root=root)
        with transaction:
            for path in roots:
                _safe_root(path, root)
                target = _host(root, path)
                if target.is_symlink() or target.is_file():
                    target.unlink()
                elif target.is_dir():
                    shutil.rmtree(target)
            with tarfile.open(bundle, "r:gz") as archive:
                staged_members = [member for member in archive.getmembers()
                                  if member.name != MANIFEST_NAME and not member.name.startswith("recovery-rpms/")]
            directories = [member for member in staged_members if member.isdir()]
            for member in sorted(directories, key=lambda item: (item.name.count("/"), item.name)):
                source = stage.joinpath(*pathlib.PurePosixPath(member.name).parts)
                destination = _host(root, pathlib.Path("/") / member.name)
                destination.mkdir(parents=True, exist_ok=True)
            for member in staged_members:
                if member.isdir():
                    continue
                source = stage.joinpath(*pathlib.PurePosixPath(member.name).parts)
                destination = _host(root, pathlib.Path("/") / member.name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if member.issym():
                    destination.symlink_to(member.linkname)
                else:
                    shutil.copyfile(source, destination, follow_symlinks=False)
                    destination.chmod(member.mode & 0o777)
                if root == pathlib.Path("/"):
                    try:
                        os.chown(destination, member.uid, member.gid, follow_symlinks=not member.issym())
                    except PermissionError as exc:
                        raise RecoveryError(f"cannot restore recorded ownership for {destination}") from exc
            for member in sorted(directories, key=lambda item: (-item.name.count("/"), item.name)):
                destination = _host(root, pathlib.Path("/") / member.name)
                if root == pathlib.Path("/"):
                    os.chown(destination, member.uid, member.gid)
                destination.chmod(member.mode & 0o777)
            if rpm_to_restore is not None:
                runner(["rpm", "-Uvh", "--oldpackage", "--replacepkgs", str(rpm_to_restore)],
                       check=True, capture_output=True, text=True, timeout=60)
            if packages_to_remove:
                try:
                    removal = runner(
                        ["dnf", "remove", "--cacheonly", "--noautoremove", "-y", *packages_to_remove],
                        check=False, capture_output=True, text=True, timeout=90,
                    )
                    if removal.returncode != 0:
                        package_cleanup_deferred = packages_to_remove
                        packages_to_remove = []
                except (OSError, subprocess.SubprocessError):
                    package_cleanup_deferred = packages_to_remove
                    packages_to_remove = []
            transaction.commit()
        return (f"Restored Niri+ {manifest['niri_plus_version']} and its managed files offline. "
                + ("The recorded Quickshell RPM was also restored offline. " if rpm_to_restore else
                   "RPM packages were inventoried; no RPM rollback was needed. ")
                + (f"New Niri+-owned packages removed without autoremove: {', '.join(packages_to_remove)}. "
                   if packages_to_remove else "")
                + (f"Warning: managed files were restored, but offline RPM cleanup was deferred; "
                   f"packages remain installed: {', '.join(package_cleanup_deferred)}. "
                   if package_cleanup_deferred else "")
                + (f"Pre-recovery Niri configs saved at {preserved}." if existing else ""))
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="niri-plus-recover", description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("prepare", help="write a protected recovery package for current managed files")
    actions.add_parser("status", help="verify the latest offline recovery package read-only")
    restore_parser = actions.add_parser("restore", help="restore the latest verified package offline")
    restore_parser.add_argument("--bundle", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            print(f"Prepared protected offline recovery bundle: {prepare_bundle()}")
        elif args.action == "status":
            print(status())
        else:
            print(restore(args.bundle))
    except (RecoveryError, OSError, RecoveryTransactionError) as exc:
        print(f"Offline recovery {args.action} failed: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
