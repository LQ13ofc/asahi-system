import contextlib
import io
import os
import pathlib
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

from niri_plus import cli, gaming


class GamingModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.socket_path = pathlib.Path(self.temp.name) / "niri.sock"
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(self.socket_path))
        self.env = {
            "NIRI_SOCKET": str(self.socket_path),
            "WAYLAND_DISPLAY": "wayland-1",
            "XDG_CURRENT_DESKTOP": "niri",
            "PATH": "/mock/bin",
        }

    def tearDown(self):
        self.socket.close()
        self.temp.cleanup()

    def _which(self, command, path=None):
        return {"gamescope": "/mock/bin/gamescope", "steam": "/mock/bin/steam"}.get(command)

    def test_niri_session_requires_live_owned_socket_and_desktop_markers(self):
        self.assertTrue(gaming.niri_session_active(self.env))
        self.assertFalse(gaming.niri_session_active({**self.env, "XDG_CURRENT_DESKTOP": "KDE"}))
        self.assertFalse(gaming.niri_session_active({**self.env, "WAYLAND_DISPLAY": ""}))
        self.assertFalse(gaming.niri_session_active({**self.env, "NIRI_SOCKET": str(self.socket_path) + ".missing"}))
        self.assertFalse(gaming.niri_session_active(self.env, uid=os.getuid() + 1))

    def test_status_reports_readiness_without_launching_a_process(self):
        with mock.patch.object(gaming.shutil, "which", side_effect=self._which), \
             mock.patch.object(gaming.subprocess, "run") as run:
            state = gaming.inspect(self.env)
            rendered = gaming.render_status(self.env)
        self.assertEqual(state, {
            "niri_session": "OK",
            "gamescope": "/mock/bin/gamescope",
            "steam": "/mock/bin/steam",
            "gaming_mode": "AVAILABLE_UNVERIFIED",
        })
        self.assertIn("Fallback      disabled", rendered)
        self.assertIn("Runtime compatibility  M1_REQUIRED", rendered)
        run.assert_not_called()

    def test_launch_wraps_exact_argv_in_gamescope_without_shell_or_flags(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0)

        with mock.patch.object(gaming.os, "geteuid", return_value=1000), \
             mock.patch.object(gaming.shutil, "which", side_effect=self._which):
            result = gaming.launch(["--", "steam", "--game", "42"], env=self.env, runner=runner)
        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ["/mock/bin/gamescope", "--", "/mock/bin/steam", "--game", "42"])
        self.assertEqual(calls[0][1]["env"], self.env)
        self.assertNotIn("shell", calls[0][1])

    def test_missing_gamescope_fails_without_direct_niri_fallback(self):
        runner = mock.Mock()
        with mock.patch.object(gaming.os, "geteuid", return_value=1000), \
             mock.patch.object(gaming.shutil, "which", side_effect=lambda command, path=None:
                               "/mock/bin/steam" if command == "steam" else None):
            with self.assertRaisesRegex(gaming.GamingError, "no fallback was started"):
                gaming.launch(["steam"], env=self.env, runner=runner)
        runner.assert_not_called()

    def test_non_niri_and_root_sessions_refuse_before_launch(self):
        runner = mock.Mock()
        with mock.patch.object(gaming.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(gaming.GamingError, "requires an active Niri"):
                gaming.launch(["steam"], env={**self.env, "XDG_CURRENT_DESKTOP": "KDE"}, runner=runner)
        with mock.patch.object(gaming.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(gaming.GamingError, "never as root"):
                gaming.launch(["steam"], env=self.env, runner=runner)
        runner.assert_not_called()

    def test_failed_gamescope_is_reported_and_not_retried_directly(self):
        runner = mock.Mock(side_effect=OSError("incompatible display backend"))
        with mock.patch.object(gaming.os, "geteuid", return_value=1000), \
             mock.patch.object(gaming.shutil, "which", side_effect=self._which):
            with self.assertRaisesRegex(gaming.GamingError, "no direct-Niri fallback"):
                gaming.launch(["steam"], env=self.env, runner=runner)
        runner.assert_called_once()

    def test_cli_exposes_status_steam_and_arbitrary_command(self):
        with mock.patch.object(gaming, "render_status", return_value="simulated gaming status"), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["gaming", "status"]), 0)
        self.assertIn("simulated gaming status", output.getvalue())

        with mock.patch.object(gaming, "launch", return_value=7) as launch:
            self.assertEqual(cli.main(["gaming", "steam"]), 7)
        launch.assert_called_once_with(["steam"])

        with mock.patch.object(gaming, "launch", return_value=0) as launch:
            self.assertEqual(cli.main(["gaming", "run", "--", "game", "--fullscreen"]), 0)
        launch.assert_called_once_with(["--", "game", "--fullscreen"])

    def test_cli_reports_no_fallback_when_gamescope_is_unavailable(self):
        with mock.patch.object(gaming, "launch", side_effect=gaming.GamingError("Gamescope is unavailable; no fallback was started")), \
             contextlib.redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as exit_info:
                cli.main(["gaming", "steam"])
        self.assertEqual(exit_info.exception.code, 2)
        self.assertIn("no fallback was started", error.getvalue())


if __name__ == "__main__":
    unittest.main()
