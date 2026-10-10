from __future__ import annotations

import hashlib
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import textwrap
from unittest import mock
from contextlib import redirect_stdout

from niri_plus import install, install_command, install_transaction, plugin_command, plugins, preflight, quickshell, recovery, recovery_core, snapshot_install, source_update


ROOT = pathlib.Path(__file__).resolve().parents[1]
QS_REPOSITORY = "https://github.com/LQ13ofc/quickshell-.git"


def write_json(path: pathlib.Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def make_plugin_host(root: pathlib.Path, *, manifest: bool = True) -> str:
    lock = json.loads((ROOT / "integration/quickshell.lock.json").read_text(encoding="utf-8"))
    commit = lock["commit"]
    data = root / "usr/local/share/niri-plus"
    data.mkdir(parents=True, exist_ok=True)
    (data / "VERSION").write_text("0.1.9\n", encoding="utf-8")
    (data / "integration").mkdir(exist_ok=True)
    (data / "integration/quickshell.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    runtime = data / "quickshell"
    runtime.mkdir(exist_ok=True)
    shell = b"import QtQuick\nItem {}\n"
    (runtime / "shell.qml").write_bytes(shell)
    write_json(data / "quickshell.snapshot.json", {
        "schema_version": 1,
        "repository": QS_REPOSITORY,
        "commit": commit,
        "files": [{
            "path": "shell.qml", "mode": 0o100644,
            "oid": source_update._git_hash(b"blob", shell),
        }],
    })
    install.install_files(root, include_quickshell=True)
    state = install.load_state(root)
    state["applied_version"] = "0.1.9"
    state.setdefault("packages_installed_by_us", []).append("quickshell")
    install.update_quickshell_state(state, commit, included=True)
    install.write_state(root, state)
    bootstrap = root / "var/lib/niri-plus/bootstrap.json"
    write_json(bootstrap, {
        "schema_version": 2,
        "version": "0.1.9",
        "managed": ["/usr/local/lib/niri-plus", "/usr/local/share/niri-plus", "/usr/local/bin/niri+"],
        "source_commit": "a" * 40,
        "source_channel": "production",
        "quickshell_repository": QS_REPOSITORY,
        "quickshell_expected_commit": commit,
        "quickshell_source": "/usr/local/share/niri-plus/quickshell",
    })
    if manifest:
        write_json(root / plugins.PLUGIN_STATE.relative_to("/"), {
            "schema_version": 1,
            "plugins": {"quickshell": {
                "repository": QS_REPOSITORY,
                "commit": commit,
                "engine_nevra": lock["engine"]["nevra"],
                "niri_plus_version": "0.1.9",
            }},
        })
    (root / "usr/local/bin/niri+").parent.mkdir(parents=True, exist_ok=True)
    (root / "usr/local/bin/niri+").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return commit


class PluginRegistryTests(unittest.TestCase):
    def test_main_install_resolves_quickshell_only_for_a_registered_plugin(self):
        class SnapshotContext:
            def __init__(self, included: bool):
                self.snapshot = type("Snapshot", (), {
                    "include_quickshell": included,
                    "system_commit": "a" * 40,
                    "quickshell_commit": "b" * 40 if included else None,
                    "root": pathlib.Path("/tmp/snapshot"),
                })()

            def __enter__(self):
                return self.snapshot

            def __exit__(self, *_args):
                return False

        scenarios = (("absent", "none", False), ("installed", "update", True))
        for state, action, include in scenarios:
            with self.subTest(state=state), \
                 mock.patch.object(install_command.plugins, "quickshell_state", return_value=(state, {})), \
                 mock.patch.object(install_command.install, "target_mismatches", return_value=[]), \
                 mock.patch.object(install_command.install, "parse_os_release", return_value={"ID": "fedora-asahi-remix", "VERSION_ID": "44"}), \
                 mock.patch.object(install_command.os, "geteuid", return_value=0), \
                 mock.patch.object(install_command.source_update, "discover_source_root", return_value=pathlib.Path("/tmp/source")), \
                 mock.patch.object(install_command.source_update, "resolved_snapshot", return_value=SnapshotContext(include)) as resolve, \
                 mock.patch.object(install_command, "_refuse_downgrade"), \
                 mock.patch.object(install_command.source_update, "install_from_snapshot") as apply:
                self.assertEqual(install_command.run_install(), 0)
            self.assertEqual(resolve.call_args.kwargs["include_quickshell"], include)
            self.assertEqual(apply.call_args.kwargs["plugin_action"], action)

    def test_joint_update_refuses_running_or_unobservable_quickshell_before_resolution(self):
        for process_state in (
            {"status": quickshell.WARNING, "count": 1},
            {"status": quickshell.UNAVAILABLE, "count": None},
        ):
            with self.subTest(process_state=process_state), \
                 mock.patch.object(install_command.plugins, "quickshell_state", return_value=("installed", {})), \
                 mock.patch.object(install_command.install, "target_mismatches", return_value=[]), \
                 mock.patch.object(install_command.install, "parse_os_release", return_value={
                     "ID": "fedora-asahi-remix", "VERSION_ID": "44",
                 }), \
                 mock.patch.object(install_command.os, "geteuid", return_value=0), \
                 mock.patch.object(install_command.quickshell, "process_diagnostics", return_value=process_state), \
                 mock.patch.object(install_command.source_update, "discover_source_root") as discover, \
                 mock.patch.object(install_command.source_update, "resolved_snapshot") as resolve:
                self.assertEqual(install_command.run_install(), 2)
            discover.assert_not_called()
            resolve.assert_not_called()

    def test_network_or_auth_failure_never_reaches_host_apply(self):
        with mock.patch.object(install_command.plugins, "quickshell_state", return_value=("installed", {})), \
             mock.patch.object(install_command.install, "target_mismatches", return_value=[]), \
             mock.patch.object(install_command.os, "geteuid", return_value=0), \
             mock.patch.object(install_command.source_update, "discover_source_root", return_value=pathlib.Path("/tmp/source")), \
             mock.patch.object(install_command.source_update, "resolved_snapshot",
                              side_effect=source_update.SourceUpdateError("non-interactive authentication failed")), \
             mock.patch.object(install_command.source_update, "install_from_snapshot") as apply:
            self.assertEqual(install_command.run_install(), 2)
            apply.assert_not_called()

    def test_dry_run_prints_both_verified_commits_without_applying(self):
        qs_commit = json.loads((ROOT / "integration/quickshell.lock.json").read_text())["commit"]
        system_commit = "a" * 40
        snapshot_root = ROOT
        snapshot = type("Snapshot", (), {
            "include_quickshell": True, "system_commit": system_commit,
            "quickshell_commit": qs_commit, "root": snapshot_root,
        })()

        class SnapshotContext:
            def __enter__(self):
                return snapshot

            def __exit__(self, *_args):
                return False

        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             mock.patch.object(install_command.plugins, "quickshell_state", return_value=("installed", {})), \
             mock.patch.object(install_command.install, "target_mismatches", return_value=[]), \
             mock.patch.object(install_command.install, "parse_os_release", return_value={"ID": "fedora-asahi-remix", "VERSION_ID": "44"}), \
             mock.patch.object(install_command.source_update, "discover_source_root", return_value=pathlib.Path("/tmp/source")), \
             mock.patch.object(install_command.source_update, "resolved_snapshot", return_value=SnapshotContext()), \
             mock.patch.object(install_command, "_refuse_downgrade"), \
             mock.patch.object(install_command.source_update, "install_from_snapshot") as apply:
            self.assertEqual(install_command.run_install(dry_run=True), 0)
        self.assertIn(system_commit, output.getvalue())
        self.assertIn(qs_commit, output.getvalue())
        self.assertIn("no host files", output.getvalue())
        apply.assert_not_called()

    def test_normal_install_refuses_a_production_source_downgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "VERSION").write_text("0.1.8\n", encoding="utf-8")
            target = root / "usr/local/share/niri-plus/VERSION"
            target.parent.mkdir(parents=True)
            target.write_text("0.1.9\n", encoding="utf-8")
            with self.assertRaisesRegex(source_update.SourceUpdateError, "refusing downgrade"):
                install_command._refuse_downgrade(root, root)

    def test_equal_version_from_a_different_installed_commit_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "usr/local/share/niri-plus").mkdir(parents=True)
            (root / "usr/local/share/niri-plus/VERSION").write_text("0.1.12\n", encoding="utf-8")
            write_json(root / "var/lib/niri-plus/bootstrap.json", {
                "source_commit": "b" * 40, "version": "0.1.12",
            })
            (root / "snapshot").mkdir()
            (root / "snapshot/VERSION").write_text("0.1.12\n", encoding="utf-8")
            with self.assertRaisesRegex(source_update.SourceUpdateError, "different source commit"):
                install_command._refuse_downgrade(root / "snapshot", root, "a" * 40)

    def test_same_version_and_same_commit_remains_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "usr/local/share/niri-plus").mkdir(parents=True)
            (root / "usr/local/share/niri-plus/VERSION").write_text("0.1.12\n", encoding="utf-8")
            write_json(root / "var/lib/niri-plus/bootstrap.json", {
                "source_commit": "a" * 40, "version": "0.1.12",
            })
            (root / "snapshot").mkdir()
            (root / "snapshot/VERSION").write_text("0.1.12\n", encoding="utf-8")
            install_command._refuse_downgrade(root / "snapshot", root, "a" * 40)
    def test_presence_of_standalone_qs_does_not_imply_managed_plugin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            binary = root / "usr/bin/qs"
            binary.parent.mkdir(parents=True)
            binary.write_text("standalone", encoding="utf-8")
            state, record = plugins.quickshell_state(root)
            self.assertEqual(state, "absent")
            self.assertIsNone(record)
            self.assertIn("NOT_INSTALLED", plugins.render_list(root))

    def test_explicit_plugin_install_requires_installed_niri_plus_core(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(plugin_command.run_install(
                "quickshell", root=pathlib.Path(temp), require_root=False,
            ), 2)

    def test_manifest_rejects_unknown_plugin_and_invalid_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "var/lib/niri-plus/plugins.json"
            write_json(path, {"schema_version": 1, "plugins": {"arbitrary": {}}})
            with self.assertRaises(plugins.PluginError):
                plugins.quickshell_state(pathlib.Path(temp))

            write_json(path, {"schema_version": 1, "plugins": {"quickshell": {
                "repository": QS_REPOSITORY, "commit": "not-a-commit", "engine_nevra": "x",
            }}})
            with self.assertRaises(plugins.PluginError):
                plugins.quickshell_state(pathlib.Path(temp))

    def test_manifest_detects_bootstrap_or_payload_pin_divergence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            make_plugin_host(root)
            bootstrap_path = root / "var/lib/niri-plus/bootstrap.json"
            bootstrap = json.loads(bootstrap_path.read_text())
            bootstrap["quickshell_expected_commit"] = "0" * 40
            write_json(bootstrap_path, bootstrap)
            state, _record = plugins.quickshell_state(root)
            self.assertEqual(state, "installed-inconsistent")

    def test_plugin_state_rejects_lockfile_commit_divergence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            make_plugin_host(root)
            lock_path = root / "usr/local/share/niri-plus/integration/quickshell.lock.json"
            lock = json.loads(lock_path.read_text())
            lock["commit"] = "0" * 40
            write_json(lock_path, lock)
            state, _record = plugins.quickshell_state(root)
            self.assertEqual(state, "installed-inconsistent")

    def test_plugin_status_displays_manifest_commit_and_lifecycle_without_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            commit = make_plugin_host(root)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(plugin_command.run_status("quickshell", root), 0)
            rendered = output.getvalue()
            self.assertIn("quickshell: INSTALLED", rendered)
            self.assertIn(f"expected_commit: {commit}", rendered)
            self.assertIn(f"installed_commit: {commit}", rendered)
            self.assertIn("lifecycle: UNAVAILABLE", rendered)

    def test_legacy_019_migration_requires_verified_runtime_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            commit = make_plugin_host(root, manifest=False)
            state, record = plugins.quickshell_state(root)
            self.assertEqual(state, "legacy")
            self.assertEqual(record["commit"], commit)
            (root / "usr/lib/systemd/user/asahi-quickshell.service").unlink()
            with self.assertRaises(plugins.PluginError):
                plugins.quickshell_state(root)

    def test_plugin_removal_keeps_core_configuration_and_standalone_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            commit = make_plugin_host(root)
            core_config = root / "etc/niri/config.kdl"
            original_core = core_config.read_bytes()
            qs_binary = root / "usr/bin/qs"
            qs_binary.parent.mkdir(parents=True, exist_ok=True)
            qs_binary.write_text("shared RPM executable", encoding="utf-8")

            with mock.patch.object(recovery, "prepare_bundle", wraps=recovery.prepare_bundle) as prepare:
                self.assertEqual(plugin_command.run_remove(
                    "quickshell", root=root, require_root=False, running_processes=0,
                ), 0)
            prepare.assert_called_once_with(root, operation_locked=True)
            bundle = recovery.latest_bundle(root)
            self.assertIsNotNone(bundle)
            self.assertEqual(core_config.read_bytes(), original_core)
            self.assertFalse((root / "usr/local/share/niri-plus/quickshell").exists())
            self.assertTrue(qs_binary.is_file())
            state = install.load_state(root)
            self.assertIsNone(state["quickshell"]["expected_commit"])
            self.assertNotIn("quickshell", state["packages_installed_by_us"])
            self.assertEqual(plugins.quickshell_state(root)[0], "absent")
            self.assertEqual(state["applied_version"], "0.1.9")
            recovery.restore(bundle, root)
            self.assertEqual(plugins.quickshell_state(root)[0], "installed")
            self.assertEqual((root / "usr/local/share/niri-plus/quickshell/shell.qml").read_text(),
                             "import QtQuick\nItem {}\n")

    def test_plugin_removal_aborts_when_offline_recovery_cannot_be_prepared(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            commit = make_plugin_host(root)
            runtime = root / "usr/local/share/niri-plus/quickshell"
            shell_before = (runtime / "shell.qml").read_bytes()
            with mock.patch.object(recovery, "prepare_bundle",
                                   side_effect=recovery.RecoveryError("recovery storage unavailable")):
                self.assertEqual(plugin_command.run_remove(
                    "quickshell", root=root, require_root=False, running_processes=0,
                ), 2)
            self.assertEqual(plugins.quickshell_state(root)[0], "installed")
            self.assertEqual((runtime / "shell.qml").read_bytes(), shell_before)
            self.assertEqual(plugins.quickshell_state(root)[1]["commit"], commit)

    def test_plugin_remove_refuses_while_qs_is_running(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            make_plugin_host(root)
            self.assertEqual(plugin_command.run_remove(
                "quickshell", root=root, require_root=False, running_processes=1,
            ), 2)
            self.assertTrue((root / "usr/local/share/niri-plus/quickshell/shell.qml").is_file())

    def test_locked_installer_does_not_update_preexisting_packages_unnecessarily(self):
        lock = json.loads((ROOT / "integration/quickshell.lock.json").read_text())
        all_installed = set((*install.PACKAGES, install.QUICKSHELL_PACKAGE))
        with mock.patch.object(install.shutil, "which", return_value="/usr/bin/dnf"), \
             mock.patch.object(install, "installed_rpm_packages_for", return_value=all_installed), \
             mock.patch("niri_plus.quickshell._rpm_version", return_value=("OK", "0:0.3.1-2.fc44.aarch64")):
            self.assertEqual(install.validate_package_plan(lock["engine"]), [])

    def test_independent_quickshell_rpm_is_not_silently_replaced(self):
        lock = json.loads((ROOT / "integration/quickshell.lock.json").read_text())
        all_installed = set((*install.PACKAGES, install.QUICKSHELL_PACKAGE))
        with mock.patch.object(install.shutil, "which", return_value="/usr/bin/dnf"), \
             mock.patch.object(install, "installed_rpm_packages_for", return_value=all_installed), \
             mock.patch("niri_plus.quickshell._rpm_version", return_value=("OK", "0:0.2.0-1.fc44.aarch64")):
            with self.assertRaisesRegex(install.InstallError, "independently installed"):
                install.validate_package_plan(lock["engine"])

    def test_managed_engine_update_requires_offline_rpm_and_uses_pinned_nevra(self):
        lock = json.loads((ROOT / "integration/quickshell.lock.json").read_text())
        all_installed = set((*install.PACKAGES, install.QUICKSHELL_PACKAGE))
        cached_bundle = pathlib.Path("/var/lib/niri-plus/recovery/example.tar.gz")
        with mock.patch.object(install.shutil, "which", return_value="/usr/bin/dnf"), \
             mock.patch.object(install, "installed_rpm_packages_for", return_value=all_installed), \
             mock.patch("niri_plus.quickshell._rpm_version", return_value=("OK", "0:0.2.0-1.fc44.aarch64")), \
             mock.patch("niri_plus.recovery.has_rpm_payload", return_value=True):
            command = install.validate_package_plan(
                lock["engine"], allow_quickshell_update=True, recovery_bundle=cached_bundle,
            )
        self.assertIn(lock["engine"]["nevra"], command)
        self.assertIn("--repofrompath=niri-plus-quickshell," + lock["engine"]["repository"], command)

    def test_managed_engine_update_stops_before_changes_without_offline_rpm(self):
        lock = json.loads((ROOT / "integration/quickshell.lock.json").read_text())
        all_installed = set((*install.PACKAGES, install.QUICKSHELL_PACKAGE))
        with mock.patch.object(install.shutil, "which", return_value="/usr/bin/dnf"), \
             mock.patch.object(install, "installed_rpm_packages_for", return_value=all_installed), \
             mock.patch("niri_plus.quickshell._rpm_version", return_value=("OK", "0:0.2.0-1.fc44.aarch64")), \
             mock.patch("niri_plus.recovery.has_rpm_payload", return_value=False):
            with self.assertRaisesRegex(install.InstallError, "offline rollback"):
                install.validate_package_plan(
                    lock["engine"], allow_quickshell_update=True,
                    recovery_bundle=pathlib.Path("/var/lib/niri-plus/recovery/missing.tar.gz"),
                )


class OfflineRecoveryTests(unittest.TestCase):
    def test_failed_install_compensation_includes_only_absent_quickshell_engine(self):
        with_quickshell = snapshot_install._failed_transaction_new_packages(
            install, include_quickshell=True, preexisting_packages={"niri", "foot", "quickshell"},
        )
        self.assertNotIn("quickshell", with_quickshell)
        self.assertNotIn("niri", with_quickshell)
        self.assertIn("fuzzel", with_quickshell)

        first_plugin = snapshot_install._failed_transaction_new_packages(
            install, include_quickshell=True, preexisting_packages=set(install.PACKAGES),
        )
        self.assertEqual(first_plugin, ["quickshell"])

        core_only = snapshot_install._failed_transaction_new_packages(
            install, include_quickshell=False, preexisting_packages={"niri"},
        )
        self.assertNotIn("quickshell", core_only)
        self.assertNotIn("niri", core_only)

    def test_recovery_core_is_a_standalone_emergency_cli(self):
        completed = subprocess.run(
            [os.sys.executable, "-I", str(ROOT / "niri_plus/recovery_core.py"), "--help"],
            check=False, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Protected local recovery bundle", completed.stdout)
        self.assertNotIn("niri_plus.cli", completed.stdout)

    def test_standalone_core_coverage_matches_installer_owned_path_set(self):
        expected = set(install_transaction.transaction_paths(install.MANAGED_FILES, install.MANAGED_LINKS))
        expected.add(pathlib.Path("/usr/local/libexec/niri-plus-recover"))
        self.assertEqual(set(recovery.managed_roots()), expected)

    def test_bundle_is_read_only_verified_and_restores_managed_files_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = root / "etc/niri/config.kdl"
            config.parent.mkdir(parents=True)
            config.write_text("known-good config\n", encoding="utf-8")
            config.chmod(0o640)
            data = root / "usr/local/share/niri-plus"
            data.mkdir(parents=True)
            (data / "VERSION").write_text("0.1.9\n", encoding="utf-8")
            bundle = recovery.prepare_bundle(root, runner=lambda *a, **k: __import__("subprocess").CompletedProcess(a[0], 127, "", "rpm unavailable"))
            self.assertTrue(bundle.is_file())
            self.assertIn("READY", recovery.status(root))
            config.write_text("partially updated\n", encoding="utf-8")
            restored = recovery.restore(bundle, root)
            self.assertIn("RPM packages were inventoried", restored)
            self.assertEqual(config.read_text(encoding="utf-8"), "known-good config\n")
            self.assertEqual(config.stat().st_mode & 0o777, 0o640)
            self.assertTrue(list((root / "var/lib/niri-plus/recovery").glob("preserved-edits-*/*")))

    def test_corrupt_bundle_is_rejected_without_host_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = root / "etc/niri/config.kdl"
            config.parent.mkdir(parents=True)
            config.write_text("before\n", encoding="utf-8")
            bundle = recovery.prepare_bundle(root)
            config.write_text("after\n", encoding="utf-8")
            with bundle.open("ab") as stream:
                stream.write(b"tamper")
            with self.assertRaisesRegex(recovery.RecoveryError, "checksum"):
                recovery.restore(bundle, root)
            self.assertEqual(config.read_text(encoding="utf-8"), "after\n")

    def test_interrupted_bundle_publish_keeps_previous_recovery_point(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = root / "etc/niri/config.kdl"
            config.parent.mkdir(parents=True)
            config.write_text("first known good\n", encoding="utf-8")
            previous = recovery.prepare_bundle(root)
            config.write_text("second state\n", encoding="utf-8")
            real_replace = os.replace

            def fail_before_archive_publish(source, destination):
                destination = pathlib.Path(destination)
                if destination.parent.name == "recovery" and destination.name.startswith("recovery-") and destination.suffix == ".gz":
                    raise OSError("injected interruption before archive publication")
                return real_replace(source, destination)

            with mock.patch.object(recovery_core.os, "replace", side_effect=fail_before_archive_publish):
                with self.assertRaisesRegex(OSError, "injected interruption"):
                    recovery.prepare_bundle(root)
            self.assertEqual(recovery.latest_bundle(root), previous)
            self.assertIn("READY", recovery.status(root))
            self.assertIn("Restored Niri+ not-installed", recovery.restore(root=root))
            self.assertEqual(config.read_text(encoding="utf-8"), "first known good\n")

    def test_recovery_sidecar_is_bound_to_bundle_filename_and_bundle_must_be_in_store(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = pathlib.Path(temp)
            config = root / "etc/niri/config.kdl"
            config.parent.mkdir(parents=True)
            config.write_text("known-good\n", encoding="utf-8")
            bundle = recovery.prepare_bundle(root)
            sidecar = bundle.with_suffix(bundle.suffix + ".sha256")
            good_sidecar = sidecar.read_text(encoding="ascii")
            sidecar.write_text(good_sidecar.split("  ")[0] + "  other.tar.gz\n", encoding="ascii")
            self.assertIn("CORRUPT", recovery.status(root))
            sidecar.write_text(good_sidecar, encoding="ascii")
            external = pathlib.Path(outside) / bundle.name
            external.write_bytes(bundle.read_bytes())
            external.with_suffix(external.suffix + ".sha256").write_text(good_sidecar, encoding="ascii")
            with self.assertRaisesRegex(recovery.RecoveryError, "protected recovery directory"):
                recovery.restore(external, root)

    def test_recovery_store_permissions_are_checked_before_bundle_use(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = root / "etc/niri/config.kdl"
            config.parent.mkdir(parents=True)
            config.write_text("known-good\n", encoding="utf-8")
            bundle = recovery.prepare_bundle(root)
            (root / "var/lib/niri-plus/recovery").chmod(0o755)
            self.assertIn("CORRUPT", recovery.status(root))
            with self.assertRaisesRegex(recovery.RecoveryError, "mode 0700"):
                recovery.restore(bundle, root)

    def test_recovery_and_installer_share_the_same_operation_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = root / "etc/niri/config.kdl"
            config.parent.mkdir(parents=True)
            config.write_text("known-good\n", encoding="utf-8")
            bundle = recovery.prepare_bundle(root)
            with install_transaction.operation_lock(root):
                with self.assertRaisesRegex(recovery.RecoveryError, "already running"):
                    recovery.restore(bundle, root)
                with self.assertRaisesRegex(recovery.RecoveryError, "already running"):
                    recovery.prepare_bundle(root)

    def test_failed_install_compensation_removes_only_new_owned_core_rpms(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = root / "etc/niri/config.kdl"
            config.parent.mkdir(parents=True)
            config.write_text("before\n", encoding="utf-8")

            def no_packages(*args, **kwargs):
                return subprocess.CompletedProcess(args[0], 127, "", "rpm unavailable")

            bundle = recovery.prepare_bundle(root, runner=no_packages)
            config.write_text("failed candidate\n", encoding="utf-8")
            calls = []
            niri_nevra = "0:26.04-1.fc44.aarch64"

            def runner(args, **kwargs):
                calls.append(args)
                if args[:3] == ["rpm", "-q", "--requires"]:
                    return subprocess.CompletedProcess(args, 0, "glibc\n", "")
                if args[:2] == ["rpm", "-q"]:
                    return subprocess.CompletedProcess(args, 0, f"niri\t{niri_nevra}\n", "")
                return subprocess.CompletedProcess(args, 0, "", "")

            restored = recovery.restore(bundle, root, runner=runner, restore_packages=True,
                                        extra_remove_packages=["niri"])
            self.assertIn("New Niri+-owned packages removed without autoremove: niri", restored)
            self.assertEqual(config.read_text(encoding="utf-8"), "before\n")
            self.assertTrue(any(args[:2] == ["dnf", "remove"] and "--assumeno" in args for args in calls))
            self.assertTrue(any(args[:2] == ["dnf", "remove"] and "--noautoremove" in args for args in calls))

    def test_offline_file_restore_completes_when_cached_dnf_cannot_plan_optional_cleanup(self):
        for dnf_failure in ("exit", "missing"):
            with self.subTest(dnf_failure=dnf_failure), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp)
                config = root / "etc/niri/config.kdl"
                config.parent.mkdir(parents=True)
                config.write_text("known-good\n", encoding="utf-8")

                def no_packages(*args, **kwargs):
                    return subprocess.CompletedProcess(args[0], 127, "", "rpm unavailable")

                bundle = recovery.prepare_bundle(root, runner=no_packages)
                state = install.load_state(root)
                state["packages_installed_by_us"] = ["niri"]
                install.write_state(root, state)
                config.write_text("candidate\n", encoding="utf-8")
                calls = []

                def runner(args, **kwargs):
                    calls.append(args)
                    if args[:3] == ["rpm", "-q", "--requires"]:
                        return subprocess.CompletedProcess(args, 0, "glibc\n", "")
                    if args[:2] == ["rpm", "-q"]:
                        return subprocess.CompletedProcess(args, 0, "niri\t0:26.04-1.fc44.aarch64\n", "")
                    if args[:3] == ["dnf", "remove", "--cacheonly"] and "--assumeno" in args:
                        if dnf_failure == "missing":
                            raise FileNotFoundError("dnf")
                        return subprocess.CompletedProcess(args, 1, "", "offline metadata unavailable")
                    return subprocess.CompletedProcess(args, 0, "", "")

                result = recovery.restore(bundle, root, runner=runner, restore_packages=True)
                self.assertIn("offline RPM cleanup was deferred", result)
                self.assertEqual(config.read_text(encoding="utf-8"), "known-good\n")
                self.assertFalse(any(args[:2] == ["dnf", "remove"] and "-y" in args for args in calls))

    def test_failed_first_plugin_install_removes_only_a_newly_owned_quickshell_rpm(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            unavailable = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 127, "", "rpm unavailable")
            bundle = recovery.prepare_bundle(root, runner=unavailable)
            package_state = install.load_state(root)
            package_state["packages_installed_by_us"] = ["quickshell"]
            install.write_state(root, package_state)
            calls = []
            qs_nevra = "0:0.4.0-1.fc44.aarch64"

            def runner(args, **kwargs):
                calls.append(args)
                if args[:3] == ["rpm", "-q", "--requires"]:
                    return subprocess.CompletedProcess(args, 0, "glibc\n", "")
                if args[:2] == ["rpm", "-q"]:
                    return subprocess.CompletedProcess(args, 0, f"quickshell\t{qs_nevra}\n", "")
                return subprocess.CompletedProcess(args, 0, "", "")

            restored = recovery.restore(bundle, root, runner=runner, restore_packages=True)
            self.assertIn("New Niri+-owned packages removed without autoremove: quickshell", restored)
            remove_calls = [args for args in calls if args[:2] == ["dnf", "remove"]]
            self.assertEqual(len(remove_calls), 2)
            self.assertTrue(all(args[-1] == "quickshell" and "--noautoremove" in args for args in remove_calls))


class OperationLockTests(unittest.TestCase):
    def test_lock_is_exclusive_between_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            program = textwrap.dedent("""
                import pathlib, sys, time
                from niri_plus.install_transaction import operation_lock
                with operation_lock(pathlib.Path(sys.argv[1])):
                    print("locked", flush=True)
                    time.sleep(20)
            """)
            process = subprocess.Popen(
                [sys.executable, "-c", program, str(root)], cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                with self.assertRaisesRegex(install_transaction.TransactionError, "already running"):
                    with install_transaction.operation_lock(root):
                        pass
            finally:
                process.terminate()
                process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()

    def test_same_process_lock_is_reentrant_for_the_filesystem_transaction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            target = root / "etc/niri/config.kdl"
            target.parent.mkdir(parents=True)
            target.write_text("config\n", encoding="utf-8")
            with install_transaction.operation_lock(root):
                with install_transaction.FilesystemTransaction([pathlib.Path("/etc/niri/config.kdl")], root=root) as tx:
                    target.write_text("committed\n", encoding="utf-8")
                    tx.commit()
            self.assertEqual(target.read_text(encoding="utf-8"), "committed\n")

    def test_symlinked_lock_file_is_refused(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other:
            root = pathlib.Path(temp)
            lock = root / "run/niri-plus-operation.lock"
            lock.parent.mkdir(parents=True)
            lock.symlink_to(pathlib.Path(other) / "lock")
            with self.assertRaises(install_transaction.TransactionError):
                with install_transaction.operation_lock(root):
                    pass

    def test_recovery_embeds_and_restores_cached_quickshell_rpm_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = root / "etc/niri/config.kdl"
            config.parent.mkdir(parents=True)
            config.write_text("known-good\n", encoding="utf-8")
            cache = root / "var/cache/dnf/test/packages"
            cache.mkdir(parents=True)
            old_rpm = cache / "quickshell-0.3.1-2.fc44.aarch64.rpm"
            old_rpm.write_bytes(b"simulated rpm payload")
            old_nevra = "0:0.3.1-2.fc44.aarch64"
            new_nevra = "0:0.4.0-1.fc44.aarch64"

            def prepare_runner(args, **kwargs):
                if args[1:2] == ["-q"]:
                    return subprocess.CompletedProcess(args, 0, f"quickshell\t{old_nevra}\n", "")
                if args[1:2] == ["-qp"]:
                    return subprocess.CompletedProcess(args, 0, old_nevra, "")
                if args[0] == "rpmkeys":
                    return subprocess.CompletedProcess(args, 0, "digests signatures OK", "")
                return subprocess.CompletedProcess(args, 1, "", "not available")

            bundle = recovery.prepare_bundle(root, runner=prepare_runner)
            self.assertTrue(recovery.has_rpm_payload(bundle, "quickshell", old_nevra, root))
            config.write_text("candidate config\n", encoding="utf-8")
            calls = []
            trusted_signature = False

            def restore_runner(args, **kwargs):
                calls.append(args)
                if args[1:2] == ["-q"]:
                    return subprocess.CompletedProcess(args, 0, f"quickshell\t{new_nevra}\n", "")
                if args[0] == "rpmkeys":
                    output = "digests signatures OK" if trusted_signature else "NOKEY"
                    return subprocess.CompletedProcess(args, 0, output, "")
                return subprocess.CompletedProcess(args, 0, "Signature OK", "")

            with self.assertRaisesRegex(recovery.RecoveryError, "signature validation failed"):
                recovery.restore(bundle, root, runner=restore_runner, restore_packages=True)
            self.assertEqual(config.read_text(encoding="utf-8"), "candidate config\n")
            trusted_signature = True
            message = recovery.restore(bundle, root, runner=restore_runner, restore_packages=True)
            self.assertIn("Quickshell RPM was also restored offline", message)
            self.assertEqual(config.read_text(encoding="utf-8"), "known-good\n")
            self.assertTrue(any(command[:2] == ["rpm", "-Uvh"] for command in calls))

    def test_recovery_never_bundles_an_untrusted_cached_quickshell_rpm(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            cache = root / "var/cache/dnf/test/packages"
            cache.mkdir(parents=True)
            candidate = cache / "quickshell-0.3.1-2.fc44.aarch64.rpm"
            candidate.write_bytes(b"untrusted rpm")

            for signature_output in ("NOKEY", "digests OK", "signatures NOT OK"):
                with self.subTest(signature_output=signature_output):
                    def runner(args, **kwargs):
                        if args[0] == "rpm" and "-qp" in args:
                            return subprocess.CompletedProcess(args, 0, "0:0.3.1-2.fc44.aarch64", "")
                        if args[0] == "rpmkeys":
                            # Some rpm versions can return zero for NOKEY or
                            # digest-only results. Neither is restore trust.
                            return subprocess.CompletedProcess(args, 0, signature_output, "")
                        return subprocess.CompletedProcess(args, 1, "", "unavailable")

                    self.assertIsNone(
                        recovery_core._cached_quickshell_rpm(root, "0:0.3.1-2.fc44.aarch64", runner)
                    )

            def verbose_success(args, **kwargs):
                if args[0] == "rpm" and "-qp" in args:
                    return subprocess.CompletedProcess(args, 0, "0:0.3.1-2.fc44.aarch64", "")
                if args[0] == "rpmkeys":
                    return subprocess.CompletedProcess(
                        args, 0,
                        "Header V4 RSA/SHA256 Signature, key ID abc: OK\nPayload SHA256 digest: OK", "",
                    )
                return subprocess.CompletedProcess(args, 1, "", "unavailable")

            self.assertEqual(
                recovery_core._cached_quickshell_rpm(root, "0:0.3.1-2.fc44.aarch64", verbose_success),
                candidate,
            )

    def test_recovery_rejects_unsafe_paths_and_symlinked_storage(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery._canonical_member_name("../../etc/shadow")
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other:
            root = pathlib.Path(temp)
            (root / "var/lib/niri-plus").mkdir(parents=True)
            (root / "var/lib/niri-plus/recovery").symlink_to(other)
            with self.assertRaises(recovery.RecoveryError):
                recovery.prepare_bundle(root)

    def test_recovery_refuses_to_start_when_disk_reserve_is_low(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            with mock.patch.object(recovery.shutil, "disk_usage", return_value=type("Usage", (), {"free": 0})()):
                with self.assertRaisesRegex(recovery.RecoveryError, "32 MiB"):
                    recovery.prepare_bundle(root)

    def test_preflight_is_read_only_and_never_reports_m1_as_cloud_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            result = preflight.inspect(root, os_release={"ID": "fedora-asahi-remix", "VERSION_ID": "44"}, machine="aarch64")
            after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            self.assertEqual(before, after)
            self.assertNotEqual(result["status"], "CLOUD_VALIDATED")
            self.assertIn("offline_recovery", {item["name"] for item in result["checks"]})


if __name__ == "__main__":
    unittest.main()
