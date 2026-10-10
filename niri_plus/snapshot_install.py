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


def _failed_transaction_new_packages(install_module, *, include_quickshell: bool,
                                     preexisting_packages: set[str]) -> list[str]:
    candidates = set(install_module.PACKAGES)
    if include_quickshell:
        candidates.add(install_module.QUICKSHELL_PACKAGE)
    return sorted(candidates - preexisting_packages)


def _tree_signature(path: pathlib.Path) -> tuple:
    if path.is_symlink():
        return ("symlink", os.readlink(path))
    if path.is_file():
        return ("file", hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode & 0o777)
    if not path.is_dir():
        return ("absent",)
    children = []
    for child in sorted(path.iterdir(), key=lambda value: value.name):
        if child.name == "__pycache__" or child.suffix == ".pyc":
            continue
        children.append((child.name, _tree_signature(child)))
    return ("directory", tuple(children))


def _verify_committed_install(snapshot_root: pathlib.Path, manifest: dict, install_module,
                              root: pathlib.Path = pathlib.Path("/")) -> None:
    version = (snapshot_root / "VERSION").read_text(encoding="utf-8").strip()
    include_quickshell = manifest["quickshell"].get("included", True)
    bootstrap_path = pathlib.Path("/var/lib/niri-plus/bootstrap.json") if root == pathlib.Path("/") else root / "var/lib/niri-plus/bootstrap.json"
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    qs_commit = bootstrap.get("quickshell_expected_commit")
    channel = manifest.get("channel", "production")
    if (bootstrap.get("version") != version or bootstrap.get("source_commit") != manifest["system"]["commit"]
            or bootstrap.get("source_channel", "production") != channel
            or (include_quickshell and qs_commit != manifest["quickshell"]["commit"])):
        raise RuntimeError("CLI/assets/pin state do not describe the resolved snapshot")
    state = install_module.load_state(root)
    if state.get("applied_version") != version or state.get("quickshell", {}).get("expected_commit") != qs_commit:
        raise RuntimeError("session state and CLI/Quickshell snapshot do not describe the same install")
    for absolute, source in install_module.MANAGED_FILES.items():
        if not include_quickshell and absolute in install_module.QUICKSHELL_MANAGED_FILES:
            continue
        target = absolute if root == pathlib.Path("/") else root / absolute.as_posix().lstrip("/")
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            raise RuntimeError(f"managed file failed post-install verification: {absolute}")
    for absolute, link_target in install_module.MANAGED_LINKS.items():
        if not include_quickshell and absolute in install_module.QUICKSHELL_MANAGED_LINKS:
            continue
        target = absolute if root == pathlib.Path("/") else root / absolute.as_posix().lstrip("/")
        if not target.is_symlink() or os.readlink(target) != link_target:
            raise RuntimeError(f"managed symlink failed post-install verification: {absolute}")
    if include_quickshell:
        qml_root = pathlib.Path("/usr/local/share/niri-plus/quickshell") if root == pathlib.Path("/") else root / "usr/local/share/niri-plus/quickshell"
        if not qml_root.is_dir() or not (qml_root / "shell.qml").is_file():
            raise RuntimeError("pinned Quickshell runtime files were not committed")
        plugin_state_path = pathlib.Path("/var/lib/niri-plus/plugins.json") if root == pathlib.Path("/") else root / "var/lib/niri-plus/plugins.json"
        plugin_action = manifest.get("plugin_action", "none")
        if plugin_action != "none":
            from . import plugins
            plugin_manifest = plugins._validated_manifest(root)
            record = (plugin_manifest or {}).get("plugins", {}).get("quickshell")
            if not record or record.get("commit") != manifest["quickshell"]["commit"]:
                raise RuntimeError("Quickshell plugin manifest does not match the installed snapshot")
        elif plugin_state_path.exists():
            from . import plugins
            plugins.quickshell_state(root)
    for absolute, entry in state.get("entries", {}).items():
        if entry.get("kind") == "file":
            target = absolute if root == pathlib.Path("/") else root / absolute.as_posix().lstrip("/")
            expected = entry.get("installed_sha256")
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"managed file checksum failed after install: {absolute}")

    source_data = snapshot_root / "niri_plus"
    installed_data = pathlib.Path("/usr/local/lib/niri-plus/niri_plus") if root == pathlib.Path("/") else root / "usr/local/lib/niri-plus/niri_plus"
    if _tree_signature(source_data) != _tree_signature(installed_data):
        raise RuntimeError("installed Niri+ CLI modules differ from the verified snapshot")
    data_base = pathlib.Path("/usr/local/share/niri-plus") if root == pathlib.Path("/") else root / "usr/local/share/niri-plus"
    for name in ("niri", "sessions", "integration", "scripts/collect-performance-baseline",
                 "scripts/collect-m1-release-gate.sh"):
        source = snapshot_root / name
        installed = data_base / name
        if _tree_signature(source) != _tree_signature(installed):
            raise RuntimeError(f"installed Niri+ data differs from verified snapshot: {name}")
    recovery_helper = pathlib.Path("/usr/local/libexec/niri-plus-recover") if root == pathlib.Path("/") else root / "usr/local/libexec/niri-plus-recover"
    if recovery_helper.read_bytes() != (snapshot_root / "niri_plus/recovery_core.py").read_bytes():
        raise RuntimeError("standalone offline recovery helper differs from the verified snapshot")


