import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from niri_plus import benchmark, cli, doctor, host, install_command, rollback, status


ROOT = pathlib.Path(__file__).resolve().parents[1]


class NiriPlusCliTests(unittest.TestCase):
    def test_help_and_version(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as help_exit:
            cli.main(["--help"])
        self.assertEqual(help_exit.exception.code, 0)
        self.assertIn("status", output.getvalue())
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as version_exit:
            cli.main(["--version"])
        self.assertEqual(version_exit.exception.code, 0)
        self.assertIn((ROOT / "VERSION").read_text().strip(), output.getvalue())

    def test_status_command_dispatches(self):
        with mock.patch.object(cli.status, "render_status", return_value="simulated status") as render, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["status"]), 0)
        render.assert_called_once()
        self.assertIn("simulated status", output.getvalue())

    def test_status_on_simulated_host_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            release = root / "etc/os-release"
            release.parent.mkdir()
            release.write_text('ID=fedora-asahi-remix\nVERSION_ID="44"\nPRETTY_NAME="Fedora Asahi Remix 44"\n')
            before = sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None) for p in root.rglob("*"))
            report = status.render_status("0.1.0", root=root, machine="aarch64", runner=missing_runner)
            after = sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None) for p in root.rglob("*"))
            self.assertIn("Fedora Asahi Remix 44 / aarch64", report)
            self.assertIn("Quickshell", report)
            self.assertEqual(before, after)

    def test_doctor_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            before = list(root.rglob("*"))
            report = doctor.render_doctor(root=root, machine="x86_64", runner=missing_runner)
            self.assertIn("read-only", report)
            self.assertIn("M1_REQUIRED", report)
            self.assertEqual(before, list(root.rglob("*")))

    def test_install_dry_run_does_not_require_root_or_mutate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            before = list(root.rglob("*"))
            with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(install_command.install, "parse_os_release", return_value={"ID": "fedora", "VERSION_ID": "44"}), mock.patch.object(install_command.platform, "machine", return_value="x86_64"):
                self.assertEqual(install_command.run_install(dry_run=True), 0)
            self.assertEqual(before, list(root.rglob("*")))

    def test_incompatible_host_is_rejected(self):
        self.assertEqual(install_command.install.target_mismatches({"ID": "fedora", "VERSION_ID": "44"}, "x86_64"),
                         ["OS ID must be fedora-asahi-remix", "architecture must be aarch64"])

    def test_install_files_are_idempotent_and_rollback_restores(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            target = install_command.install.prefixed(root, "/usr/share/wayland-sessions/niri-performance.desktop")
            target.parent.mkdir(parents=True)
            target.write_text("previous entry\n")
            _, first = install_command.install.install_files(root)
            state_path = install_command.install.prefixed(root, install_command.install.STATE_PATH)
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o644)
            _, second = install_command.install.install_files(root)
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertIn("Name=Niri", target.read_text())
            install_command.install.rollback_files(root)
            self.assertEqual(target.read_text(), "previous entry\n")

    def test_rollback_command_restores_managed_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            target = install_command.install.prefixed(root, "/usr/local/bin/asahi-niri-session")
            install_command.install.install_files(root)
            self.assertTrue(target.exists())
            self.assertEqual(rollback.run_rollback(root=root, require_root=False), 0)
            self.assertFalse(target.exists())

    def test_benchmark_forwards_arguments_to_existing_collector(self):
        calls = []
        def runner(args, check=False):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)
        with mock.patch.object(benchmark, "data_dir", return_value=ROOT):
            self.assertEqual(benchmark.run_benchmark(5, "capture.json", runner), 0)
        self.assertEqual(calls, [[sys.executable, str(ROOT / "scripts/collect-performance-baseline"),
                                  "--runs", "5", "--output", "capture.json"]])

    def test_desktop_display_name_is_exactly_niri(self):
        from configparser import ConfigParser
        parser = ConfigParser(interpolation=None)
        parser.read(ROOT / "sessions/niri.desktop", encoding="utf-8")
        self.assertEqual(parser["Desktop Entry"]["Name"], "Niri")


def missing_runner(args, **kwargs):
    return subprocess.CompletedProcess(args, 1, stdout="", stderr="not present")


if __name__ == "__main__":
    unittest.main()
