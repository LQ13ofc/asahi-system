"""Root-side installer executed only from the closed, verified Git snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import runpy
import sys

from . import install_transaction, source_update


def _managed_paths(install_module) -> list[pathlib.Path]:
    return install_transaction.transaction_paths(
        install_module.MANAGED_FILES.keys(), install_module.MANAGED_LINKS.keys(),
    )


def _verify_committed_install(snapshot_root: pathlib.Path, manifest: dict, install_module) -> None:
    version = (snapshot_root / "VERSION").read_text(encoding="utf-8").strip()
    qs_commit = manifest["quickshell"]["commit"]
    bootstrap_path = pathlib.Path("/var/lib/niri-plus/bootstrap.json")
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    if (bootstrap.get("version") != version or bootstrap.get("source_commit") != manifest["system"]["commit"]
            or bootstrap.get("quickshell_expected_commit") != qs_commit):
        raise RuntimeError("CLI/assets/pin state do not describe the resolved snapshot")
    state = install_module.load_state(pathlib.Path("/"))
    if state.get("applied_version") != version or state.get("quickshell", {}).get("expected_commit") != qs_commit:
        raise RuntimeError("session state and CLI/Quickshell snapshot do not describe the same install")
    for absolute, source in install_module.MANAGED_FILES.items():
        target = pathlib.Path(absolute)
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            raise RuntimeError(f"managed file failed post-install verification: {absolute}")
    for absolute, link_target in install_module.MANAGED_LINKS.items():
        target = pathlib.Path(absolute)
        if not target.is_symlink() or os.readlink(target) != link_target:
            raise RuntimeError(f"managed symlink failed post-install verification: {absolute}")
    qml_root = pathlib.Path("/usr/local/share/niri-plus/quickshell")
    if not qml_root.is_dir() or not (qml_root / "shell.qml").is_file():
        raise RuntimeError("pinned Quickshell runtime files were not committed")
    for absolute, entry in state.get("entries", {}).items():
        if entry.get("kind") == "file":
            target = pathlib.Path(absolute)
            expected = entry.get("installed_sha256")
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"managed file checksum failed after install: {absolute}")


def run_install_transaction(paths: list[pathlib.Path], bootstrap, apply, verify, finalize=lambda: None,
                            *, root: pathlib.Path = pathlib.Path("/")) -> None:
    transaction = install_transaction.FilesystemTransaction(paths, root=root)
    with transaction:
        bootstrap()
        apply()
        verify()
        finalize()
        transaction.commit()


def apply_snapshot(snapshot_root: pathlib.Path, manifest_path: pathlib.Path) -> None:
    snapshot_root = snapshot_root.resolve(strict=True)
    manifest_path = manifest_path.resolve(strict=True)
    manifest = source_update.validate_snapshot(snapshot_root, manifest_path)
    os.environ["NIRI_PLUS_DATA_DIR"] = str(snapshot_root)
    # The bootstrap module is part of this exact read-only snapshot. It is never
    # opened from the user's source checkout while privileged.
    bootstrap_namespace = runpy.run_path(
        str(snapshot_root / "scripts/bootstrap-niri-plus"), run_name="niri_plus_snapshot_bootstrap",
    )
    from . import install as install_module

    try:
        run_install_transaction(
            _managed_paths(install_module),
            lambda: bootstrap_namespace["install_snapshot"](snapshot_root, manifest_path),
            lambda: install_module.apply_install(defer_packages=True),
            lambda: _verify_committed_install(snapshot_root, manifest, install_module),
            lambda: install_module.install_locked_packages(
                json.loads((snapshot_root / "integration/quickshell.lock.json").read_text(encoding="utf-8"))["engine"]
            ),
        )
    except Exception:
        # Session unit files may have been reloaded during apply. Re-read the
        # restored definitions without starting or stopping any user unit.
        try:
            install_module.reload_invoking_user_manager()
        except Exception:
            pass
        raise
    install_module.reload_invoking_user_manager()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-root", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise RuntimeError("snapshot install helper requires root")
        apply_snapshot(args.snapshot_root, args.manifest)
    except Exception as exc:
        print(f"Niri+ snapshot install failed: {exc}", file=sys.stderr)
        return 2
    print("Niri+ snapshot installation committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