def run_install_transaction(paths: list[pathlib.Path], bootstrap, apply, verify, finalize=lambda: None,
                            *, root: pathlib.Path = pathlib.Path("/")) -> None:
    transaction = install_transaction.FilesystemTransaction(paths, root=root)
    with transaction:
        bootstrap()
        apply()
        verify()
        finalize()
        transaction.commit()


def apply_snapshot(snapshot_root: pathlib.Path, manifest_path: pathlib.Path, *,
                   include_quickshell: bool = True, plugin_action: str = "none",
                   root: pathlib.Path = pathlib.Path("/")) -> None:
    with install_transaction.operation_lock(root):
        return _apply_snapshot_locked(
            snapshot_root, manifest_path, include_quickshell=include_quickshell,
            plugin_action=plugin_action, root=root,
        )


def _apply_snapshot_locked(snapshot_root: pathlib.Path, manifest_path: pathlib.Path, *,
                           include_quickshell: bool, plugin_action: str,
                           root: pathlib.Path) -> None:
    snapshot_root = snapshot_root.resolve(strict=True)
    manifest_path = manifest_path.resolve(strict=True)
    manifest = source_update.validate_snapshot(snapshot_root, manifest_path)
    if manifest["quickshell"].get("included", True) != include_quickshell:
        raise RuntimeError("install mode does not match the resolved snapshot")
    if plugin_action not in {"none", "install", "update", "migrate"}:
        raise RuntimeError("unsupported plugin transaction action")
    if plugin_action != "none" and not include_quickshell:
        raise RuntimeError("plugin registration requires a Quickshell snapshot")
    prior_plugin_state = "absent"
    if plugin_action != "none":
        from . import plugins
        prior_plugin_state, _ = plugins.quickshell_state(root)
    manifest["plugin_action"] = plugin_action
    os.environ["NIRI_PLUS_DATA_DIR"] = str(snapshot_root)
    # The bootstrap module is part of this exact read-only snapshot. It is never
    # opened from the user's source checkout while privileged.
    bootstrap_namespace = runpy.run_path(
        str(snapshot_root / "scripts/bootstrap-niri-plus"), run_name="niri_plus_snapshot_bootstrap",
    )
    from . import install as install_module

    already_current = False
    try:
        _verify_committed_install(snapshot_root, manifest, install_module, root)
        package_names = (*install_module.PACKAGES,
                         *((install_module.QUICKSHELL_PACKAGE,) if include_quickshell else ()))
        if root == pathlib.Path("/"):
            installed_packages = install_module.installed_rpm_packages_for(package_names)
            already_current = set(package_names) <= installed_packages
        if already_current and include_quickshell:
            from . import quickshell
            rpm_state, rpm_version = quickshell._rpm_version(root, install_module.subprocess.run)
            engine = json.loads((snapshot_root / "integration/quickshell.lock.json").read_text(encoding="utf-8"))["engine"]
            expected = engine["nevra"].removeprefix("quickshell-") + ".aarch64"
            already_current = rpm_state == "OK" and rpm_version == expected
    except Exception:
        already_current = False
    if already_current:
        print("Niri+ and its selected plugins already match this verified snapshot; no files or packages were replaced.")
        return

    # The recovery point is deliberately outside the host transaction so an
    # interrupted or failed apply never removes it.
    from . import recovery
    recovery_bundle = recovery.prepare_bundle(root, operation_locked=True)
    package_engine = (json.loads((snapshot_root / "integration/quickshell.lock.json").read_text(encoding="utf-8"))["engine"]
                      if include_quickshell else None)
    install_state = install_module.load_state(root)
    allow_quickshell_update = install_module.QUICKSHELL_PACKAGE in install_state.get("packages_installed_by_us", [])
    package_plan = []
    preexisting_packages = set()
    if root == pathlib.Path("/"):
        package_names = (*install_module.PACKAGES,
                         *((install_module.QUICKSHELL_PACKAGE,) if include_quickshell else ()))
        preexisting_packages = install_module.installed_rpm_packages_for(package_names)
        package_plan = install_module.validate_package_plan(
            package_engine, allow_quickshell_update=allow_quickshell_update,
            recovery_bundle=recovery_bundle,
        )

    package_mutation_started = False

    def finalize_packages() -> None:
        nonlocal package_mutation_started
        if root != pathlib.Path("/"):
            return
        if package_plan:
            package_mutation_started = True
            install_module.install_locked_packages(
                package_engine, allow_quickshell_update=allow_quickshell_update,
                recovery_bundle=recovery_bundle,
            )
        remaining = install_module.validate_package_plan(
            package_engine, allow_quickshell_update=True, recovery_bundle=recovery_bundle,
        )
        if remaining:
            raise RuntimeError("post-install RPM verification still requires changes")

    try:
        run_install_transaction(
            _managed_paths(install_module),
            lambda: bootstrap_namespace["install_snapshot"](snapshot_root, manifest_path),
            lambda: _apply_with_plugin_state(
                install_module, snapshot_root, manifest, include_quickshell, plugin_action,
                prior_plugin_state, root, recovery_bundle,
            ),
            lambda: _verify_committed_install(snapshot_root, manifest, install_module, root),
            finalize_packages,
            root=root,
        )
    except BaseException as install_error:
        # Session unit files may have been reloaded during apply. Re-read the
        # restored definitions without starting or stopping any user unit.
        try:
            install_module.reload_invoking_user_manager()
        except Exception:
            pass
        if package_mutation_started:
            from . import recovery
            try:
                recovery.restore(recovery_bundle, root=root, restore_packages=(root == pathlib.Path("/")),
                                 operation_locked=True,
                                 extra_remove_packages=_failed_transaction_new_packages(
                                     install_module, include_quickshell=include_quickshell,
                                     preexisting_packages=preexisting_packages,
                                 ))
            except BaseException as recovery_error:
                raise RuntimeError(
                    f"install failed after package mutation ({install_error}); offline recovery also failed "
                    f"({recovery_error}); bundle retained at {recovery_bundle}"
                ) from install_error
        raise
    install_module.reload_invoking_user_manager()


