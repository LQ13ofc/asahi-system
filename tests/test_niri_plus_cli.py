import contextlib
import io
import json
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from niri_plus import benchmark, cli, doctor, host, install_command, quickshell, rollback, source_update, status


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

    def test_status_overall_never_ignores_failed_niri_config_validation(self):
        report = {
            "fedora_asahi": "OK", "architecture": "aarch64", "fedora_version": "44",
            "niri": {"status": "OK"}, "session": "OK", "session_package": "OK",
            "configuration": "OK", "launcher": "OK", "configuration_validation": "OK",
            "launcher_binary": "OK", "install_state": "OK", "rollback_state": "OK",
            "source_checkout": {"status": "OK"}, "quickshell": {"status": "OK", "processes": {"count": 1, "managed_count": 1}},
            "core": {"all": "OK"}, "system": {"all": "OK"},
            "managed_file_checksums": {}, "kde_isolation": ("OK", []),
        }
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
            filters = (
                "app-org.kde.discover.notifier@autostart.service",
                "app-org.kde.kalendarac@autostart.service",
                "app-org.kde.kdeconnect.daemon@autostart.service",
                "app-org.kde.xwaylandvideobridge@autostart.service",
            )
            for unit in filters:
                dropin = root / "usr/lib/systemd/user" / f"{unit}.d/10-niri-session.conf"
                dropin.parent.mkdir(parents=True, exist_ok=True)
                dropin.write_text("[Unit]\nConditionEnvironment=XDG_CURRENT_DESKTOP=KDE\n")
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
            report = doctor.render_doctor(root=root, machine="x86_64", runner=missing_runner)
            self.assertIn("read-only", report)
            self.assertIn("M1_REQUIRED", report)
            self.assertEqual(before, list(root.rglob("*")))

    def test_status_includes_quickshell_pin_and_lifecycle_without_mutations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "etc").mkdir()
            (root / "etc/os-release").write_text('ID=fedora\nVERSION_ID="44"\nPRETTY_NAME="Fedora Cloud"\n')
            before = sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None) for p in root.rglob("*"))
            output = status.render_status("0.1.0", root=root, machine="x86_64", runner=missing_runner)
            after = sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None) for p in root.rglob("*"))
            self.assertIn("Quickshell", output)
            self.assertIn("Expected commit", output)
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

    def test_benchmark_forwards_arguments_to_existing_collector(self):
        calls = []
        def runner(args, check=False):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)
        with mock.patch.object(benchmark, "data_dir", return_value=ROOT):
            self.assertEqual(benchmark.run_benchmark(5, "capture.json", runner), 0)
        self.assertEqual(calls, [[sys.executable, str(ROOT / "scripts/collect-performance-baseline"),
                                  "--runs", "5", "--output", "capture.json"]])

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

    def test_source_update_ff_only_sequence_reaches_cached_origin_main(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            remote = root / "remote.git"
            seed = root / "seed"
            checkout = root / "checkout"
            subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "--initial-branch=main", str(seed)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(seed), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(seed), "config", "user.name", "Test"], check=True)
            (seed / "VERSION").write_text("0.1.1\n")
            subprocess.run(["git", "-C", str(seed), "add", "VERSION"], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-qm", "initial"], check=True)
            subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(seed), "push", "-qu", "origin", "main"], check=True)
            subprocess.run(["git", "clone", "-q", str(remote), str(checkout)], check=True)
            # A user-owned checkout can contain executable hooks and a
            # repository-local fsmonitor command. Source refresh must never
            # execute either while invoked by `sudo niri+ install`.
            hook_marker = root / "post-merge-ran"
            hook_dir = checkout / ".git/hooks"
            hook_dir.mkdir(exist_ok=True)
            post_merge = hook_dir / "post-merge"
            post_merge.write_text(f"#!/bin/sh\nprintf ran > {hook_marker}\n")
            post_merge.chmod(0o755)
            fsmonitor_marker = root / "fsmonitor-ran"
            fsmonitor = root / "fsmonitor-helper"
            fsmonitor.write_text(f"#!/bin/sh\nprintf ran > {fsmonitor_marker}\nprintf 'token\\n'\n")
            fsmonitor.chmod(0o755)
            subprocess.run(["git", "-C", str(checkout), "config", "core.fsmonitor", str(fsmonitor)], check=True)
            (seed / "VERSION").write_text("0.1.2\n")
            subprocess.run(["git", "-C", str(seed), "add", "VERSION"], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-qm", "update"], check=True)
            subprocess.run(["git", "-C", str(seed), "push", "-qu", "origin", "main"], check=True)
            head = source_update.refresh_source(checkout, expected_repository=str(remote))
            current = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
            self.assertEqual(head, current)
            self.assertFalse(hook_marker.exists(), "source update must disable checkout Git hooks")
            self.assertFalse(fsmonitor_marker.exists(), "source update must disable repository-local fsmonitor")

    def test_privileged_git_probe_runs_as_checkout_owner_with_clean_git_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = pathlib.Path(temp) / "checkout"
            checkout.mkdir()
            captured = {}

            def runner(args, **kwargs):
                captured.update(kwargs)
                captured["args"] = args
                return subprocess.CompletedProcess(args, 0, stdout="clean\n", stderr="")

            with mock.patch.object(source_update.os, "geteuid", return_value=0), \
                 mock.patch.dict(source_update.os.environ, {"GIT_TRACE": "/tmp/should-not-leak"}):
                result = source_update._run_as_owner(
                    checkout, ["git", "status"], runner, check=False, timeout=4
                )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(captured["user"], checkout.stat().st_uid)
            self.assertEqual(captured["env"]["HOME"], str(pathlib.Path.home()))
            self.assertNotIn("GIT_CONFIG_NOSYSTEM", captured["env"])
            self.assertNotIn("GIT_TRACE", captured["env"])

            bootstrap = runpy.run_path(str(ROOT / "scripts/bootstrap-niri-plus"))
            with mock.patch.object(bootstrap["os"], "geteuid", return_value=0), \
                 mock.patch.object(bootstrap["subprocess"], "run",
                                  return_value=subprocess.CompletedProcess([], 0, stdout="ok", stderr="")) as run:
                bootstrap["git_as_checkout_owner"](checkout, "status")
            self.assertEqual(run.call_args.kwargs["user"], checkout.stat().st_uid)

    def test_reexec_failure_is_reported_without_retrying(self):
        with mock.patch.object(source_update.os, "execve", side_effect=FileNotFoundError("missing CLI")):
            with self.assertRaisesRegex(source_update.SourceUpdateError, "could not be restarted"):
                source_update.reexec_install()

    def test_source_update_discovers_checkout_from_bootstrap_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            source = root / "asahi-system"
            quickshell = source / "external/quickshell"
            quickshell.mkdir(parents=True)
            state = root / "bootstrap.json"
            state.write_text(json.dumps({"quickshell_source": str(quickshell)}))
            self.assertEqual(source_update.discover_source_root(state), source.resolve())

    def test_source_update_rejects_wrong_origin_before_pull(self):
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / "repo"
            source.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", "https://example.invalid/not-asahi.git"], check=True)
            with self.assertRaisesRegex(source_update.SourceUpdateError, "unexpected origin"):
                source_update.refresh_source(source)

    def test_source_update_refuses_dirty_tree_before_pull(self):
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / "repo"
            source.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
            (source / "VERSION").write_text("0.1.1\n")
            subprocess.run(["git", "-C", str(source), "add", "VERSION"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "initial"], check=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", "https://github.com/LQ13ofc/asahi-system.git"], check=True)
            (source / "dirty").write_text("local")
            with self.assertRaisesRegex(source_update.SourceUpdateError, "local changes"):
                source_update.refresh_source(source)

    def test_bootstrap_requires_clean_official_main_at_cached_remote_head(self):
        bootstrap = runpy.run_path(str(ROOT / "scripts/bootstrap-niri-plus"))
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / "repo"
            source.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
            (source / "VERSION").write_text("0.1.1\n")
            subprocess.run(["git", "-C", str(source), "add", "VERSION"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "initial"], check=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", "git@github.com:LQ13ofc/asahi-system.git"], check=True)
            head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
            subprocess.run(["git", "-C", str(source), "update-ref", "refs/remotes/origin/main", head], check=True)
            validate_source = bootstrap["validate_system_source"]
            validate_source.__globals__["ROOT"] = source
            self.assertEqual(validate_source(), (head, "main"))
            (source / "dirty").write_text("pending")
            with self.assertRaisesRegex(RuntimeError, "local changes"):
                validate_source()

    def test_install_refreshes_and_reexecs_before_apply(self):
        with mock.patch.object(install_command.os, "geteuid", return_value=0), \
             mock.patch.object(install_command.install, "target_mismatches", return_value=[]), \
             mock.patch.object(install_command.source_update, "refresh_and_bootstrap", return_value="a" * 40) as refresh, \
             mock.patch.object(install_command.source_update, "reexec_install", side_effect=SystemExit(0)) as reexec:
            with self.assertRaises(SystemExit):
                install_command.run_install(dry_run=False)
        refresh.assert_called_once()
        reexec.assert_called_once()

    def test_incompatible_host_refuses_before_self_update(self):
        with mock.patch.object(install_command.os, "geteuid", return_value=0), \
             mock.patch.object(install_command.install, "parse_os_release", return_value={"ID": "fedora", "VERSION_ID": "44"}), \
             mock.patch.object(install_command.platform, "machine", return_value="x86_64"), \
             mock.patch.object(install_command.source_update, "refresh_and_bootstrap") as refresh, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(install_command.run_install(dry_run=False), 2)
        refresh.assert_not_called()
        self.assertIn("incompatible host", output.getvalue())

    def test_install_skips_refresh_after_reexec(self):
        with mock.patch.object(install_command.os, "geteuid", return_value=0), \
             mock.patch.dict(install_command.os.environ, {"NIRI_PLUS_SELF_UPDATED": "1"}), \
             mock.patch.object(install_command.install, "apply_install") as apply_install, \
             mock.patch.object(install_command.source_update, "refresh_and_bootstrap") as refresh:
            self.assertEqual(install_command.run_install(dry_run=False), 0)
        apply_install.assert_called_once()
        refresh.assert_not_called()

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
