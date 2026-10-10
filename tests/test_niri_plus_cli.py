import contextlib
import io
import json
import pathlib
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from niri_plus import benchmark, brightness, cli, doctor, host, install, install_command, niri_settings, quickshell, rollback, source_update, status, wayland_ready


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

    def test_quickshell_candidate_compare_dispatches_without_host_mutation(self):
        with mock.patch.object(benchmark, "compare_quickshell_candidate_benchmarks", return_value=0) as compare_candidate:
            result = cli.main([
                "benchmark", "compare-quickshell",
                "--known-good", "c0.json",
                "--candidate", "c1.json",
                "--output", "report.md",
                "--allow-asahi-system-commit-change",
            ])
        self.assertEqual(result, 0)
        compare_candidate.assert_called_once_with(
            "c0.json", "c1.json", "report.md", None,
            allow_asahi_system_commit_change=True,
        )

    def test_status_command_dispatches(self):
        with mock.patch.object(cli.status, "render_status", return_value="simulated status") as render, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["status"]), 0)
        render.assert_called_once()
        self.assertIn("simulated status", output.getvalue())

    def test_brightness_command_uses_logind_without_root(self):
        with mock.patch.object(brightness, "set_percent", return_value=("apple-panel-bl", 50)) as set_percent, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["brightness", "set", "50", "--device", "apple-panel-bl"]), 0)
        set_percent.assert_called_once_with(50, "apple-panel-bl")
        self.assertIn("Brilho alterado para 50%", output.getvalue())

    def test_brightness_cli_rejects_invalid_range_before_dbus(self):
        with mock.patch.object(brightness, "set_percent") as set_percent, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_info:
                cli.main(["brightness", "set", "101"])
        self.assertEqual(exit_info.exception.code, 2)
        set_percent.assert_not_called()

    def test_niri_settings_cli_exposes_status_apply_rollback_and_uses_stdin(self):
        manager = mock.Mock()
        manager.status.return_value = {"status": "NOT_CONFIGURED", "configured": False}
        manager.rollback.return_value = {"status": "OK"}
        with mock.patch.object(niri_settings, "NiriSettingsManager", return_value=manager), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["niri-settings", "status"]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "NOT_CONFIGURED")
        manager.status.assert_called_once_with()
        with mock.patch.object(niri_settings, "NiriSettingsManager", return_value=manager), \
             mock.patch.object(niri_settings, "apply_from_stdin", return_value=0) as apply_stdin:
            self.assertEqual(cli.main(["niri-settings", "apply"]), 0)
        apply_stdin.assert_called_once_with(manager)
        with mock.patch.object(niri_settings, "NiriSettingsManager", return_value=manager), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["niri-settings", "rollback"]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "OK")

    def test_niri_settings_cli_help_lists_only_supported_actions(self):
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as exit_info:
            cli.main(["niri-settings", "--help"])
        self.assertEqual(exit_info.exception.code, 0)
        for action in ("status", "apply", "rollback"):
            self.assertIn(action, output.getvalue())

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

    def test_status_overall_never_ignores_failed_niri_config_validation(self):
        report = {
            "fedora_asahi": "OK", "architecture": "aarch64", "fedora_version": "44",
            "niri": {"status": "OK"}, "session": "OK", "session_package": "OK",
            "configuration": "OK", "launcher": "OK", "configuration_validation": "OK",
            "launcher_binary": "OK", "install_state": "OK", "rollback_state": "OK",
            "source_checkout": {"status": "OK"}, "quickshell": {"status": "OK", "processes": {"count": 1, "managed_count": 1}},
            "core": {"all": "OK"}, "system": {"all": "OK"},
            "managed_file_checksums": {}, "kde_isolation": ("OK", []),
            "wayland_readiness": host.OK, "wayland_clients_runtime": ("OK", []),
        }
        self.assertEqual(status.overall_state(report, niri_active=True), "M1_REQUIRED")
        report["quickshell"].update(status=host.NOT_CONFIGURED, state_pin_status=host.NOT_CONFIGURED,
                                    processes={"count": 0, "managed_count": 0})
        self.assertEqual(status.overall_state(report, niri_active=True), "M1_REQUIRED")
        report["configuration_validation"] = host.WARNING
        self.assertEqual(status.overall_state(report, niri_active=True), "WARNING")

    def test_fuzzel_binary_probe_distinguishes_missing_from_present(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            binary = root / "usr/bin/fuzzel"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
            self.assertEqual(host.command_version("fuzzel", lambda args, **kw: subprocess.CompletedProcess(args, 0, "fuzzel 1.0\n", ""), root)[0], host.OK)
            self.assertEqual(host.command_version("fuzzel", missing_runner, root)[0], host.WARNING)

    def test_kde_autostart_scope_and_process_detector(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "host"
            proc = pathlib.Path(temp) / "proc"
            proc.mkdir()
            for unit in (*install.KDE_SESSION_ONLY_UNITS, *install.KDE_AUTOSTART_UNITS):
                dropin = root / "usr/lib/systemd/user" / f"{unit}.d/10-niri-session.conf"
                dropin.parent.mkdir(parents=True, exist_ok=True)
                dropin.write_text("[Unit]\nConditionEnvironment=XDG_CURRENT_DESKTOP=KDE\nPartOf=graphical-session.target\n")
            def runner(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            with mock.patch.dict("os.environ", {"NIRI_SOCKET": "/tmp/niri-test.sock", "XDG_CURRENT_DESKTOP": "niri"}):
                self.assertEqual(host.kde_isolation_status(root, runner, proc), (host.OK, []))
                service = "app-org.kde.kalendarac@autostart.service loaded active running Calendar Reminders\n"
                def active_kde_runner(args, **kwargs):
                    return subprocess.CompletedProcess(args, 0, stdout=service, stderr="")
                state, names = host.kde_isolation_status(root, active_kde_runner, proc)
                self.assertEqual(state, host.WARNING)
                self.assertIn("app-org.kde.kalendarac@autostart.service", names)
                bridge = proc / "123"
                bridge.mkdir()
                (bridge / "cmdline").write_bytes(b"/usr/bin/xwaylandvideobridge\0")
                (bridge / "comm").write_text("xwaylandvideobridge\n")
                state, names = host.kde_isolation_status(root, runner, proc)
                self.assertEqual(state, host.WARNING)
                self.assertIn("process:xwaylandvideobridge", names)

                bridge.rename(proc / "old")
                shutil.rmtree(proc / "old")
                agent = proc / "124"
                agent.mkdir()
                (agent / "cmdline").write_bytes(b"/usr/libexec/akonadi_maildispatcher_agent\0")
                (agent / "comm").write_text("akonadi_maildispatcher_agent\n")
                state, names = host.kde_isolation_status(root, runner, proc)
                self.assertEqual(state, host.WARNING)
                self.assertIn("process:akonadi_maildispatcher_agent", names)

                shutil.rmtree(agent)
                for pid, process_name in (("125", "plasma-keyboard"), ("126", "org_kde_powerdevil"),
                                          ("127", "polkit-kde-authentication-agent-1"),
                                          ("128", "kwin_wayland_wrapper"),
                                          ("129", "startplasma-wayland")):
                    entry = proc / pid
                    entry.mkdir()
                    (entry / "cmdline").write_bytes(f"/usr/libexec/{process_name}\0".encode())
                    (entry / "comm").write_text(process_name[:15] + "\n")
                state, names = host.kde_isolation_status(root, runner, proc)
                self.assertEqual(state, host.WARNING)
                for process in ("plasma-keyboard", "org_kde_powerdevil", "polkit-kde-auth",
                                "kwin_wayland", "startplasma"):
                    self.assertIn(f"process:{process}", names)

    def test_kde_isolation_keeps_shared_wallet_and_open_konsole_out_of_warnings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "host"
            proc = pathlib.Path(temp) / "proc"
            proc.mkdir()
            for unit in (*install.KDE_SESSION_ONLY_UNITS, *install.KDE_AUTOSTART_UNITS):
                dropin = root / "usr/lib/systemd/user" / f"{unit}.d/10-niri-session.conf"
                dropin.parent.mkdir(parents=True, exist_ok=True)
                dropin.write_text("[Unit]\nConditionEnvironment=XDG_CURRENT_DESKTOP=KDE\nPartOf=graphical-session.target\n")

            active_shared_units = (
                "app-org.kde.konsole@session.service loaded active running User-opened Konsole\n"
                "dbus-org.kde.kwalletd6.service loaded active running KDE Wallet D-Bus service\n"
            )

            def runner(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, stdout=active_shared_units, stderr="")

            with mock.patch.dict("os.environ", {"NIRI_SOCKET": "/tmp/niri-test.sock", "XDG_CURRENT_DESKTOP": "niri"}):
                self.assertEqual(host.kde_isolation_status(root, runner, proc), (host.OK, []))
            with mock.patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "KDE"}, clear=True):
                self.assertEqual(host.kde_isolation_status(root, runner, proc), ("NOT_APPLICABLE", []))

    def test_wayland_runtime_doctor_detects_first_start_restarts(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = pathlib.Path(temp)
            socket_path = runtime / "wayland-0"
            wayland_socket = socket.socket(socket.AF_UNIX)
            wayland_socket.bind(str(socket_path))
            def runner(args, **kwargs):
                unit = next((item for item in args if item.endswith((".target", ".service"))), "")
                if unit == "graphical-session.target":
                    output = "ActiveState=active\n"
                else:
                    restarts = "1" if unit == "asahi-quickshell.service" else "0"
                    substate = "exited" if unit == "asahi-niri-wayland-ready.service" else "running"
                    output = f"ActiveState=active\nSubState={substate}\nResult=success\nNRestarts={restarts}\n"
                return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")
            with mock.patch.object(host, "niri_session_active", return_value=True), \
                 mock.patch.object(wayland_ready, "_handshake", return_value=True), \
                 mock.patch.dict("os.environ", {"XDG_RUNTIME_DIR": str(runtime), "WAYLAND_DISPLAY": "wayland-0"}):
                state, problems = host.wayland_clients_runtime_status(runner)
            wayland_socket.close()
            self.assertEqual(state, host.WARNING)
            self.assertEqual(problems, ["asahi-quickshell.service restarted 1 times"])

    def test_quickshell_process_diagnostic_detects_unmanaged_duplicate_invocations(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = pathlib.Path(temp)
            for pid, command in (("1", "/usr/bin/qs --path /usr/local/share/niri-plus/quickshell/shell.qml"),
                                 ("2", "/usr/bin/qs -c barra")):
                entry = proc / pid
                entry.mkdir()
                (entry / "cmdline").write_bytes(b"\0".join(part.encode() for part in command.split()) + b"\0")
            info = quickshell.process_diagnostics(proc)
            self.assertEqual(info["count"], 2)
            self.assertEqual(info["managed_count"], 1)
            self.assertEqual(info["status"], host.WARNING)
            self.assertEqual(info["processes"][0]["pid"], 1)
            self.assertTrue(info["processes"][0]["managed_invocation"])
            self.assertFalse(info["processes"][1]["managed_invocation"])
            (proc / "1" / "cmdline").unlink()
            (proc / "1").rmdir()
            only_unmanaged = quickshell.process_diagnostics(proc)
            self.assertEqual(only_unmanaged["count"], 1)
            self.assertEqual(only_unmanaged["managed_count"], 0)
            self.assertEqual(only_unmanaged["status"], host.WARNING)

    def test_quickshell_restart_history_is_not_reported_healthy(self):
        details = subprocess.CompletedProcess([], 0, "Result=success\nNRestarts=2\nActiveState=active\n", "")
        state, detail = quickshell.active_unit_diagnostics("enabled", details)
        self.assertEqual(state, host.WARNING)
        self.assertIn("2 automatic restart", detail)

    def test_doctor_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            before = list(root.rglob("*"))
            with mock.patch.object(doctor.gaming, "render_status", return_value="Niri+ Gaming Mode\nGaming Mode   UNAVAILABLE\nFallback      disabled"):
                report = doctor.render_doctor(root=root, machine="x86_64", runner=missing_runner)
            self.assertIn("read-only", report)
            self.assertIn("M1_REQUIRED", report)
            self.assertIn("Fallback      disabled", report)
            self.assertEqual(before, list(root.rglob("*")))

    def test_status_includes_quickshell_pin_and_lifecycle_without_mutations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "etc").mkdir()
            (root / "etc/os-release").write_text('ID=fedora\nVERSION_ID="44"\nPRETTY_NAME="Fedora Cloud"\n')
            before = sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None) for p in root.rglob("*"))
            with mock.patch.object(status.gaming, "render_status", return_value="Niri+ Gaming Mode\nGaming Mode   UNAVAILABLE\nFallback      disabled"):
                output = status.render_status("0.1.0", root=root, machine="x86_64", runner=missing_runner)
            after = sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None) for p in root.rglob("*"))
            self.assertIn("Quickshell", output)
            self.assertIn("Expected commit", output)
            self.assertIn("Gaming Mode", output)
            self.assertIn("Fallback      disabled", output)
            self.assertIn("M1_REQUIRED", output)
            self.assertNotIn("Overall: HEALTHY", output)
            self.assertEqual(before, after)

    def test_install_dry_run_does_not_require_root_or_mutate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            before = list(root.rglob("*"))
            with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(install_command.install, "parse_os_release", return_value={"ID": "fedora", "VERSION_ID": "44"}), mock.patch.object(install_command.platform, "machine", return_value="x86_64"):
                self.assertEqual(install_command.run_install(dry_run=True), 0)
            self.assertEqual(before, list(root.rglob("*")))

    def test_default_install_cli_is_independent_of_visual_runtime(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["install", "--dry-run"]), 0)
        self.assertIn("Quickshell visual runtime: omitted", output.getvalue())
        self.assertNotIn("Quickshell visual pin:", output.getvalue())
        self.assertNotIn("asahi-quickshell.service", output.getvalue())

    def test_default_apply_does_not_request_the_visual_repository(self):
        with mock.patch.object(cli.install_command, "run_install", return_value=0) as run:
            self.assertEqual(cli.main(["install"]), 0)
        run.assert_called_once_with(False, include_quickshell=False)

    def test_optional_visual_integration_is_explicit_in_dry_run(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["install", "--dry-run", "--with-quickshell"]), 0)
        self.assertIn("Quickshell visual pin:", output.getvalue())
        self.assertIn("asahi-quickshell.service", output.getvalue())

    def test_niri_only_package_transaction_does_not_enable_visual_repository(self):
        with mock.patch.object(install.shutil, "which", return_value="/usr/bin/dnf"), \
             mock.patch.object(install.subprocess, "run") as run:
            install.install_locked_packages(None)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["dnf", "install", "-y", "--setopt=install_weak_deps=False"])
        self.assertNotIn("quickshell", command)
        self.assertFalse(any(value.startswith("--repofrompath=") for value in command))

    def test_unconfigured_visual_integration_is_not_a_doctor_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            report = quickshell.doctor_report(pathlib.Path(temp), runner=missing_runner, enabled=False)
        self.assertEqual(report["status"], host.NOT_CONFIGURED)
        self.assertEqual(report["lifecycle"], host.NOT_CONFIGURED)
        self.assertEqual(report["duplicate_processes"]["status"], host.NOT_CONFIGURED)

    def test_niri_only_install_status_does_not_require_visual_units(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            install.install_files(root, include_quickshell=False)
            state = install.load_state(root)
            state["applied_version"] = "0.1.10"
            state["quickshell"] = {"expected_commit": None, "known_good_commit": None}
            install.write_state(root, state)
            report = host.host_report(root, machine="aarch64", runner=missing_runner)
        self.assertEqual(report["quickshell"]["status"], host.NOT_CONFIGURED)
        self.assertEqual(report["wayland_readiness"], host.OK)
        self.assertNotIn("/usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service",
                         report["managed_links"])

    def test_incompatible_host_is_rejected(self):
        self.assertEqual(install_command.install.target_mismatches({"ID": "fedora", "VERSION_ID": "44"}, "x86_64"),
                         ["OS ID must be fedora-asahi-remix", "architecture must be aarch64"])

    def test_install_files_are_idempotent_and_rollback_restores(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            target = install_command.install.prefixed(root, "/etc/niri/config.kdl")
            target.parent.mkdir(parents=True)
            target.write_text("previous config\n")
            _, first = install_command.install.install_files(root)
            state_path = install_command.install.prefixed(root, install_command.install.STATE_PATH)
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o644)
            _, second = install_command.install.install_files(root)
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertIn('include "keybinds.kdl"', target.read_text())
            install_command.install.rollback_files(root)
            self.assertEqual(target.read_text(), "previous config\n")

    def test_rollback_command_restores_managed_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            target = install_command.install.prefixed(root, "/etc/niri/config.kdl")
            install_command.install.install_files(root)
            self.assertTrue(target.exists())
            self.assertEqual(rollback.run_rollback(root=root, require_root=False), 0)
            self.assertFalse(target.exists())

    def test_benchmark_forwards_validated_profile_and_window_to_existing_collector(self):
        calls = []
        def runner(args, check=False):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)
        with mock.patch.object(benchmark, "data_dir", return_value=ROOT):
            self.assertEqual(benchmark.run_benchmark("niri-quickshell", 5, 12.5, "capture.json", runner), 0)
        self.assertEqual(calls, [[sys.executable, str(ROOT / "scripts/collect-performance-baseline"),
                                  "--profile", "niri-quickshell", "--runs", "5", "--window", "12.5",
                                  "--output", "capture.json"]])

    def test_benchmark_overhead_forwards_to_separate_diagnostic_mode(self):
        calls = []
        def runner(args, check=False):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)
        with mock.patch.object(benchmark, "data_dir", return_value=ROOT):
            self.assertEqual(benchmark.run_overhead_diagnostic(3, "overhead.json", runner), 0)
        self.assertEqual(calls, [[sys.executable, str(ROOT / "scripts/collect-performance-baseline"),
                                  "--diagnose-overhead", "--runs", "3", "--output", "overhead.json"]])

    def test_memory_command_forwards_to_existing_collector_without_root_or_host_mutation(self):
        calls = []
        def runner(args, check=False):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)
        with mock.patch.object(benchmark, "data_dir", return_value=ROOT):
            self.assertEqual(benchmark.run_memory_diagnostic(
                "memory.json", 2, 0.5, series=True, include_60_minutes=True, runner=runner
            ), 0)
        self.assertEqual(calls, [[sys.executable, str(ROOT / "scripts/collect-performance-baseline"),
                                  "--memory-series", "--memory-window", "2", "--memory-sample-period", "0.5",
                                  "--include-60-minutes", "--output", "memory.json"]])

    def test_memory_cli_validates_series_flags_and_forwards_json(self):
        with mock.patch.object(benchmark, "run_memory_diagnostic", return_value=0) as memory_mock:
            self.assertEqual(cli.main(["memory", "--series", "--include-60-minutes",
                                       "--window", "2", "--sample-period", "0.5",
                                       "--json-output", "memory.json"]), 0)
        memory_mock.assert_called_once_with("memory.json", 2.0, 0.5, True, True)
        with self.assertRaises(SystemExit):
            cli.main(["memory", "--include-60-minutes"])

    def test_benchmark_compare_cli_forwards_three_profiles_offline(self):
        with mock.patch.object(benchmark, "compare_benchmarks", return_value=0) as compare_mock:
            self.assertEqual(cli.main([
                "benchmark", "compare",
                "--plasma", "plasma.json",
                "--niri-core", "niri-core.json",
                "--niri-quickshell", "niri-quickshell.json",
                "--output", "report.md",
                "--json-output", "report.json",
            ]), 0)
        compare_mock.assert_called_once_with(
            "plasma.json", "niri-core.json", "niri-quickshell.json", "report.md", "report.json"
        )

    def test_benchmark_requires_an_action_but_compare_is_a_subcommand(self):
        with self.assertRaises(SystemExit):
            cli.main(["benchmark"])

    def test_session_status_accepts_packaged_niri_and_rejects_legacy_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            packaged = root / "usr/share/wayland-sessions/niri.desktop"
            packaged.parent.mkdir(parents=True)
            packaged.write_text("[Desktop Entry]\nName=Niri\nType=Application\n")
            self.assertEqual(host.session_status(root), host.OK)
            legacy = root / "usr/share/wayland-sessions/niri-performance.desktop"
            legacy.write_text("[Desktop Entry]\nName=Niri\nType=Application\n")
            self.assertEqual(host.session_status(root), host.WARNING)
            legacy.unlink()
            local_duplicate = root / "usr/local/share/wayland-sessions/custom-session.desktop"
            local_duplicate.parent.mkdir(parents=True)
            local_duplicate.write_text(
                "[Desktop Entry]\nName=Niri Performance (B1)\n"
                "Exec=/usr/local/bin/asahi-niri-session\nType=Application\n"
            )
            self.assertEqual(host.session_status(root), host.WARNING)


    def test_configuration_includes_and_launcher_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            niri = root / "etc/niri"
            niri.mkdir(parents=True)
            for path in (ROOT / "niri").glob("*.kdl"):
                (niri / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(host.configuration_status(root), host.OK)
            self.assertEqual(host.launcher_status(root), host.OK)
            (niri / "keybinds.kdl").write_text('binds { Mod+D { spawn "fuzzel"; } }')
            self.assertEqual(host.launcher_status(root), host.WARNING)
            (niri / "rules.kdl").unlink()
            self.assertEqual(host.configuration_status(root), host.WARNING)

    def test_configuration_status_rejects_user_override_and_duplicate_includes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            niri = root / "etc/niri"
            niri.mkdir(parents=True)
            for path in (ROOT / "niri").glob("*.kdl"):
                (niri / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            config = niri / "config.kdl"
            config.write_text(config.read_text() + '\ninclude "keybinds.kdl"\n')
            self.assertEqual(host.configuration_status(root), host.WARNING)

    def test_configuration_status_warns_when_user_config_shadows_system_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            niri = root / "etc/niri"
            niri.mkdir(parents=True)
            for path in (ROOT / "niri").glob("*.kdl"):
                (niri / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            user_config = root / "home/.config/niri/config.kdl"
            user_config.parent.mkdir(parents=True)
            user_config.write_text("// shadows the managed system config\n")
            self.assertEqual(host.configuration_status(root, {"XDG_CONFIG_HOME": str(root / "home/.config")}), host.WARNING)

    def test_rollback_validates_all_backups_before_removing_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            target = install_command.install.prefixed(root, "/etc/niri/config.kdl")
            target.parent.mkdir(parents=True)
            target.write_text("user config\n")
            install_command.install.install_files(root)
            backup = install_command.install.prefixed(root, "/var/lib/asahi-system/niri-performance/backups/etc/niri/config.kdl")
            backup.unlink()
            with self.assertRaisesRegex(install_command.install.InstallError, "required backup"):
                install_command.install.rollback_files(root)
            self.assertIn('include "keybinds.kdl"', target.read_text())

    def test_rollback_preflights_all_preserved_edit_destinations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            install_command.install.install_files(root)
            config = install_command.install.prefixed(root, "/etc/niri/config.kdl")
            keys = install_command.install.prefixed(root, "/etc/niri/keybinds.kdl")
            config.write_text("user edit config\n")
            keys.write_text("user edit keys\n")
            collision = install_command.install.prefixed(root, "/var/lib/asahi-system/niri-performance/rollback-edits/etc/niri/keybinds.kdl")
            collision.parent.mkdir(parents=True)
            collision.write_text("keep me\n")
            with self.assertRaisesRegex(install_command.install.InstallError, "destination already exists"):
                install_command.install.rollback_files(root)
            self.assertEqual(config.read_text(), "user edit config\n")
            self.assertEqual(keys.read_text(), "user edit keys\n")

    def test_install_refuses_symlinked_managed_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "root"
            outside = pathlib.Path(temp) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "etc").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(install_command.install.InstallError, "symlinked parent"):
                install_command.install.install_files(root)
            self.assertEqual(list(outside.iterdir()), [])

    def test_install_state_rejects_symlink_and_status_detects_missing_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            state_path = install_command.install.prefixed(root, install_command.install.STATE_PATH)
            state_path.parent.mkdir(parents=True)
            foreign = root / "foreign.json"
            foreign.write_text('{"schema_version":1,"entries":{}}')
            state_path.symlink_to(foreign)
            self.assertEqual(host.installation_state(root)[0], host.WARNING)
            with self.assertRaisesRegex(install_command.install.InstallError, "state is a symlink"):
                install_command.install.load_state(root)
            state_path.unlink()
            broken = {"entries": {"/etc/niri/config.kdl": {"kind": "file", "backup": "/var/lib/asahi-system/niri-performance/backups/etc/niri/config.kdl"}}}
            self.assertEqual(host.rollback_state_status(root, broken), host.WARNING)

    def test_install_state_rejects_invalid_version_or_quickshell_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            state = {"schema_version": 1, "entries": {}, "applied_version": "latest",
                     "quickshell": {"expected_commit": "not-a-commit"}}
            install_command.install.write_state(root, state)
            with self.assertRaisesRegex(install_command.install.InstallError, "invalid applied_version"):
                install_command.install.load_state(root)
            state["applied_version"] = "0.1.2"
            install_command.install.write_state(root, state)
            with self.assertRaisesRegex(install_command.install.InstallError, "invalid Quickshell expected_commit"):
                install_command.install.load_state(root)

    def test_rollback_preserves_package_ownership_until_uninstall(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            state = {"schema_version": 1, "entries": {}, "packages_installed_by_us": ["niri", "fuzzel"]}
            install_command.install.write_state(root, state)
            install_command.install.rollback(root, remove_packages=False, require_root=False)
            saved = json.loads(install_command.install.prefixed(root, install_command.install.STATE_PATH).read_text())
            self.assertEqual(saved["entries"], {})
            self.assertEqual(saved["packages_installed_by_us"], ["niri", "fuzzel"])

    def test_status_does_not_call_legacy_applied_version_known_good(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            state_path = install_command.install.prefixed(root, install_command.install.STATE_PATH)
            install_command.install.write_state(root, {"schema_version": 1, "entries": {}, "known_good_version": "0.1.0"})
            result = host.host_report(root, machine="x86_64", runner=missing_runner)
            self.assertEqual(result["known_good"], host.NOT_CONFIGURED)
            self.assertEqual(result["install_state"], host.WARNING)
            self.assertTrue(any("health verification" in warning for warning in result["warnings"]))
            self.assertIn("known_good_version", json.loads(state_path.read_text()))

    def test_applied_install_requires_all_managed_files_and_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            state_path = install_command.install.prefixed(root, install_command.install.STATE_PATH)
            install_command.install.write_state(root, {
                "schema_version": 1,
                "applied_version": "0.1.1",
                "entries": {},
                "quickshell": {"expected_commit": "55e92880d0aff75d235f283c839ec0990eaa9e17"},
            })
            report = host.host_report(root, machine="x86_64", runner=missing_runner)
            self.assertEqual(report["managed_file_checksums"]["/etc/niri/config.kdl"], host.WARNING)
            self.assertEqual(report["managed_links"][
                "/usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service"
            ], host.WARNING)
            self.assertEqual(status.overall_state(report), "WARNING")
            self.assertTrue(state_path.is_file())

    def test_status_detects_state_pin_diverging_from_repository_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            state = {
                "schema_version": 1,
                "applied_version": "0.1.2",
                "entries": {},
                "quickshell": {"expected_commit": "0" * 40},
            }
            install_command.install.write_state(root, state)
            report = host.host_report(root, machine="aarch64", runner=missing_runner)
            self.assertEqual(report["quickshell"]["state_pin_status"], host.WARNING)
            self.assertEqual(report["quickshell"]["status"], host.WARNING)

    def test_quickshell_rpm_probe_renders_implicit_zero_epoch_canonically(self):
        calls = []

        def rpm_runner(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(
                args, 0, stdout="0:0.3.1-2.fc44.aarch64", stderr=""
            )

        state, version = quickshell._rpm_version(pathlib.Path("/"), rpm_runner)

        self.assertEqual(state, host.OK)
        self.assertEqual(version, "0:0.3.1-2.fc44.aarch64")
        self.assertEqual(
            calls,
            [[
                "rpm", "-q", "--qf",
                "%|EPOCH?{%{EPOCH}:}:{0:}|%{VERSION}-%{RELEASE}.%{ARCH}",
                "quickshell",
            ]],
        )

    def test_quickshell_rpm_lock_comparison_preserves_real_mismatches(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp) / "data"
            integration = base / "integration"
            integration.mkdir(parents=True)
            expected_commit = "0" * 40
            repository = "https://github.com/LQ13ofc/quickshell-.git"
            (integration / "quickshell.lock.json").write_text(json.dumps({
                "schema_version": 1,
                "repository": repository,
                "commit": expected_commit,
                "engine": {
                    "nevra": "quickshell-0:0.3.1-2.fc44",
                    "architecture": "aarch64",
                    "compatibility": "M1_REQUIRED",
                },
            }))

            checkout = pathlib.Path(temp) / "quickshell"
            checkout.mkdir()

            cases = {
                "explicit zero epoch": ("0:0.3.1-2.fc44.aarch64", host.OK),
                "non-zero epoch": ("1:0.3.1-2.fc44.aarch64", host.WARNING),
                "different version": ("0:0.3.2-2.fc44.aarch64", host.WARNING),
                "different release": ("0:0.3.1-3.fc44.aarch64", host.WARNING),
                "different arch": ("0:0.3.1-2.fc44.x86_64", host.WARNING),
            }

            for label, (installed, expected_status) in cases.items():
                with self.subTest(label=label), \
                     mock.patch.object(quickshell, "checkout_path", return_value=checkout), \
                     mock.patch.object(quickshell, "_runtime_snapshot", return_value={
                         "status": host.OK,
                         "repository": repository,
                         "commit": expected_commit,
                     }), \
                     mock.patch.object(quickshell, "_qs_version", return_value=(host.OK, "Quickshell 0.3.1")), \
                     mock.patch.object(quickshell, "_rpm_version", return_value=(host.OK, installed)), \
                     mock.patch.object(quickshell, "_unit_state", return_value=(host.OK, "active")), \
                     mock.patch.object(quickshell, "_runtime_link_status", return_value=host.OK):
                    info = quickshell.report(root=pathlib.Path(temp), base=base)
                self.assertEqual(info["package_status"], expected_status)

    def test_quickshell_report_detects_pinned_and_diverged_checkouts(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp) / "data"
            checkout = pathlib.Path(temp) / "quickshell"
            integration = base / "integration"
            integration.mkdir(parents=True)
            checkout.mkdir()
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Test"], check=True)
            (checkout / "shell.qml").write_text("// test shell\n")
            subprocess.run(["git", "-C", str(checkout), "add", "shell.qml"], check=True)
            subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "test pin"], check=True)
            commit = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
            subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", "https://github.com/LQ13ofc/quickshell-.git"], check=True)
            (integration / "quickshell.lock.json").write_text(json.dumps({"schema_version": 1, "repository": "https://github.com/LQ13ofc/quickshell-.git", "commit": commit, "engine": {"compatibility": "M1_REQUIRED"}}))
            root = pathlib.Path(temp) / "host"
            target = root / "usr/local/share/niri-plus/quickshell"
            target.parent.mkdir(parents=True)
            target.symlink_to(checkout, target_is_directory=True)
            bootstrap_state = root / "var/lib/niri-plus/bootstrap.json"
            bootstrap_state.parent.mkdir(parents=True)
            bootstrap_state.write_text(json.dumps({"quickshell_source": str(checkout)}))
            info = quickshell.report(root=root, base=base)
            self.assertEqual(info["checkout_status"], host.OK)
            self.assertEqual(info["status"], host.NOT_INSTALLED)
            self.assertEqual(info["runtime_link_status"], host.OK)
            target.unlink()
            target.symlink_to(base / "not-the-pinned-checkout", target_is_directory=True)
            self.assertEqual(quickshell.report(root=root, base=base)["status"], host.WARNING)
            target.unlink()
            target.symlink_to(checkout, target_is_directory=True)
            lock_path = integration / "quickshell.lock.json"
            lock = json.loads(lock_path.read_text())
            lock["commit"] = "f" * 40
            lock_path.write_text(json.dumps(lock))
            self.assertEqual(quickshell.report(root=root, base=base)["checkout_status"], host.WARNING)


def missing_runner(args, **kwargs):
    return subprocess.CompletedProcess(args, 1, stdout="", stderr="not present")


if __name__ == "__main__":
    unittest.main()