def _apply_with_plugin_state(install_module, snapshot_root: pathlib.Path, manifest: dict,
                             include_quickshell: bool, plugin_action: str,
                             prior_plugin_state: str, root: pathlib.Path,
                             recovery_bundle: pathlib.Path) -> None:
    install_module.apply_install(root=root, defer_packages=True, include_quickshell=include_quickshell,
                                 recovery_bundle=recovery_bundle)
    if plugin_action != "none":
        from . import plugins
        version = (snapshot_root / "VERSION").read_text(encoding="utf-8").strip()
        lock = json.loads((snapshot_root / "integration/quickshell.lock.json").read_text(encoding="utf-8"))
        plugins.write_snapshot_manifest(
            root, plugin_action, manifest["quickshell"]["commit"],
            lock["engine"]["nevra"], version, prior_state=prior_plugin_state,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-root", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--without-quickshell", action="store_true")
    parser.add_argument("--plugin-action", choices=("install", "update", "migrate"), default="none")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise RuntimeError("snapshot install helper requires root")
        apply_snapshot(
            args.snapshot_root, args.manifest, include_quickshell=not args.without_quickshell,
            plugin_action=args.plugin_action, root=pathlib.Path("/"),
        )
    except Exception as exc:
        print(f"Niri+ snapshot install failed: {exc}", file=sys.stderr)
        return 2
    print("Niri+ snapshot installation committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
