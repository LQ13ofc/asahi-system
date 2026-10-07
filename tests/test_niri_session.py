import contextlib
import configparser
import io
import os
import pathlib
import subprocess
import tempfile
import unittest
import json
from unittest import mock

import kdl


ROOT = pathlib.Path(__file__).resolve().parents[1]
from niri_plus import host, install
installer = install


class NiriSessionTests(unittest.TestCase):
    def test_bootstrap_entrypoint_is_executable(self):
        bootstrap = ROOT / "scripts/bootstrap-niri-plus"
        self.assertTrue(os.access(bootstrap, os.R_OK | os.X_OK))

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
        self.assertFalse((ROOT / "sessions/niri.desktop").exists())
        self.assertFalse((ROOT / "sessions/launch/niri-session").exists())

    def test_polkit_agent_is_bound_to_niri_graphical_lifecycle(self):
        unit = (ROOT / "sessions/systemd/asahi-niri-polkit-agent.service").read_text(encoding="utf-8")
        self.assertIn("ConditionEnvironment=XDG_CURRENT_DESKTOP=niri", unit)
        self.assertIn("PartOf=graphical-session.target", unit)
        self.assertIn("Requires=asahi-niri-wayland-ready.service", unit)
        self.assertIn("After=asahi-niri-wayland-ready.service", unit)
        self.assertIn("ExecStart=/usr/libexec/lxqt-policykit-agent", unit)
        self.assertNotIn("speakersafetyd", unit)

    def test_wayland_readiness_gate_precedes_niri_graphical_clients(self):
        ready = (ROOT / "sessions/systemd/asahi-niri-wayland-ready.service").read_text(encoding="utf-8")
        self.assertIn("ConditionEnvironment=XDG_CURRENT_DESKTOP=niri", ready)
        self.assertIn("After=graphical-session-pre.target", ready)
        self.assertIn("Before=graphical-session.target", ready)
        self.assertIn("PartOf=graphical-session.target", ready)
        self.assertIn("WantedBy=graphical-session.target", ready)
        self.assertIn("wayland_ready.py", ready)
        ready_unit = configparser.ConfigParser()
        ready_unit.read_string(ready)
        self.assertEqual(ready_unit.get("Service", "Type"), "oneshot")
        self.assertEqual(ready_unit.get("Service", "RemainAfterExit"), "yes")
        self.assertEqual(ready_unit.get("Service", "TimeoutStartSec"), "25s")
        self.assertFalse(ready_unit.has_option("Unit", "TimeoutStartSec"))
        with tempfile.TemporaryDirectory() as temp:
            host_root = pathlib.Path(temp)
            unit_root = host_root / "usr/lib/systemd/user"
            unit_root.mkdir(parents=True)
            for name in ("asahi-niri-wayland-ready.service", "asahi-quickshell.service", "asahi-niri-polkit-agent.service"):
                (unit_root / name).write_text((ROOT / "sessions/systemd" / name).read_text())
            self.assertEqual(host.wayland_readiness_status(host_root), host.OK)

    def test_only_minimal_explicit_packages_are_requested(self):
        package_lines = [line.strip() for line in (ROOT / "packages/niri-performance.txt").read_text().splitlines() if line.strip() and not line.startswith("#")]
        self.assertEqual(package_lines, ["niri", "foot", "fuzzel", "xdg-desktop-portal-gtk", "lxqt-policykit", "python3-dbus", "python3-gobject", "quickshell"])
        self.assertNotIn("plasma", " ".join(package_lines))
        self.assertNotIn("gamescope", " ".join(package_lines))

    def test_command_space_is_primary_fuzzel_launcher(self):
        keybinds = (ROOT / "niri/keybinds.kdl").read_text(encoding="utf-8")
        self.assertRegex(keybinds, r'Mod\+Space\s*\{\s*spawn "fuzzel";\s*\}')
        self.assertEqual(keybinds.count('spawn "fuzzel"'), 2)
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            target = root / "etc/niri/keybinds.kdl"
            target.parent.mkdir(parents=True)
            target.write_text(keybinds, encoding="utf-8")
            self.assertEqual(host.launcher_status(root), host.OK)
            target.write_text(keybinds.replace("Mod+Space", "Mod+P"), encoding="utf-8")
            self.assertEqual(host.launcher_status(root), host.WARNING)

    def test_quickshell_submodule_and_lock_have_same_exact_pin(self):
        lock = json.loads((ROOT / "integration/quickshell.lock.json").read_text())
        gitmodules = (ROOT / ".gitmodules").read_text()
        self.assertIn('path = external/quickshell', gitmodules)
        self.assertIn('url = https://github.com/LQ13ofc/quickshell-.git', gitmodules)
        self.assertEqual(lock["repository"], "https://github.com/LQ13ofc/quickshell-.git")
        self.assertRegex(lock["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(lock["checkout"], "external/quickshell")
        self.assertEqual(lock["engine"]["architecture"], "aarch64")
        self.assertEqual(lock["engine"]["compatibility"], "M1_REQUIRED")
        if (ROOT / ".git").exists():
            result = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "HEAD", "external/quickshell"],
                                    check=True, capture_output=True, text=True)
            fields = result.stdout.split()
            self.assertGreaterEqual(len(fields), 4)
            self.assertEqual(fields[0], "160000")
            self.assertEqual(fields[1], "commit")
            self.assertEqual(fields[2], lock["commit"])
        else:
            self.skipTest("Cloud source archive has no Git tree metadata; CI validates the gitlink")

    def test_visual_qml_is_not_duplicated_in_asahi_system(self):
        self.assertEqual(list(ROOT.rglob("*.qml")), [])

    def test_quickshell_lifecycle_and_no_niri_exec_once_duplication(self):
        unit = (ROOT / "sessions/systemd/asahi-quickshell.service").read_text()
        self.assertIn("ConditionEnvironment=XDG_CURRENT_DESKTOP=niri", unit)
        self.assertIn("Requires=asahi-niri-wayland-ready.service", unit)
        self.assertIn("After=asahi-niri-wayland-ready.service", unit)
        self.assertIn("PartOf=graphical-session.target", unit)
        self.assertIn("WantedBy=graphical-session.target", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("ExecStart=/usr/bin/qs --path /usr/local/share/niri-plus/quickshell/shell.qml", unit)
        self.assertIn("asahi-quickshell.service", installer.MANAGED_LINKS["/usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service"])
        self.assertNotIn("spawn-at-startup", "\n".join(path.read_text() for path in (ROOT / "niri").glob("*.kdl")))

    def test_xwayland_video_bridge_is_only_filtered_for_plasma_context(self):
        dropin = (ROOT / "sessions/systemd/autostart-filters/app-org.kde.xwaylandvideobridge@autostart.service.d/10-niri-session.conf").read_text()
        self.assertIn("ConditionEnvironment=XDG_CURRENT_DESKTOP=KDE", dropin)
        filters = [path for path in installer.MANAGED_FILES if "@autostart.service.d/10-niri-session.conf" in path]
        self.assertEqual(len(filters), 4)
        for path in filters:
            self.assertIn("ConditionEnvironment=XDG_CURRENT_DESKTOP=KDE",
                          installer.MANAGED_FILES[path].read_text(encoding="utf-8"))
        self.assertFalse(any("disable" in str(path).lower() for path in installer.MANAGED_LINKS))
        self.assertFalse(any("xwaylandvideobridge" in path for path in installer.LEGACY_MANAGED_PATHS))
        self.assertNotIn("systemctl disable", "\n".join(path.read_text() for path in (ROOT / "sessions").rglob("*.*") if path.is_file()))

    def test_all_kde_xdg_autostarts_in_baseline_get_session_scoped_filters(self):
        baseline = (ROOT / "hardware/mba-m1-8gb/baseline/user-services.txt").read_text(encoding="utf-8")
        observed = {line.split()[0] for line in baseline.splitlines() if "app-org.kde." in line and "@autostart.service" in line}
        filtered = {path.split("/usr/lib/systemd/user/")[1].split(".d/")[0]
                    for path in installer.MANAGED_FILES if "@autostart.service.d/" in path}
        self.assertEqual(observed, filtered)

    def test_baseline_kde_background_units_are_scoped_to_kde_session(self):
        baseline = (ROOT / "hardware/mba-m1-8gb/baseline/user-services.txt").read_text(encoding="utf-8")
        observed = {line.split()[0] for line in baseline.splitlines() if line.strip()}
        common = (ROOT / "sessions/systemd/kde-session-only.conf").read_text(encoding="utf-8")
        self.assertIn("ConditionEnvironment=XDG_CURRENT_DESKTOP=KDE", common)
        self.assertIn("PartOf=graphical-session.target", common)
        for unit in installer.KDE_SESSION_ONLY_UNITS:
            with self.subTest(unit=unit):
                self.assertIn(unit, observed)
                self.assertIn(f"/usr/lib/systemd/user/{unit}.d/10-niri-session.conf", installer.MANAGED_FILES)
        self.assertNotIn("systemctl disable", common)
        self.assertNotIn("systemctl mask", common)

    def test_quickshell_unit_is_session_owned_and_restart_bounded(self):
        unit = (ROOT / "sessions/systemd/asahi-quickshell.service").read_text(encoding="utf-8")
        for required in (
            "ConditionEnvironment=XDG_CURRENT_DESKTOP=niri",
            "Requires=asahi-niri-wayland-ready.service",
            "After=asahi-niri-wayland-ready.service",
            "PartOf=graphical-session.target",
            "StartLimitIntervalSec=60",
            "StartLimitBurst=5",
            "Restart=on-failure",
            "RestartSec=2s",
            "WantedBy=graphical-session.target",
        ):
            self.assertIn(required, unit)

    def test_niri_configuration_includes_resolve(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            target = root / "etc/niri"
            target.mkdir(parents=True)
            for path in (ROOT / "niri").glob("*.kdl"):
                (target / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(host.configuration_status(root), host.OK)
            (target / "outputs.kdl").unlink()
            self.assertEqual(host.configuration_status(root), host.WARNING)

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
