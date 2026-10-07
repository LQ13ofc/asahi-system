import contextlib
import configparser
import io
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

import kdl


ROOT = pathlib.Path(__file__).resolve().parents[1]
from niri_plus import install
installer = install


class NiriSessionTests(unittest.TestCase):
    def test_all_kdl_files_parse(self):
        for path in sorted((ROOT / "niri").glob("*.kdl")):
            with self.subTest(path=path.name):
                document = kdl.parse(path.read_text(encoding="utf-8"))
                self.assertIsNotNone(document)

    def test_reuses_fedora_packaged_session_and_preserves_plasma_choice(self):
        self.assertNotIn("/usr/share/wayland-sessions/niri-performance.desktop", installer.MANAGED_FILES)
        self.assertNotIn("/usr/local/bin/asahi-niri-session", installer.MANAGED_FILES)
        self.assertNotIn("/usr/share/wayland-sessions/plasma.desktop", installer.MANAGED_FILES)
        self.assertEqual(installer.MANAGED_FILES["/etc/niri/config.kdl"], ROOT / "niri/config.kdl")

    def test_polkit_agent_is_bound_to_niri_graphical_lifecycle(self):
        unit = (ROOT / "sessions/systemd/asahi-niri-polkit-agent.service").read_text(encoding="utf-8")
        self.assertIn("ConditionEnvironment=XDG_CURRENT_DESKTOP=niri", unit)
        self.assertIn("PartOf=graphical-session.target", unit)
        self.assertIn("After=graphical-session-pre.target", unit)
        self.assertIn("ExecStart=/usr/libexec/lxqt-policykit-agent", unit)
        self.assertNotIn("speakersafetyd", unit)

    def test_only_minimal_explicit_packages_are_requested(self):
        package_lines = [line.strip() for line in (ROOT / "packages/niri-performance.txt").read_text().splitlines() if line.strip() and not line.startswith("#")]
        self.assertEqual(package_lines, ["niri", "foot", "fuzzel", "xdg-desktop-portal-gtk", "lxqt-policykit"])
        self.assertNotIn("plasma", " ".join(package_lines))
        self.assertNotIn("gamescope", " ".join(package_lines))

    def test_target_checks_reject_cloud_and_non_asahi(self):
        fedora_asahi_44 = {"ID": "fedora-asahi-remix", "VERSION_ID": "44"}
        self.assertEqual(installer.target_mismatches(fedora_asahi_44, "aarch64"), [])
        self.assertEqual(len(installer.target_mismatches({"ID": "fedora", "VERSION_ID": "44"}, "x86_64")), 2)
        self.assertEqual(len(installer.target_mismatches({"ID": "fedora-asahi-remix", "VERSION_ID": "43"}, "aarch64")), 1)

    def test_incompatible_apply_refuses_before_writing_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            before = list(root.rglob("*"))
            with self.assertRaisesRegex(installer.InstallError, "incompatible host"):
                installer.apply_install(root=root, os_release={"ID": "fedora", "VERSION_ID": "44"}, machine="x86_64")
            self.assertEqual(before, list(root.rglob("*")))

    def test_cloud_dry_run_prints_plan_without_applying(self):
        output = io.StringIO()
        error = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error), \
             mock.patch.object(installer, "parse_os_release", return_value={"ID": "fedora", "VERSION_ID": "44"}), \
             mock.patch.object(installer.platform, "machine", return_value="x86_64"):
            status = installer.main(["--dry-run"])
        self.assertEqual(status, 0)
        self.assertIn("Niri+ install plan", output.getvalue())
        self.assertIn("Apply will refuse this host", error.getvalue())

    def test_install_is_idempotent_and_rollback_restores_backups(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            preexisting = installer.prefixed(root, "/etc/niri/config.kdl")
            preexisting.parent.mkdir(parents=True)
            preexisting.write_text("user config\n")

            _, first_changed = installer.install_files(root)
            _, second_changed = installer.install_files(root)
            self.assertTrue(first_changed)
            self.assertFalse(second_changed)
            self.assertIn('include "keybinds.kdl"', preexisting.read_text())

            restored = installer.rollback_files(root)
            self.assertEqual(preexisting.read_text(), "user config\n")
            self.assertTrue(any("restored /etc/niri/config.kdl" in item for item in restored))

    def test_rollback_saves_post_install_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = installer.prefixed(root, "/etc/niri/config.kdl")
            installer.install_files(root)
            config.write_text("local post-install edit\n")
            installer.rollback_files(root)
            saved_edit = installer.prefixed(root, "/var/lib/asahi-system/niri-performance/rollback-edits/etc/niri/config.kdl")
            self.assertEqual(saved_edit.read_text(), "local post-install edit\n")
            self.assertFalse(config.exists())

    def test_existing_identical_destination_is_preserved_by_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            relative = "/etc/niri/config.kdl"
            target = installer.prefixed(root, relative)
            target.parent.mkdir(parents=True)
            target.write_bytes(installer.content_for(installer.MANAGED_FILES[relative]))
            installer.install_files(root)
            installer.rollback_files(root)
            self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
