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
        return {
            **niri_settings.DEFAULTS,
            "touchpad": dict(niri_settings.TOUCHPAD_DEFAULTS),
            **changes,
        }

    def test_generated_kdl_parses_and_has_only_allowlisted_values(self):
        result = niri_settings.render_kdl(self.settings(gaps=12, border_enabled=True))
        self.assertIsNotNone(kdl.parse(result))
        self.assertIn("gaps 12", result)
        self.assertNotIn("touchpad {", result)
        self.assertIn('Mod+Space { spawn "fuzzel"; }', result)
        self.assertIn("Mod+Q { close-window; }", result)
        self.assertIn("Mod+H { focus-column-left; }", result)
        self.assertIn("Mod+L { focus-column-right; }", result)
        self.assertIn("Mod+K { focus-window-up; }", result)
        self.assertIn("Mod+J { focus-window-down; }", result)
        self.assertIn("Mod+Shift+H { move-column-left; }", result)
        self.assertIn("Mod+Shift+L { move-column-right; }", result)
        self.assertIn("Mod+V { toggle-window-floating; }", result)
        with self.assertRaises(niri_settings.NiriSettingsError):
            niri_settings.render_kdl(self.settings(launcher_key='Mod+Space; exec "bad"'))
        with self.assertRaises(niri_settings.NiriSettingsError):
            niri_settings.render_kdl(self.settings(launcher_key="Mod+Return", terminal_key="Mod+Return"))

    def test_close_window_shortcut_is_allowlisted_and_cannot_collide(self):
        rendered = niri_settings.render_kdl(self.settings(close_window_key="Mod+Shift+W"))
        self.assertIsNotNone(kdl.parse(rendered))
        self.assertIn("Mod+Shift+W { close-window; }", rendered)
        for value in ('Mod+Q; quit', "Mod+Return", "Ctrl+Q", "Mod+H"):
            with self.subTest(shortcut=value), self.assertRaises(niri_settings.NiriSettingsError):
                niri_settings.render_kdl(self.settings(close_window_key=value))

    def test_niri_focus_shortcuts_are_allowlisted_and_collision_checked(self):
        rendered = niri_settings.render_kdl(self.settings(
            focus_column_left_key="Mod+Left", focus_window_down_key="Mod+Down"
        ))
        self.assertIsNotNone(kdl.parse(rendered))
        self.assertIn("Mod+Left { focus-column-left; }", rendered)
        self.assertIn("Mod+Down { focus-window-down; }", rendered)
        with self.assertRaisesRegex(niri_settings.NiriSettingsError, "atalhos.*distintos"):
            niri_settings.render_kdl(self.settings(focus_window_up_key="Mod+Space"))
        with self.assertRaises(niri_settings.NiriSettingsError):
            niri_settings.render_kdl(self.settings(focus_column_right_key="Ctrl+L"))

    def test_move_and_floating_shortcuts_are_allowlisted(self):
        rendered = niri_settings.render_kdl(self.settings(
            move_column_left_key="Mod+Shift+Left", toggle_floating_key="Mod+Shift+V"
        ))
        self.assertIsNotNone(kdl.parse(rendered))
        self.assertIn("Mod+Shift+Left { move-column-left; }", rendered)
        self.assertIn("Mod+Shift+V { toggle-window-floating; }", rendered)
        with self.assertRaises(niri_settings.NiriSettingsError):
            niri_settings.render_kdl(self.settings(move_column_right_key="Mod+Space"))

    def test_touchpad_controls_emit_official_boolean_options_only_when_managed(self):
        touchpad = {
            "managed": True,
            "tap": True,
            "natural_scroll": False,
            "disabled_on_external_mouse": True,
        }
        rendered = niri_settings.render_kdl(self.settings(touchpad=touchpad))
        self.assertIsNotNone(kdl.parse(rendered))
        self.assertIn("touchpad {", rendered)
        self.assertIn("tap\n", rendered)
        self.assertIn("natural-scroll false", rendered)
        self.assertIn("disabled-on-external-mouse\n", rendered)
        for invalid in (None, {**touchpad, "unknown": True}, {**touchpad, "tap": 1}):
            with self.subTest(touchpad=invalid), self.assertRaises(niri_settings.NiriSettingsError):
                niri_settings.render_kdl(self.settings(touchpad=invalid))

    def test_touchpad_settings_refuse_existing_blocks_and_preserve_files(self):
        original_config = self.user_config.read_bytes()
        original_extra = self.niri_dir.joinpath("extra.kdl")
        original_extra.write_text("input { touchpad { tap } }\n", encoding="utf-8")
        touchpad = {**niri_settings.TOUCHPAD_DEFAULTS, "managed": True, "tap": True}
        with self.assertRaisesRegex(niri_settings.NiriSettingsError, "já está configurado fora"):
            self.manager.apply(self.settings(touchpad=touchpad))
        self.assertEqual(self.user_config.read_bytes(), original_config)
        self.assertEqual(original_extra.read_text(encoding="utf-8"), "input { touchpad { tap } }\n")
        self.assertFalse(self.manager.settings_path.exists())
        self.assertFalse(self.manager.state_path.exists())

    def test_touchpad_settings_refuse_uninspectable_absolute_includes(self):
        self.user_config.write_text('include optional=true "/etc/niri/custom.kdl"\n', encoding="utf-8")
        touchpad = {**niri_settings.TOUCHPAD_DEFAULTS, "managed": True}
        with self.assertRaisesRegex(niri_settings.NiriSettingsError, "includes absolutos"):
            self.manager.apply(self.settings(touchpad=touchpad))
        self.assertFalse(self.manager.settings_path.exists())

    def test_window_rules_are_exact_bounded_and_emit_valid_kdl(self):
        rules = [{"app_id": "org.mozilla.firefox", "workspace": "web apps", "floating": True}]
        rendered = niri_settings.render_kdl(self.settings(window_rules=rules))
        self.assertIsNotNone(kdl.parse(rendered))
        self.assertIn(r'match app-id="^org\\.mozilla\\.firefox$"', rendered)
        self.assertIn('open-on-workspace "web apps"', rendered)
        self.assertIn("open-floating true", rendered)
        invalid = (
            [{"app_id": "bad app; exec", "workspace": "web", "floating": False}],
            [{"app_id": "com.example.app", "workspace": "bad; exec", "floating": False}],
            [{"app_id": "com.example.app", "workspace": "web", "floating": 1}],
            [{"app_id": "com.example.app", "workspace": "web", "floating": False}] * 2,
            [{"app_id": f"app{i}", "workspace": "web", "floating": False}
             for i in range(niri_settings.MAX_WINDOW_RULES + 1)],
        )
        for ruleset in invalid:
            with self.subTest(rules=ruleset):
                with self.assertRaises(niri_settings.NiriSettingsError):
                    niri_settings.render_kdl(self.settings(window_rules=ruleset))

    def test_state_loader_migrates_legacy_settings_without_window_rules(self):
        legacy = dict(niri_settings.DEFAULTS)
        legacy.pop("window_rules")
        legacy.pop("touchpad")
        self.manager.state_dir.mkdir(mode=0o700, parents=True)
        state = {
            "schema_version": niri_settings.SCHEMA_VERSION,
            "settings": legacy,
            "previous_settings": legacy,
            "settings_sha256": "0" * 64,
        }
        self.manager.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.manager.state_path.chmod(0o600)
        migrated = self.manager._load_state()
        self.assertEqual(migrated["settings"]["window_rules"], [])
        self.assertEqual(migrated["previous_settings"]["window_rules"], [])

    def test_state_loader_adds_default_shortcut_to_pre_feature_state(self):
        legacy = dict(niri_settings.DEFAULTS)
        legacy.pop("window_rules")
        legacy.pop("touchpad")
        legacy.pop("close_window_key")
        legacy.pop("focus_column_left_key")
        legacy.pop("focus_column_right_key")
        legacy.pop("focus_window_up_key")
        legacy.pop("focus_window_down_key")
        legacy.pop("move_column_left_key")
        legacy.pop("move_column_right_key")
        legacy.pop("toggle_floating_key")
        self.manager.state_dir.mkdir(mode=0o700, parents=True)
        self.manager.state_path.write_text(json.dumps({
            "schema_version": niri_settings.SCHEMA_VERSION,
            "settings": legacy,
            "previous_settings": legacy,
            "settings_sha256": "0" * 64,
        }), encoding="utf-8")
        self.manager.state_path.chmod(0o600)
        migrated = self.manager._load_state()
        self.assertEqual(migrated["settings"]["close_window_key"], "Mod+Q")
        self.assertEqual(migrated["previous_settings"]["close_window_key"], "Mod+Q")
        self.assertEqual(migrated["settings"]["focus_column_left_key"], "Mod+H")
        self.assertEqual(migrated["settings"]["move_column_left_key"], "Mod+Shift+H")

    def test_close_window_only_state_migrates_navigation_defaults(self):
        close_only = self.settings()
        for key in niri_settings._NIRI_ACTION_SHORTCUT_FIELDS:
            close_only.pop(key)
        migrated = niri_settings.validate_settings(close_only)
        self.assertEqual(migrated["focus_column_left_key"], "Mod+H")
        self.assertEqual(migrated["focus_window_down_key"], "Mod+J")

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

    def test_window_rule_updates_persist_and_rollback_as_one_transaction(self):
        firefox = [{"app_id": "org.mozilla.firefox", "workspace": "web", "floating": False}]
        chat = [{"app_id": "org.telegram.desktop", "workspace": "chat", "floating": True}]
        self.manager.apply(self.settings(window_rules=firefox))
        self.manager.apply(self.settings(window_rules=chat))
        self.assertIn('open-on-workspace "chat"', self.manager.settings_path.read_text(encoding="utf-8"))
        restored = self.manager.rollback()
        self.assertEqual(restored["settings"]["window_rules"], firefox)
        installed = self.manager.settings_path.read_text(encoding="utf-8")
        self.assertIn('open-on-workspace "web"', installed)
        self.assertNotIn('open-on-workspace "chat"', installed)
        self.assertEqual(self.manager.status()["status"], "OK")

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
