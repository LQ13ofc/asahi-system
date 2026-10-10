from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from niri_plus import brightness


class BrightnessTests(unittest.TestCase):
    def make_device(self, root: pathlib.Path, name: str = "apple-panel-bl", maximum: int = 255):
        device = root / name
        device.mkdir(parents=True)
        (device / "max_brightness").write_text(f"{maximum}\n", encoding="ascii")
        return device

    def successful_runner(self, args, **kwargs):
        if args[-1] == "ListSessions":
            output = 'a(susso) 1 "4" 1000 "user" "seat0" "/org/freedesktop/login1/session/_4"'
        elif args[-1] == "Active":
            output = "b true"
        else:
            output = ""
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    def test_set_percent_uses_active_logind_session_and_never_runs_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            self.make_device(root)
            runner = mock.Mock(side_effect=self.successful_runner)
            result = brightness.set_percent(50, session_id="4", sysfs_root=root, runner=runner)
        self.assertEqual(result, ("apple-panel-bl", 128))
        calls = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(len(calls), 3)
        self.assertTrue(all("--system" in command and "sudo" not in command for command in calls))
        self.assertEqual(calls[-1][-5:], [
            "SetBrightness", "ssu", "backlight", "apple-panel-bl", "128"
        ])

    def test_inactive_logind_session_is_rejected_before_mutating_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            self.make_device(root)

            def runner(args, **kwargs):
                if args[-1] == "ListSessions":
                    output = 'a(susso) 1 "4" 1000 "user" "seat0" "/org/freedesktop/login1/session/_4"'
                else:
                    output = "b false"
                return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

            with self.assertRaisesRegex(brightness.BrightnessError, "sessão gráfica ativa"):
                brightness.set_percent(25, session_id="4", sysfs_root=root, runner=runner)

    def test_missing_session_device_and_invalid_device_fail_without_dbus_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            runner = mock.Mock(side_effect=self.successful_runner)
            with self.assertRaisesRegex(brightness.BrightnessError, "nenhum dispositivo"):
                brightness.set_percent(20, session_id="4", sysfs_root=root, runner=runner)
            self.make_device(root)
            with self.assertRaisesRegex(brightness.BrightnessError, "nome de dispositivo inválido"):
                brightness.set_percent(20, "../escape", session_id="4", sysfs_root=root, runner=runner)
            self.assertEqual(runner.call_count, 0)

    def test_multiple_backlights_require_explicit_device(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            self.make_device(root)
            self.make_device(root, "keyboard-led")
            with self.assertRaisesRegex(brightness.BrightnessError, "selecione um dispositivo"):
                brightness.set_percent(20, session_id="4", sysfs_root=root, runner=self.successful_runner)


if __name__ == "__main__":
    unittest.main()
