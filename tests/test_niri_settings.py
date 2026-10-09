from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import kdl

from niri_plus import niri_settings


class NiriSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.config_home = self.home / ".config"
        self.config_home.mkdir(mode=0o750)
        self.niri_dir = self.config_home / "niri"
        self.niri_dir.mkdir(mode=0o755)
        self.state_home = self.home / ".local" / "state"
        self.system_config = self.root / "etc/niri/config.kdl"
        self.system_config.parent.mkdir(parents=True)
        self.system_config.write_text(niri_settings.SYSTEM_INCLUDE + "\n", encoding="utf-8")
        self.user_config = self.niri_dir / "config.kdl"
        self.user_config.write_text(
            'include optional=true "extra.kdl"\nlayout { gaps 6 }\n', encoding="utf-8"
        )
        (self.niri_dir / "extra.kdl").write_text("hotkey-overlay { skip-at-startup }\n", encoding="utf-8")
        self.environment = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "XDG_STATE_HOME": str(self.state_home),
            "NIRI_CONFIG": str(self.user_config),
        }
        self.manager = niri_settings.NiriSettingsManager(
            home=self.home,
            config_home=self.config_home,
            state_home=self.state_home,
            system_config=self.system_config,
            environment=self.environment,
            uid=os.getuid(),
            validator=lambda _path: (True, ""),
            niri_binary="niri-test-double",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_niri_settings_module_loads_from_installed_split_library_layout(self):
        library = self.root / "usr/local/lib/niri-plus"
        library.mkdir(parents=True)
        package = library / "niri_plus"
        source = pathlib.Path(__file__).resolve().parents[1] / "niri_plus"
        shutil.copytree(source, package, ignore=shutil.ignore_patterns("__pycache__"))
        probe = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from niri_plus import niri_settings; print(niri_settings.__file__)"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", probe, str(library)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pathlib.Path(result.stdout.strip()), package / "niri_settings.py")

    @staticmethod
    def settings(**changes):
        return {**niri_settings.DEFAULTS, **changes}

    def test_generated_kdl_parses_and_has_only_allowlisted_values(self):
        result = niri_settings.render_kdl(self.settings(gaps=12, border_enabled=True))
        self.assertIsNotNone(kdl.parse(result))
        self.assertIn("gaps 12", result)
        self.assertIn('Mod+Space { spawn "fuzzel"; }', result)
        with self.assertRaises(niri_settings.NiriSettingsError):
            niri_settings.render_kdl(self.settings(launcher_key='Mod+Space; exec "bad"'))
        with self.assertRaises(niri_settings.NiriSettingsError):
            niri_settings.render_kdl(self.settings(launcher_key="Mod+Return", terminal_key="Mod+Return"))

    def test_apply_persists_atomically_without_chmod_of_existing_xdg_directories(self):
        original_config_mode = stat.S_IMODE(self.config_home.stat().st_mode)
        original_niri_mode = stat.S_IMODE(self.niri_dir.stat().st_mode)
        result = self.manager.apply(self.settings(gaps=10, border_enabled=True))
        self.assertEqual(result["status"], "OK")
        self.assertEqual(stat.S_IMODE(self.config_home.stat().st_mode), original_config_mode)
        self.assertEqual(stat.S_IMODE(self.niri_dir.stat().st_mode), original_niri_mode)
        self.assertEqual(stat.S_IMODE(self.manager.settings_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.manager.state_dir.stat().st_mode), 0o700)
        installed = self.manager.settings_path.read_text(encoding="utf-8")
        self.assertIsNotNone(kdl.parse(installed))
        config = self.user_config.read_text(encoding="utf-8")
        self.assertIn('include optional=true "extra.kdl"', config)
        self.assertIn(niri_settings.USER_INCLUDE_START, config)
        self.assertEqual(self.manager.status()["status"], "OK")

    def test_apply_and_rollback_restore_previous_settings(self):
        self.manager.apply(self.settings(gaps=6))
        self.manager.apply(self.settings(gaps=14, border_enabled=True))
        result = self.manager.rollback()
        self.assertEqual(result["status"], "OK")
        self.assertEqual(self.manager.status()["settings"]["gaps"], 6)
        self.assertEqual(self.manager.status()["settings"]["border_enabled"], False)

    def test_validator_failure_leaves_user_config_and_state_untouched(self):
        original = self.user_config.read_bytes()
        self.manager.validator = lambda _path: (False, "invalid KDL")
        with self.assertRaisesRegex(niri_settings.NiriSettingsError, "invalid KDL"):
            self.manager.apply(self.settings(gaps=9))
        self.assertEqual(self.user_config.read_bytes(), original)
        self.assertFalse(self.manager.settings_path.exists())
        self.assertFalse(self.manager.state_path.exists())

    def test_failure_after_state_replace_recovers_all_managed_files(self):
        original = self.user_config.read_bytes()
        real_write = niri_settings._atomic_write
        failed = False

        def write_then_fail_once(path, data, **kwargs):
            nonlocal failed
            result = real_write(path, data, **kwargs)
            if pathlib.Path(path) == self.manager.state_path and not failed:
                failed = True
                raise OSError("simulated fsync failure after replace")
            return result

        with mock.patch.object(niri_settings, "_atomic_write", side_effect=write_then_fail_once):
            with self.assertRaisesRegex(niri_settings.NiriSettingsError, "anteriores restaurados"):
                self.manager.apply(self.settings(gaps=13))
        self.assertTrue(failed)
        self.assertEqual(self.user_config.read_bytes(), original)
        self.assertFalse(self.manager.settings_path.exists())
        self.assertFalse(self.manager.state_path.exists())
        self.assertTrue(self.manager.backup_path.is_file())

    def test_status_is_read_only_and_detects_external_edit(self):
        self.assertEqual(self.manager.status()["status"], "NOT_CONFIGURED")
        before = self.user_config.read_bytes()
        self.manager.apply(self.settings(gaps=8))
        managed = self.manager.settings_path.read_bytes()
        self.manager.settings_path.write_bytes(managed + b"// external edit\n")
        edited = self.manager.settings_path.read_bytes()
        status = self.manager.status()
        self.assertEqual(status["status"], "WARNING")
        self.assertEqual(self.user_config.read_bytes(), before + b"\n" + niri_settings._user_include_block(self.manager.settings_path).encode() + b"\n")
        self.assertEqual(self.manager.settings_path.read_bytes(), edited)

    def test_symlinked_xdg_directory_or_settings_file_is_rejected(self):
        alias = self.home / "config-link"
        alias.symlink_to(self.config_home, target_is_directory=True)
        unsafe = niri_settings.NiriSettingsManager(
            home=self.home,
            config_home=alias,
            state_home=self.state_home,
            system_config=self.system_config,
            environment=self.environment,
            uid=os.getuid(),
            validator=lambda _path: (True, ""),
        )
        with self.assertRaises(niri_settings.NiriSettingsError):
            unsafe.apply(self.settings())
        target = self.config_home / "niri-plus"
        target.mkdir(mode=0o700)
        outside = self.home / "outside.kdl"
        outside.write_text("unchanged\n", encoding="utf-8")
        (target / "settings.kdl").symlink_to(outside)
        with self.assertRaises(niri_settings.NiriSettingsError):
            self.manager.apply(self.settings(gaps=11))
        self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged\n")

    def test_rejects_config_outside_supported_roots(self):
        outside = self.root / "unmanaged.kdl"
        outside.write_text("layout { gaps 6 }\n", encoding="utf-8")
        self.manager.environment["NIRI_CONFIG"] = str(outside)
        with self.assertRaisesRegex(niri_settings.NiriSettingsError, "fora das configurações"):
            self.manager.apply(self.settings())

    def test_bounded_newline_json_stdin_protocol(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = niri_settings.apply_from_stdin(self.manager, stream=io.StringIO(json.dumps(self.settings(gaps=17)) + "\n"))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["settings"]["gaps"], 17)
        too_large = io.StringIO()
        with contextlib.redirect_stdout(too_large):
            code = niri_settings.apply_from_stdin(self.manager, stream=io.StringIO(" " * 65537 + "\n"))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
