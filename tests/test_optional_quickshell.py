from __future__ import annotations

import json
import os
import pathlib
import runpy
import shutil
import tempfile
import unittest

from niri_plus import install, install_transaction

from niri_plus.install import update_quickshell_state


BOOTSTRAP = pathlib.Path(__file__).resolve().parents[1] / "scripts/bootstrap-niri-plus"
preserve_optional_quickshell = runpy.run_path(
    str(BOOTSTRAP), run_name="niri_plus_bootstrap_test_helpers",
)["preserve_optional_quickshell"]


class OptionalQuickshellTests(unittest.TestCase):
    def test_niri_core_installs_without_creating_quickshell_runtime_or_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            install.install_files(root, include_quickshell=False)

            self.assertTrue(install.prefixed(root, "/etc/niri/config.kdl").is_file())
            self.assertFalse(install.prefixed(root, "/usr/lib/systemd/user/asahi-quickshell.service").exists())
            self.assertFalse(install.prefixed(
                root, "/usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service"
            ).exists())
            self.assertFalse(install.prefixed(root, "/usr/local/share/niri-plus/quickshell").exists())
            state = install.load_state(root)
            self.assertNotIn("quickshell", state)

            install.rollback_files(root)
            self.assertFalse(install.prefixed(root, "/etc/niri/config.kdl").exists())

    def test_core_only_update_preserves_known_good_visual_install_and_permissions(self):
        stable_pin = "55e92880d0aff75d235f283c839ec0990eaa9e17"
        candidate_pin = json.loads((pathlib.Path(__file__).resolve().parents[1]
                                    / "integration/quickshell.lock.json").read_text())["commit"]
        self.assertNotEqual(candidate_pin, stable_pin)

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = install.prefixed(root, "/etc/niri/config.kdl")
            service = install.prefixed(root, "/usr/lib/systemd/user/asahi-quickshell.service")
            wants = install.prefixed(
                root, "/usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service"
            )
            runtime = install.prefixed(root, "/usr/local/share/niri-plus/quickshell")
            config.parent.mkdir(parents=True)
            service.parent.mkdir(parents=True)
            config.write_text("user-owned config\n", encoding="utf-8")
            config.chmod(0o600)
            service.write_text("known-good unit\n", encoding="utf-8")
            service.chmod(0o640)
            wants.parent.mkdir(parents=True)
            wants.symlink_to("../previous-quickshell.service")
            runtime.mkdir(parents=True)
            (runtime / "shell.qml").write_text("known-good visual runtime\n", encoding="utf-8")
            state = {"schema_version": 1, "entries": {}, "packages_installed_by_us": ["niri", "foot"],
                     "quickshell": {"expected_commit": stable_pin, "known_good_commit": stable_pin}}
            install.write_state(root, state)

            install.install_files(root, include_quickshell=False)
            state = install.load_state(root)
            install.update_quickshell_state(state, candidate_pin, included=False)
            install.write_state(root, state)

            self.assertEqual(service.read_text(encoding="utf-8"), "known-good unit\n")
            self.assertEqual(service.stat().st_mode & 0o777, 0o640)
            self.assertEqual(os.readlink(wants), "../previous-quickshell.service")
            self.assertEqual((runtime / "shell.qml").read_text(encoding="utf-8"), "known-good visual runtime\n")
            self.assertEqual(state["quickshell"]["expected_commit"], stable_pin)
            self.assertEqual(state["quickshell"]["known_good_commit"], stable_pin)
            self.assertNotIn("quickshell", state["packages_installed_by_us"])

            install.rollback_files(root)
            self.assertEqual(config.read_text(encoding="utf-8"), "user-owned config\n")
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(service.stat().st_mode & 0o777, 0o640)
            self.assertEqual(os.readlink(wants), "../previous-quickshell.service")
            self.assertEqual(state["quickshell"]["expected_commit"], stable_pin)

    def test_failed_integrated_update_restores_code_runtime_state_and_file_modes(self):
        lock = json.loads((pathlib.Path(__file__).resolve().parents[1]
                           / "integration/quickshell.lock.json").read_text())
        candidate_pin = lock["commit"]
        stable_pin = "55e92880d0aff75d235f283c839ec0990eaa9e17"
        paths = install_transaction.transaction_paths(install.MANAGED_FILES, install.MANAGED_LINKS)

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = install.prefixed(root, "/etc/niri/config.kdl")
            service = install.prefixed(root, "/usr/lib/systemd/user/asahi-quickshell.service")
            wants = install.prefixed(
                root, "/usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service"
            )
            data = install.prefixed(root, "/usr/local/share/niri-plus")
            lib = install.prefixed(root, "/usr/local/lib/niri-plus")
            cli = install.prefixed(root, "/usr/local/bin/niri+")
            config.parent.mkdir(parents=True)
            config.write_text("previous config\n", encoding="utf-8")
            config.chmod(0o600)
            service.parent.mkdir(parents=True)
            service.write_text("previous Quickshell unit\n", encoding="utf-8")
            service.chmod(0o640)
            wants.parent.mkdir(parents=True)
            wants.symlink_to("../previous-quickshell.service")
            data.mkdir(parents=True)
            (data / "VERSION").write_text("0.1.9\n", encoding="utf-8")
            (data / "quickshell").mkdir()
            (data / "quickshell/shell.qml").write_text("known-good quickshell\n", encoding="utf-8")
            lib.mkdir(parents=True)
            (lib / "marker").write_text("old modules\n", encoding="utf-8")
            cli.parent.mkdir(parents=True)
            cli.write_text("old niri+ launcher\n", encoding="utf-8")
            state_path = install.prefixed(root, install.STATE_PATH)
            state_path.parent.mkdir(parents=True)
            state = {"schema_version": 1, "entries": {}, "applied_version": "0.1.9",
                     "packages_installed_by_us": ["niri", "foot"],
                     "quickshell": {"expected_commit": stable_pin, "known_good_commit": stable_pin}}
            install.write_state(root, state)

            with self.assertRaisesRegex(RuntimeError, "injected verify failure"):
                with install_transaction.FilesystemTransaction(paths, root=root) as transaction:
                    install.install_files(root, include_quickshell=True)
                    new_state = install.load_state(root)
                    install.update_quickshell_state(new_state, candidate_pin, included=True)
                    new_state["applied_version"] = "0.1.10-rc"
                    install.write_state(root, new_state)
                    shutil.rmtree(data / "quickshell")
                    (data / "quickshell").mkdir()
                    (data / "quickshell/shell.qml").write_text("candidate quickshell\n", encoding="utf-8")
                    (data / "quickshell.snapshot.json").write_text(
                        json.dumps({"repository": lock["repository"], "commit": candidate_pin}) + "\n",
                        encoding="utf-8",
                    )
                    (lib / "marker").write_text("new modules\n", encoding="utf-8")
                    cli.write_text("new niri+ launcher\n", encoding="utf-8")
                    raise RuntimeError("injected verify failure")

            restored = install.load_state(root)
            self.assertEqual((data / "VERSION").read_text(encoding="utf-8"), "0.1.9\n")
            self.assertEqual((data / "quickshell/shell.qml").read_text(encoding="utf-8"), "known-good quickshell\n")
            self.assertFalse((data / "quickshell.snapshot.json").exists())
            self.assertEqual((lib / "marker").read_text(encoding="utf-8"), "old modules\n")
            self.assertEqual(cli.read_text(encoding="utf-8"), "old niri+ launcher\n")
            self.assertEqual(config.read_text(encoding="utf-8"), "previous config\n")
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(service.read_text(encoding="utf-8"), "previous Quickshell unit\n")
            self.assertEqual(service.stat().st_mode & 0o777, 0o640)
            self.assertEqual(os.readlink(wants), "../previous-quickshell.service")
            self.assertEqual(restored["applied_version"], "0.1.9")
            self.assertEqual(restored["quickshell"]["expected_commit"], stable_pin)
            self.assertEqual(restored["quickshell"]["known_good_commit"], stable_pin)
    def test_core_only_upgrade_preserves_existing_visual_runtime_and_pin_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            current = root / "current"
            staged = root / "staged"
            (current / "quickshell").mkdir(parents=True)
            (current / "quickshell/shell.qml").write_text("visual runtime\n", encoding="utf-8")
            (current / "quickshell.snapshot.json").write_text('{"commit":"abc"}\n', encoding="utf-8")
            staged.mkdir()

            preserve_optional_quickshell(current, staged)

            self.assertEqual((staged / "quickshell/shell.qml").read_text(encoding="utf-8"), "visual runtime\n")
            self.assertEqual((staged / "quickshell.snapshot.json").read_text(encoding="utf-8"), '{"commit":"abc"}\n')
            self.assertTrue((current / "quickshell/shell.qml").is_file())

    def test_core_only_install_keeps_old_visual_pin_but_fresh_install_has_none(self):
        state = {"quickshell": {"expected_commit": "a" * 40, "known_good_commit": "b" * 40}}
        update_quickshell_state(state, "c" * 40, included=False)
        self.assertEqual(state["quickshell"]["expected_commit"], "a" * 40)
        self.assertEqual(state["quickshell"]["known_good_commit"], "b" * 40)

        fresh = {}
        update_quickshell_state(fresh, "c" * 40, included=False)
        self.assertIsNone(fresh["quickshell"]["expected_commit"])

    def test_explicit_visual_install_updates_the_expected_pin(self):
        state = {"quickshell": {"expected_commit": "a" * 40}}
        update_quickshell_state(state, "c" * 40, included=True)
        self.assertEqual(state["quickshell"]["expected_commit"], "c" * 40)


if __name__ == "__main__":
    unittest.main()
