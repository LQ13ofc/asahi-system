from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import pwd
import runpy
import socket
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from niri_plus import install_command, install_transaction, snapshot_install, source_update, wayland_ready


def git(args, **kwargs):
    command = ["git", *args]
    if "stdout" in kwargs or "stderr" in kwargs:
        return subprocess.run(command, check=True, text=True, **kwargs)
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


def make_repository(root: pathlib.Path, name: str):
    bare = root / f"{name}.git"
    seed = root / f"{name}-seed"
    bare.mkdir()
    seed.mkdir()
    git(["init", "--bare", "--initial-branch=main", str(bare)])
    git(["init", "--initial-branch=main", str(seed)])
    git(["-C", str(seed), "config", "user.name", "Release test"])
    git(["-C", str(seed), "config", "user.email", "release-test@example.invalid"])
    return bare, seed


def commit_all(seed: pathlib.Path, message: str) -> str:
    git(["-C", str(seed), "add", "-A"])
    git(["-C", str(seed), "commit", "-m", message])
    return git(["-C", str(seed), "rev-parse", "HEAD"]).stdout.strip()


class GitSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="niri-plus-git-test-")
        self.root = pathlib.Path(self.temp.name)
        self.qs_remote, self.qs_seed = make_repository(self.root, "quickshell")
        git(["-C", str(self.qs_seed), "remote", "add", "origin", self.qs_remote.as_uri()])
        (self.qs_seed / "shell.qml").write_text("// visual pin one\n")
        self.qs_commit = commit_all(self.qs_seed, "initial Quickshell")
        git(["-C", str(self.qs_seed), "push", "-u", "origin", "main"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.asahi_remote, self.asahi_seed = make_repository(self.root, "asahi")
        self.asahi_url = self.asahi_remote.as_uri()
        self.qs_url = self.qs_remote.as_uri()
        self._write_system_tree("0.1.1", self.qs_commit)
        self.initial_system_commit = commit_all(self.asahi_seed, "initial asahi-system")
        git(["-C", str(self.asahi_seed), "remote", "add", "origin", self.asahi_url])
        git(["-C", str(self.asahi_seed), "push", "-u", "origin", "main"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.checkout = self.root / "asahi-checkout"
        subprocess.run(["git", "clone", "--branch", "main", self.asahi_url, str(self.checkout)],
                       check=True, capture_output=True)

    def tearDown(self):
        self.temp.cleanup()

    def _write_system_tree(self, version: str, pin: str):
        (self.asahi_seed / "VERSION").write_text(version + "\n")
        if not (self.asahi_seed / "external/quickshell/.git").exists():
            git(["-c", "protocol.file.allow=always", "-C", str(self.asahi_seed),
                 "submodule", "add", "--force", self.qs_url, "external/quickshell"])
        (self.asahi_seed / "integration").mkdir(exist_ok=True)
        (self.asahi_seed / "integration/quickshell.lock.json").write_text(json.dumps({
            "schema_version": 1, "repository": self.qs_url,
            "checkout": "external/quickshell", "commit": pin,
        }, sort_keys=True) + "\n")
        (self.asahi_seed / "niri_plus").mkdir(exist_ok=True)
        (self.asahi_seed / "niri_plus/install_command.py").write_text(
            f"# privileged snapshot version {version}\n"
        )
        (self.asahi_seed / "scripts").mkdir(exist_ok=True)
        (self.asahi_seed / "scripts/install-snapshot").write_text(
            f"# trusted snapshot entrypoint for version {version}\n"
        )

    def _snapshot(self, **kwargs):
        return source_update.resolved_snapshot(
            self.checkout,
            expected_repository=self.asahi_url,
            expected_quickshell_repository=self.qs_url,
            allow_test_file_sources=True,
            **kwargs,
        )

    def test_environment_cannot_enable_unapproved_local_git_sources(self):
        with mock.patch.dict(os.environ, {"NIRI_PLUS_TEST_GIT_SOURCES": "1"}):
            with self.assertRaisesRegex(source_update.SourceUpdateError, "test Git source override is disabled"):
                with source_update.resolved_snapshot(
                    self.checkout,
                    expected_repository=self.asahi_url,
                    expected_quickshell_repository=self.qs_url,
                ):
                    pass

    def test_installed_cli_launcher_uses_isolated_python_mode(self):
        bootstrap = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[1]
                                       / "scripts/bootstrap-niri-plus"), run_name="bootstrap-test")
        self.assertTrue(bootstrap["launcher"]().startswith("#!/usr/bin/python3 -I\n"))

    def test_git_subprocess_uses_checkout_owner_identity_under_sudo(self):
        owner = pwd.getpwuid(self.checkout.stat().st_uid)
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        with mock.patch.object(source_update.os, "geteuid", return_value=0):
            source_update._run_as_owner(self.checkout, ["git", "status"], runner)
        args, kwargs = calls[0]
        self.assertEqual(kwargs["user"], owner.pw_uid)
        self.assertEqual(kwargs["group"], owner.pw_gid)
        self.assertEqual(kwargs["env"]["HOME"], owner.pw_dir)
        self.assertIn("--no-optional-locks", args)

    def test_checkout_credential_helpers_are_preserved_in_noninteractive_git_environment(self):
        git(["-C", str(self.checkout), "config", "--local", "--add", "credential.helper", "!printf owner-helper"])
        helpers = source_update._local_credential_helpers(self.checkout, self.asahi_url, subprocess.run)
        self.assertEqual(helpers, ["!printf owner-helper"])

        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            if "ls-remote" in args:
                ref = args[-1]
                return subprocess.CompletedProcess(args, 0, stdout=f"{self.initial_system_commit}\t{ref}\n".encode(), stderr=b"")
            return subprocess.run(args, **kwargs)

        fetch_store = self.root / "fetch.git"
        git(["init", "--bare", str(fetch_store)])
        source_update._fetch_commit(self.checkout, fetch_store, self.asahi_url,
                                    "refs/heads/main", runner, credential_helpers=helpers)
        git_calls = [(args, kwargs) for args, kwargs in calls
                     if args and args[0] == "git" and ("ls-remote" in args or "fetch" in args)]
        self.assertEqual(len(git_calls), 2)
        for args, kwargs in git_calls:
            env = kwargs["env"]
            configuration = {
                env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
                for index in range(int(env["GIT_CONFIG_COUNT"]))
            }
            self.assertEqual(configuration["credential.helper"], "!printf owner-helper")
            self.assertEqual(configuration["credential.interactive"], "false")
            self.assertNotIn("owner-helper", args)

    def test_github_ssh_and_https_spellings_share_only_the_approved_identity(self):
        canonical = "https://github.com/LQ13ofc/asahi-system.git"
        self.assertEqual(source_update._normalize_repository(canonical), "https://github.com/lq13ofc/asahi-system")
        self.assertEqual(source_update._normalize_repository("git@github.com:LQ13ofc/asahi-system.git"),
                         source_update._normalize_repository(canonical))
        self.assertEqual(source_update._normalize_repository("ssh://git@github.com/LQ13ofc/asahi-system.git"),
                         source_update._normalize_repository(canonical))
        self.assertEqual(source_update._normalize_repository("https://github.com/example/asahi-system.git"), "https://github.com/example/asahi-system")

    def test_install_uses_remote_commit_when_source_checkout_is_behind(self):
        self._write_system_tree("0.1.2", self.qs_commit)
        remote_commit = commit_all(self.asahi_seed, "release 0.1.2")
        git(["-C", str(self.asahi_seed), "push", "origin", "main"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self._snapshot() as snapshot:
            self.assertEqual(snapshot.system_commit, remote_commit)
            self.assertEqual((snapshot.root / "VERSION").read_text(), "0.1.2\n")
            self.assertEqual(snapshot.quickshell_commit, self.qs_commit)

    def test_install_with_source_already_at_remote_head_is_reproducible(self):
        with self._snapshot() as first:
            first_sha = first.system_commit
        with self._snapshot() as second:
            self.assertEqual(second.system_commit, first_sha)
            self.assertEqual(second.quickshell_commit, self.qs_commit)

    def test_wrong_origin_branch_and_dirty_source_are_rejected_before_fetch(self):
        git(["-C", str(self.checkout), "remote", "set-url", "origin", "https://example.invalid/wrong.git"])
        with self.assertRaisesRegex(source_update.SourceUpdateError, "unexpected configured origin"):
            with self._snapshot():
                pass
        git(["-C", str(self.checkout), "remote", "set-url", "origin", self.asahi_url])
        git(["-C", str(self.checkout), "switch", "-c", "feature"])
        with self.assertRaisesRegex(source_update.SourceUpdateError, "must be on main"):
            with self._snapshot():
                pass
        git(["-C", str(self.checkout), "switch", "main"])
        (self.checkout / "dirty.txt").write_text("keep me\n")
        with self.assertRaisesRegex(source_update.SourceUpdateError, "local changes"):
            with self._snapshot():
                pass
        self.assertEqual((self.checkout / "dirty.txt").read_text(), "keep me\n")

    def test_lockfile_and_gitlink_mismatch_fails_before_quickshell_fetch(self):
        lock_path = self.asahi_seed / "integration/quickshell.lock.json"
        lock = json.loads(lock_path.read_text())
        lock["commit"] = "0" * 40
        lock_path.write_text(json.dumps(lock, sort_keys=True) + "\n")
        commit_all(self.asahi_seed, "bad lock")
        git(["-C", str(self.asahi_seed), "push", "origin", "main"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self.assertRaisesRegex(source_update.SourceUpdateError, "gitlink and lockfile diverge"):
            with self._snapshot():
                pass

    def test_private_quickshell_auth_failure_is_fast_and_noninteractive(self):
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            if any("quickshell.git" in arg for arg in args) and "fetch" in args:
                raise subprocess.CalledProcessError(
                    128, args, output=b"", stderr=b"fatal: could not read Username for remote: terminal prompts disabled\n",
                )
            return subprocess.run(args, **kwargs)

        with self.assertRaisesRegex(source_update.SourceUpdateError, "private commit could not be fetched"):
            with self._snapshot(runner=runner):
                pass
        git_calls = [kwargs for args, kwargs in calls if args and args[0] == "git"]
        self.assertTrue(git_calls)
        for args, kwargs in calls:
            if not args or args[0] != "git":
                continue
            if kwargs.get("input") is None:
                self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            else:
                self.assertIsNone(kwargs["stdin"])
            self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
            self.assertEqual(kwargs["env"]["GCM_INTERACTIVE"], "never")
            self.assertTrue(kwargs["env"]["GIT_SSH_COMMAND"].endswith("-o BatchMode=yes"))
            self.assertNotIn("GIT_ASKPASS", kwargs["env"])
            self.assertEqual(kwargs["env"]["HOME"], str(pathlib.Path.home()))
            configuration = {
                kwargs["env"][f"GIT_CONFIG_KEY_{index}"]: kwargs["env"][f"GIT_CONFIG_VALUE_{index}"]
                for index in range(int(kwargs["env"]["GIT_CONFIG_COUNT"]))
            }
            self.assertEqual(configuration["credential.interactive"], "false")
            self.assertIn("--no-optional-locks", args)

    def test_public_install_never_reaches_apply_if_private_pin_auth_fails(self):
        error = source_update.SourceUpdateError(
            "Quickshell's pinned private commit could not be fetched non-interactively; no changes were applied."
        )
        with mock.patch.object(install_command.os, "geteuid", return_value=0), \
             mock.patch.object(install_command.install, "parse_os_release",
                               return_value={"ID": "fedora-asahi-remix", "VERSION_ID": "44"}), \
             mock.patch.object(install_command.platform, "machine", return_value="aarch64"), \
             mock.patch.object(install_command.source_update, "discover_source_root", return_value=self.checkout), \
             mock.patch.object(install_command.source_update, "resolved_snapshot", side_effect=error), \
             mock.patch.object(install_command.source_update, "install_from_snapshot") as apply, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(install_command.run_install(), 2)
        apply.assert_not_called()
        self.assertIn("no changes were applied", output.getvalue())

    def test_origin_url_rewrite_is_checked_without_overwriting_configured_value(self):
        git(["-C", str(self.checkout), "config", "url.https://example.invalid/.insteadOf", self.asahi_url])
        with self.assertRaisesRegex(source_update.SourceUpdateError, "URL rewrite"):
            with self._snapshot():
                pass
        raw = git(["-C", str(self.checkout), "config", "--local", "--get", "remote.origin.url"]).stdout.strip()
        self.assertEqual(raw, self.asahi_url)

    def test_concurrent_checkout_edit_cannot_change_privileged_snapshot_code(self):
        with self._snapshot() as snapshot:
            pinned = (snapshot.root / "niri_plus/install_command.py").read_bytes()
            pinned_entry = (snapshot.root / "scripts/install-snapshot").read_bytes()
            source_file = self.checkout / "niri_plus/install_command.py"
            source_file.write_text("# attacker replacement after resolution\n")
            (self.checkout / "scripts/install-snapshot").write_text("# changed root entry after resolution\n")
            executed = {}

            def root_runner(args, **kwargs):
                executed["args"] = args
                executed["env"] = kwargs["env"]
                executed["cwd"] = kwargs["cwd"]
                executed["snapshot_code"] = pathlib.Path(args[1]).read_bytes()
                executed["snapshot_cli"] = pathlib.Path(kwargs["env"]["PYTHONPATH"],
                                                         "niri_plus/install_command.py").read_bytes()
                executed["python_environment"] = {key: value for key, value in kwargs["env"].items()
                                                  if key.startswith("PYTHON")}
                return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

            with mock.patch.dict(os.environ, {"PYTHONHOME": "/tmp/untrusted-python",
                                               "PYTHONSTARTUP": "/tmp/untrusted-startup"}):
                source_update.install_from_snapshot(snapshot, root_runner)
            self.assertEqual(executed["snapshot_code"], pinned_entry)
            self.assertEqual(executed["snapshot_cli"], pinned)
            self.assertEqual(executed["env"]["PYTHONPATH"], str(snapshot.root))
            self.assertEqual(executed["python_environment"], {"PYTHONPATH": str(snapshot.root)})
            self.assertNotIn(str(self.checkout), executed["args"])
            self.assertEqual(executed["cwd"], str(snapshot.root))
            self.assertEqual((snapshot.root / "niri_plus/install_command.py").read_bytes(), pinned)
        self.assertFalse(snapshot.root.exists(), "snapshot must be discarded after install")


class InstallationTransactionTests(unittest.TestCase):
    def test_failure_during_bootstrap_staging_restores_all_previous_destinations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            cli = root / "usr/local/bin/niri+"
            assets = root / "usr/local/share/niri-plus"
            state = root / "var/lib/asahi-system/state.json"
            cli.parent.mkdir(parents=True)
            assets.mkdir(parents=True)
            state.parent.mkdir(parents=True)
            cli.write_text("old-cli")
            (assets / "VERSION").write_text("0.1.1")
            state.write_text('{"applied_version":"0.1.1"}')
            before = (cli.read_bytes(), (assets / "VERSION").read_bytes(), state.read_bytes())
            tx = install_transaction.FilesystemTransaction(
                [pathlib.Path("/usr/local/bin/niri+"), pathlib.Path("/usr/local/share/niri-plus"),
                 pathlib.Path("/var/lib/asahi-system/state.json")], root=root,
            )
            with self.assertRaisesRegex(RuntimeError, "staging failure"):
                with tx:
                    cli.write_text("new-cli")
                    (assets / "VERSION").write_text("0.1.2")
                    state.write_text('{"applied_version":"0.1.2"}')
                    raise RuntimeError("staging failure")
            self.assertEqual((cli.read_bytes(), (assets / "VERSION").read_bytes(), state.read_bytes()), before)

    def test_second_noop_transaction_preserves_install_state_and_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            path = root / "usr/local/share/niri-plus/VERSION"
            path.parent.mkdir(parents=True)
            path.write_text("0.1.2")
            before = path.stat().st_mtime_ns
            with install_transaction.FilesystemTransaction([pathlib.Path("/usr/local/share/niri-plus")], root=root) as tx:
                self.assertEqual(path.read_text(), "0.1.2")
                tx.commit()
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_failure_after_apply_restores_pin_and_managed_config_together(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = root / "etc/niri/keybinds.kdl"
            state = root / "var/lib/asahi-system/state.json"
            config.parent.mkdir(parents=True)
            state.parent.mkdir(parents=True)
            config.write_text("Mod+Space { spawn \"fuzzel\"; }\n")
            state.write_text(json.dumps({"applied_version": "0.1.1", "quickshell": {"expected_commit": "a" * 40}}))
            tx = install_transaction.FilesystemTransaction(
                [pathlib.Path("/etc/niri/keybinds.kdl"), pathlib.Path("/var/lib/asahi-system/state.json")], root=root,
            )
            with self.assertRaisesRegex(RuntimeError, "post-apply verification"):
                with tx:
                    config.write_text("Mod+Space { spawn \"other\"; }\n")
                    state.write_text(json.dumps({"applied_version": "0.1.2", "quickshell": {"expected_commit": "b" * 40}}))
                    raise RuntimeError("post-apply verification")
            self.assertIn("fuzzel", config.read_text())
            restored = json.loads(state.read_text())
            self.assertEqual(restored["applied_version"], "0.1.1")
            self.assertEqual(restored["quickshell"]["expected_commit"], "a" * 40)

    def test_orchestrated_failures_at_each_install_stage_restore_coherent_previous_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            targets = {
                "cli": root / "usr/local/bin/niri+",
                "pin": root / "var/lib/niri-plus/bootstrap.json",
                "config": root / "etc/niri/keybinds.kdl",
            }
            for path in targets.values():
                path.parent.mkdir(parents=True, exist_ok=True)
            old = {"cli": b"old-cli", "pin": b'{"quickshell_expected_commit":"a"}',
                   "config": b'Mod+Space { spawn "fuzzel"; }\n'}
            paths = [pathlib.Path("/usr/local/bin/niri+"), pathlib.Path("/var/lib/niri-plus/bootstrap.json"),
                     pathlib.Path("/etc/niri/keybinds.kdl")]
            for failure_stage in ("bootstrap", "apply", "verify", "package"):
                for key, data in old.items():
                    targets[key].write_bytes(data)

                def write_new():
                    targets["cli"].write_bytes(b"new-cli")
                    targets["pin"].write_bytes(b'{"quickshell_expected_commit":"b"}')
                    targets["config"].write_bytes(b'Mod+Space { spawn "broken"; }\n')

                def stage(name):
                    if failure_stage == name:
                        raise RuntimeError(name + " failure")

                with self.assertRaisesRegex(RuntimeError, failure_stage + " failure"):
                    snapshot_install.run_install_transaction(
                        paths,
                        lambda: (write_new(), stage("bootstrap")),
                        lambda: stage("apply"),
                        lambda: stage("verify"),
                        lambda: stage("package"),
                        root=root,
                    )
                self.assertEqual({key: path.read_bytes() for key, path in targets.items()}, old)


class WaylandReadinessTests(unittest.TestCase):
    def test_gate_waits_for_wayland_sync_response_not_just_a_socket_name(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = pathlib.Path(temp)
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            socket_path = runtime / "wayland-9"
            server.bind(str(socket_path))
            server.listen(1)

            def serve_once():
                connection, _ = server.accept()
                with connection:
                    request = connection.recv(12)
                    self.assertEqual(len(request), 12)
                    connection.sendall(struct.pack("=III", 2, 12 << 16, 1))

            thread = threading.Thread(target=serve_once, daemon=True)
            thread.start()
            display, found = wayland_ready.wait_for_wayland(runtime, timeout=1.0)
            thread.join(timeout=1)
            server.close()
            self.assertEqual(display, "wayland-9")
            self.assertEqual(found, socket_path)

    def test_stale_imported_display_does_not_hide_niris_live_socket(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = pathlib.Path(temp)
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(str(runtime / "wayland-0"))
            stale.listen(1)  # No accept/read: it is a socket name, not a ready compositor.
            ready_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            ready_path = runtime / "wayland-1"
            ready_server.bind(str(ready_path))
            ready_server.listen(1)

            def serve_once():
                connection, _ = ready_server.accept()
                with connection:
                    request = connection.recv(12)
                    self.assertEqual(len(request), 12)
                    connection.sendall(struct.pack("=III", 2, 12 << 16, 1))

            thread = threading.Thread(target=serve_once, daemon=True)
            thread.start()
            display, found = wayland_ready.wait_for_wayland(runtime, "wayland-0", timeout=1.5)
            thread.join(timeout=1)
            stale.close()
            ready_server.close()
            self.assertEqual(display, "wayland-1")
            self.assertEqual(found, ready_path)

    def test_unit_ordering_gates_both_graphical_clients_and_stops_with_session(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        ready = (root / "sessions/systemd/asahi-niri-wayland-ready.service").read_text()
        for client_path in ("asahi-quickshell.service", "asahi-niri-polkit-agent.service"):
            client = (root / "sessions/systemd" / client_path).read_text()
            self.assertIn("ConditionEnvironment=XDG_CURRENT_DESKTOP=niri", client)
            self.assertIn("Requires=asahi-niri-wayland-ready.service", client)
            self.assertIn("After=asahi-niri-wayland-ready.service", client)
            self.assertIn("PartOf=graphical-session.target", client)
        self.assertIn("Before=graphical-session.target", ready)
        self.assertIn("PartOf=graphical-session.target", ready)
        self.assertIn("WantedBy=graphical-session.target", ready)
        self.assertNotIn("sleep ", ready)


if __name__ == "__main__":
    unittest.main()
