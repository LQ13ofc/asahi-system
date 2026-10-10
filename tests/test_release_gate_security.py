from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pathlib
import pwd
import runpy
import socket
import struct
import subprocess
import sys
import shutil
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
            "engine": {"nevra": "quickshell-0:0.3.1-2.fc44", "repository": "https://packages.invalid/",
                       "gpg_key": "https://packages.invalid/key.gpg"},
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

    def test_resolved_visual_commit_installs_as_one_reversible_host_transaction(self):
        # Build a complete, isolated candidate from real Git objects, then run
        # the actual snapshot bootstrap and system-file installer against a
        # temporary root. This exercises their boundary without touching /usr.
        for relative, content in {
            "niri/config.kdl": "include \"keybinds.kdl\"\n",
            "sessions/systemd/asahi-quickshell.service": "[Service]\nExecStart=/usr/bin/qs\n",
            "niri_plus/cli.py": "# candidate CLI module\n",
            "scripts/collect-performance-baseline": "#!/bin/sh\nexit 0\n",
        }.items():
            target = self.asahi_seed / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        bootstrap_source = pathlib.Path(__file__).resolve().parents[1] / "scripts/bootstrap-niri-plus"
        shutil.copy2(bootstrap_source, self.asahi_seed / "scripts/bootstrap-niri-plus")
        (self.asahi_seed / "scripts/collect-performance-baseline").chmod(0o755)
        self._write_system_tree("0.1.2", self.qs_commit)
        candidate_commit = commit_all(self.asahi_seed, "complete candidate installation tree")
        git(["-C", str(self.asahi_seed), "push", "origin", "main"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git(["-C", str(self.asahi_seed), "push", "origin", "HEAD:refs/pull/19/head"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        real_run_path = snapshot_install.runpy.run_path
        real_mkdtemp = tempfile.mkdtemp
        original_transaction = snapshot_install.run_install_transaction
        original_verify = snapshot_install._verify_committed_install

        def exercise(host_root: pathlib.Path, snapshot, failure: str | None = None) -> None:
            version = (snapshot.root / "VERSION").read_text(encoding="utf-8").strip()
            qs_commit = snapshot.quickshell_commit
            staged_roots = {
                "lib": host_root / "usr/local/lib/niri-plus",
                "data": host_root / "usr/local/share/niri-plus",
                "bin": host_root / "usr/local/bin/niri+",
                "state": host_root / "var/lib/niri-plus/bootstrap.json",
                "backups": host_root / "var/lib/niri-plus/bootstrap-backups",
            }
            (host_root / "usr/local").mkdir(parents=True, exist_ok=True)

            def run_snapshot_script(path, *args, **kwargs):
                namespace = real_run_path(path, *args, **kwargs)
                globals_ns = namespace["install_snapshot"].__globals__
                globals_ns["LIB"] = staged_roots["lib"]
                globals_ns["DATA"] = staged_roots["data"]
                globals_ns["BIN"] = staged_roots["bin"]
                globals_ns["STATE"] = staged_roots["state"]
                globals_ns["BACKUPS"] = staged_roots["backups"]
                globals_ns["EXPECTED_MANAGED"] = [str(staged_roots[key]) for key in ("lib", "data", "bin")]
                return namespace

            def staged_mkdtemp(suffix=None, prefix=None, dir=None):
                if dir == "/usr/local":
                    dir = str(host_root / "usr/local")
                return real_mkdtemp(prefix=prefix, suffix=suffix, dir=dir)

            def apply_candidate(*, defer_packages: bool, include_quickshell: bool) -> None:
                install_command.install.install_files(host_root, include_quickshell=include_quickshell)
                state = install_command.install.load_state(host_root)
                state["applied_version"] = version
                state["packages_installed_by_us"] = ["niri", "foot"]
                install_command.install.update_quickshell_state(state, qs_commit, included=include_quickshell)
                install_command.install.write_state(host_root, state)
                if failure == "apply":
                    raise RuntimeError("injected apply failure")

            def verify_candidate(*_args) -> None:
                if failure == "verify":
                    raise RuntimeError("injected verify failure")
                installed = json.loads(staged_roots["state"].read_text(encoding="utf-8"))
                state = install_command.install.load_state(host_root)
                if (installed["version"] != version
                        or installed["quickshell_expected_commit"] != qs_commit
                        or state["applied_version"] != version
                        or state["quickshell"]["expected_commit"] != qs_commit):
                    raise RuntimeError("simulated post-install verification failed")

            def transaction(paths, bootstrap, apply, verify, finalize=lambda: None):
                if failure == "bootstrap":
                    resolved_bootstrap = bootstrap
                    def fail_after_bootstrap():
                        resolved_bootstrap()
                        raise RuntimeError("injected bootstrap failure")
                    bootstrap = fail_after_bootstrap
                return original_transaction(paths, bootstrap, apply, verify, finalize, root=host_root)

            def patched_bootstrap(path, *args, **kwargs):
                return run_snapshot_script(path, *args, **kwargs)

            with mock.patch.dict(os.environ, {}), \
                 mock.patch.object(snapshot_install.source_update, "EXPECTED_REPOSITORY", self.asahi_url), \
                 mock.patch.object(snapshot_install.source_update, "EXPECTED_QUICKSHELL_REPOSITORY", self.qs_url), \
                 mock.patch("os.geteuid", return_value=0), \
                 mock.patch("tempfile.mkdtemp", side_effect=staged_mkdtemp), \
                 mock.patch.object(snapshot_install.runpy, "run_path", side_effect=patched_bootstrap), \
                 mock.patch.object(snapshot_install, "run_install_transaction", side_effect=transaction), \
                 mock.patch.object(snapshot_install, "_verify_committed_install", side_effect=verify_candidate), \
                 mock.patch.object(install_command.install, "apply_install", side_effect=apply_candidate), \
                 mock.patch.object(install_command.install, "install_locked_packages") as package_install, \
                 mock.patch.object(install_command.install, "reload_invoking_user_manager"):
                old_path = list(sys.path)
                try:
                    snapshot_install.apply_snapshot(snapshot.root, snapshot.manifest)
                finally:
                    sys.path[:] = old_path
                if failure is None:
                    package_install.assert_called_once()
                    runtime = staged_roots["data"] / "quickshell"
                    self.assertEqual((runtime / "shell.qml").read_text(encoding="utf-8"), "// visual pin one\n")
                    marker = json.loads((staged_roots["data"] / "quickshell.snapshot.json").read_text())
                    self.assertEqual(marker["commit"], qs_commit)
                    self.assertEqual(install_command.install.load_state(host_root)["quickshell"]["expected_commit"], qs_commit)
                    self.assertTrue((host_root / "usr/lib/systemd/user/asahi-quickshell.service").is_file())
                    self.assertEqual(os.readlink(host_root / "usr/lib/systemd/user/graphical-session.target.wants/asahi-quickshell.service"),
                                     "../asahi-quickshell.service")
                else:
                    package_install.assert_not_called()
                    self.assertFalse(staged_roots["lib"].exists())
                    self.assertFalse(staged_roots["data"].exists())
                    self.assertFalse(staged_roots["bin"].exists())
                    self.assertFalse(staged_roots["state"].exists())
                    self.assertFalse((host_root / "etc/niri/config.kdl").exists())
                    self.assertFalse((host_root / "usr/lib/systemd/user/asahi-quickshell.service").exists())

        with self._snapshot() as snapshot:
            self.assertEqual(snapshot.quickshell_commit, self.qs_commit)
            for failure in ("bootstrap", "apply", "verify"):
                with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                    host_root = pathlib.Path(temp)
                    with self.assertRaisesRegex(RuntimeError, f"injected {failure} failure"):
                        exercise(host_root, snapshot, failure)
            with tempfile.TemporaryDirectory() as temp:
                host_root = pathlib.Path(temp)
                exercise(host_root, snapshot)

        # Exercise the new opt-in channel through the same snapshot bootstrap,
        # asset staging, post-install verification and injected rollback path.
        with mock.patch.object(source_update, "RELEASE_CANDIDATE_QUICKSHELL_COMMIT", self.qs_commit):
            with self._snapshot(release_candidate=True, expected_system_commit=candidate_commit) as snapshot:
                self.assertEqual(snapshot.system_commit, candidate_commit)
                for failure in ("bootstrap", "apply", "verify"):
                    with self.subTest(candidate_failure=failure), tempfile.TemporaryDirectory() as temp:
                        host_root = pathlib.Path(temp)
                        with self.assertRaisesRegex(RuntimeError, f"injected {failure} failure"):
                            exercise(host_root, snapshot, failure)
                with tempfile.TemporaryDirectory() as temp:
                    exercise(pathlib.Path(temp), snapshot)

    def test_temporary_bare_store_is_created_by_git_as_checkout_owner(self):
        parent = self.root / "owner-temp"
        parent.mkdir()
        store = parent / "objects.git"

        source_update._create_git_dir(self.checkout, store, subprocess.run)

        self.assertTrue((store / "HEAD").is_file())
        self.assertTrue((store / "objects").is_dir())

        with self.assertRaisesRegex(source_update.SourceUpdateError, "already exists"):
            source_update._create_git_dir(self.checkout, store, subprocess.run)

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
        local_head_before = git(["-C", str(self.checkout), "rev-parse", "HEAD"]).stdout.strip()
        with self._snapshot() as snapshot:
            self.assertEqual(snapshot.system_commit, remote_commit)
            self.assertEqual((snapshot.root / "VERSION").read_text(), "0.1.2\n")
            self.assertEqual(snapshot.quickshell_commit, self.qs_commit)
        self.assertEqual(git(["-C", str(self.checkout), "rev-parse", "HEAD"]).stdout.strip(), local_head_before)
        self.assertNotEqual(local_head_before, remote_commit)

    def test_niri_only_snapshot_skips_private_quickshell_fetch_and_materialization(self):
        commands = []

        def record_runner(args, **kwargs):
            commands.append([str(value) for value in args])
            return subprocess.run(args, **kwargs)

        with self._snapshot(runner=record_runner, include_quickshell=False) as snapshot:
            manifest = source_update.validate_snapshot(
                snapshot.root, snapshot.manifest,
                expected_repository=snapshot.expected_repository,
                expected_quickshell_repository=snapshot.expected_quickshell_repository,
                allow_test_file_sources=snapshot.allow_test_file_sources,
            )
            self.assertFalse(snapshot.include_quickshell)
            self.assertEqual(snapshot.quickshell_commit, self.qs_commit)
            self.assertFalse((snapshot.root / "external/quickshell").exists())
            self.assertEqual(manifest["quickshell"]["files"], [])
            invoked = []

            def capture_install(args, **kwargs):
                invoked.extend(str(value) for value in args)
                return subprocess.CompletedProcess(args, 0)

            source_update.install_from_snapshot(snapshot, capture_install)

        self.assertFalse(any("fetch" in command and self.qs_url in command for command in commands))
        self.assertIn("--without-quickshell", invoked)

    def test_install_with_source_already_at_remote_head_is_reproducible(self):
        with self._snapshot() as first:
            first_sha = first.system_commit
        with self._snapshot() as second:
            self.assertEqual(second.system_commit, first_sha)
            self.assertEqual(second.quickshell_commit, self.qs_commit)

    def test_release_candidate_requires_exact_pr_head_and_ignores_mutable_worktree(self):
        self._write_system_tree("0.1.9-rc", self.qs_commit)
        candidate = commit_all(self.asahi_seed, "release candidate immutable source")
        git(["-C", str(self.asahi_seed), "push", "origin", f"HEAD:refs/pull/19/head"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git(["-C", str(self.checkout), "switch", "-c", "worktree-experiment"])
        dirty_file = self.checkout / "niri_plus/install_command.py"
        dirty_file.write_text("# uncommitted code must never enter the privileged snapshot\n")
        (self.checkout / "untracked-secret.txt").write_text("never include\n")
        with mock.patch.object(source_update, "RELEASE_CANDIDATE_QUICKSHELL_COMMIT", self.qs_commit):
            with self._snapshot(release_candidate=True, expected_system_commit=candidate) as snapshot:
                manifest = source_update.validate_snapshot(
                    snapshot.root, snapshot.manifest,
                    expected_repository=self.asahi_url,
                    expected_quickshell_repository=self.qs_url,
                    allow_test_file_sources=True,
                )
                self.assertEqual(snapshot.system_commit, candidate)
                self.assertEqual(snapshot.quickshell_commit, self.qs_commit)
                self.assertEqual(manifest["channel"], "release-candidate")
                self.assertEqual(manifest["system"]["branch"], "refs/pull/19/head")
                self.assertNotIn("untracked-secret.txt", {item["path"] for item in manifest["system"]["files"]})
                self.assertNotEqual((snapshot.root / "niri_plus/install_command.py").read_text(), dirty_file.read_text())
        self.assertEqual(dirty_file.read_text(), "# uncommitted code must never enter the privileged snapshot\n")
        with mock.patch.object(source_update, "RELEASE_CANDIDATE_QUICKSHELL_COMMIT", self.qs_commit):
            with self.assertRaisesRegex(source_update.SourceUpdateError, "head changed"):
                with self._snapshot(release_candidate=True, expected_system_commit="0" * 40):
                    pass

    def test_release_candidate_rejects_quickshell_lock_divergence_and_unexpected_pin(self):
        self._write_system_tree("0.1.9-rc", self.qs_commit)
        candidate = commit_all(self.asahi_seed, "release candidate lock")
        git(["-C", str(self.asahi_seed), "push", "origin", "HEAD:refs/pull/19/head"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with mock.patch.object(source_update, "RELEASE_CANDIDATE_QUICKSHELL_COMMIT", "0" * 40):
            with self.assertRaisesRegex(source_update.SourceUpdateError, "approved Release Candidate Quickshell"):
                with self._snapshot(release_candidate=True, expected_system_commit=candidate):
                    pass

    def test_committed_bootstrap_accepts_only_the_explicit_candidate_channel(self):
        self._write_system_tree("0.1.9-rc", self.qs_commit)
        bootstrap = pathlib.Path(__file__).resolve().parents[1] / "scripts/bootstrap-niri-plus"
        shutil.copy2(bootstrap, self.asahi_seed / "scripts/bootstrap-niri-plus")
        candidate = commit_all(self.asahi_seed, "candidate bootstrap contract")
        git(["-C", str(self.asahi_seed), "push", "origin", "HEAD:refs/pull/19/head"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with mock.patch.object(source_update, "RELEASE_CANDIDATE_QUICKSHELL_COMMIT", self.qs_commit), \
             mock.patch.object(source_update, "EXPECTED_REPOSITORY", self.asahi_url), \
             mock.patch.object(source_update, "EXPECTED_QUICKSHELL_REPOSITORY", self.qs_url), \
             self._snapshot(release_candidate=True, expected_system_commit=candidate) as snapshot:
            namespace = runpy.run_path(str(snapshot.root / "scripts/bootstrap-niri-plus"),
                                       run_name="niri_plus_rc_bootstrap_contract")
            marker = RuntimeError("candidate metadata passed; staging reached")
            with mock.patch.object(os, "geteuid", return_value=0), \
                 mock.patch.dict(namespace, {"_validate_ownership": lambda: ({}, False)}), \
                 mock.patch("tempfile.mkdtemp", side_effect=marker):
                with self.assertRaisesRegex(RuntimeError, "staging reached"):
                    namespace["install_snapshot"](snapshot.root, snapshot.manifest)

    def test_known_good_restore_uses_saved_exact_system_and_visual_commits(self):
        with self._snapshot(known_good_restore=True, expected_system_commit=self.initial_system_commit,
                            expected_quickshell_commit=self.qs_commit) as snapshot:
            manifest = source_update.validate_snapshot(
                snapshot.root, snapshot.manifest,
                expected_repository=self.asahi_url,
                expected_quickshell_repository=self.qs_url,
                allow_test_file_sources=True,
            )
            self.assertEqual(manifest["channel"], "known-good-restore")
            self.assertEqual(manifest["system"]["commit"], self.initial_system_commit)
            self.assertEqual(manifest["quickshell"]["commit"], self.qs_commit)

    def test_production_install_path_still_requires_clean_main_and_reads_main_pin(self):
        self._write_system_tree("0.1.9-candidate-but-not-main", self.qs_commit)
        candidate = commit_all(self.asahi_seed, "non-production candidate")
        git(["-C", str(self.asahi_seed), "push", "origin", "HEAD:refs/pull/19/head"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self._snapshot() as snapshot:
            self.assertEqual(snapshot.system_commit, self.initial_system_commit)
            self.assertNotEqual(snapshot.system_commit, candidate)
            self.assertEqual(snapshot.quickshell_commit, self.qs_commit)

    def test_rc_known_good_record_is_atomic_validated_and_not_replaced_by_candidate_state(self):
        helper_path = pathlib.Path(__file__).resolve().parents[1] / "scripts/niri-plus-rc-bootstrap"
        helper = runpy.run_path(str(helper_path), run_name="niri_plus_rc_test_helper")
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            bootstrap = root / "var/lib/niri-plus/bootstrap.json"
            data = root / "usr/local/share/niri-plus"
            bootstrap.parent.mkdir(parents=True)
            data.joinpath("integration").mkdir(parents=True)
            bootstrap.write_text(json.dumps({
                "source_repository": source_update.EXPECTED_REPOSITORY,
                "source_branch": "main", "source_channel": "production",
                "source_commit": self.initial_system_commit, "quickshell_expected_commit": self.qs_commit,
                "version": "0.1.8",
            }))
            (data / "integration/quickshell.lock.json").write_text(json.dumps({"commit": self.qs_commit}))
            (data / "quickshell.snapshot.json").write_text(json.dumps({"commit": self.qs_commit}))
            record = helper["_save_known_good"](root)
            self.assertEqual(record["system_commit"], self.initial_system_commit)
            self.assertEqual(helper["_load_known_good"](root)["quickshell_commit"], self.qs_commit)
            bootstrap.write_text(json.dumps({"source_channel": "release-candidate"}))
            self.assertEqual(helper["_save_known_good"](root), record)
            path = root / "var/lib/niri-plus/release-candidate/known-good.json"
            contents = json.loads(path.read_text())
            contents["system_commit"] = "0" * 40
            path.write_text(json.dumps(contents))
            with self.assertRaisesRegex(helper["BootstrapError"], "integrity"):
                helper["_load_known_good"](root)

    def test_rc_bootstrap_loads_only_source_resolver_blob_from_exact_committed_tree(self):
        helper_path = pathlib.Path(__file__).resolve().parents[1] / "scripts/niri-plus-rc-bootstrap"
        helper = runpy.run_path(str(helper_path), run_name="niri_plus_rc_loader_test")
        system_commit = "a" * 40
        source_bytes = b"VERIFIED_FROM_GIT = 'candidate resolver'\n"
        helper_bytes = b"exact bootstrap blob\n"
        blob_oid = hashlib.sha1(b"blob " + str(len(source_bytes)).encode() + b"\0" + source_bytes).hexdigest()
        helper_oid = hashlib.sha1(b"blob " + str(len(helper_bytes)).encode() + b"\0" + helper_bytes).hexdigest()
        helper_globals = helper["_load_committed_resolver"].__globals__

        def git_output(_source, _owner, _groups, *args, **_kwargs):
            if args[:2] == ("ls-remote", "--exit-code"):
                return f"{system_commit}\trefs/pull/19/head"
            if args[0] == "rev-parse" and args[-1] == "FETCH_HEAD^{commit}":
                return system_commit
            if args[0] == "rev-parse" and args[-1] == f"{system_commit}:niri_plus/source_update.py":
                return blob_oid
            if args[0] == "rev-parse" and args[-1] == f"{system_commit}:scripts/niri-plus-rc-bootstrap":
                return helper_oid
            return ""

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict(helper_globals, {"__verified_script_bytes__": helper_bytes,
                                                  "_owner_git": git_output,
                                                  "_owner_git_bytes": lambda *_args: source_bytes}):
                resolver = helper["_load_committed_resolver"](
                    pathlib.Path(temp), None, [], system_commit, pathlib.Path(temp), require_current_pr=True,
                )
                self.assertEqual(resolver.VERIFIED_FROM_GIT, "candidate resolver")
            with mock.patch.dict(helper_globals, {"__verified_script_bytes__": b"tampered helper",
                                                  "_owner_git": git_output,
                                                  "_owner_git_bytes": lambda *_args: source_bytes}):
                with self.assertRaisesRegex(helper["BootstrapError"], "do not belong"):
                    helper["_load_committed_resolver"](
                        pathlib.Path(temp), None, [], system_commit, pathlib.Path(temp), require_current_pr=True,
                    )

    def test_rc_bootstrap_git_uses_checkout_owner_and_never_prompts(self):
        from types import SimpleNamespace

        helper_path = pathlib.Path(__file__).resolve().parents[1] / "scripts/niri-plus-rc-bootstrap"
        helper = runpy.run_path(str(helper_path), run_name="niri_plus_rc_git_owner_test")
        namespace = helper["_owner_git"].__globals__
        with tempfile.TemporaryDirectory() as temp:
            config_path = pathlib.Path(temp) / "gitconfig"
            config_path.write_text("[credential]\nhelper = safe-helper\n")
            owner = SimpleNamespace(pw_dir="/home/checkout-owner", pw_name="checkout-owner", pw_gid=1001,
                                    pw_uid=config_path.stat().st_uid)
            runner = mock.MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout=b"ok\n", stderr=b""))
            with mock.patch.object(namespace["subprocess"], "run", runner), \
                 mock.patch.object(namespace["os"], "geteuid", return_value=0), \
                 mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config_path)}):
                self.assertEqual(helper["_owner_git"](pathlib.Path("/home/checkout-owner/source"), owner, [1001],
                                                        "status", "--short"), "ok")
        _command, options = runner.call_args
        environment = options["env"]
        self.assertEqual(environment["HOME"], owner.pw_dir)
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], str(config_path))
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(environment["GCM_INTERACTIVE"], "never")
        self.assertEqual(environment["GCM_MODAL_PROMPT"], "0")
        self.assertEqual(environment["GIT_CONFIG_VALUE_0"], "false")
        self.assertNotIn("GIT_ASKPASS", environment)
        self.assertTrue(environment["GIT_SSH_COMMAND"].endswith("BatchMode=yes"))
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertIsNotNone(options["preexec_fn"])

    def test_rc_bootstrap_dry_run_reports_candidate_and_known_good_exact_commits(self):
        from types import SimpleNamespace

        system_sha = "c" * 40
        quickshell_sha = source_update.RELEASE_CANDIDATE_QUICKSHELL_COMMIT
        good_system_sha = "d" * 40
        good_quickshell_sha = "e" * 40

        class FakeResolver:
            def resolved_snapshot(self, *_args, **kwargs):
                self.asserted_candidate = kwargs
                return FakeSnapshotContext(
                    self.asserted_candidate.get("expected_system_commit", good_system_sha),
                    self.asserted_candidate.get("expected_quickshell_commit", quickshell_sha),
                )

            def install_from_snapshot(self, snapshot):
                self.installed_snapshot = snapshot

        class FakeSnapshotContext:
            def __init__(self, system_commit, quickshell_commit):
                self.snapshot = SimpleNamespace(system_commit=system_commit, quickshell_commit=quickshell_commit)
            def __enter__(self):
                return self.snapshot
            def __exit__(self, *_args):
                return False

        with tempfile.TemporaryDirectory() as temp:
            helper_path = pathlib.Path(__file__).resolve().parents[1] / "scripts/niri-plus-rc-bootstrap"
            helper = runpy.run_path(str(helper_path), run_name="niri_plus_rc_main_test")
            namespace = helper["main"].__globals__
            resolver = FakeResolver()
            with mock.patch.dict(namespace, {
                "_identity": lambda _source: (SimpleNamespace(pw_dir=temp, pw_name="owner"), []),
                "_remote": lambda *_args: "https://github.com/LQ13ofc/asahi-system.git",
                "_load_committed_resolver": lambda *_args, **_kwargs: resolver,
                "_current_known_good": lambda: {"system_commit": good_system_sha,
                                                 "quickshell_commit": good_quickshell_sha},
                "_prefixed": lambda _root, _path: pathlib.Path(temp) / "no-known-good-record",
                "_save_known_good": mock.MagicMock(),
            }), mock.patch.object(namespace["os"], "geteuid", return_value=0), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(helper["main"]([
                    "--source-root", temp, "--bootstrap-commit", system_sha,
                    "--candidate-commit", system_sha, "--dry-run",
                ]), 0)
            self.assertEqual(resolver.asserted_candidate, {
                "include_quickshell": True, "release_candidate": True,
                "expected_system_commit": system_sha,
            })
            for value in (system_sha, quickshell_sha, good_system_sha, good_quickshell_sha):
                self.assertIn(value, output.getvalue())

            save_good = mock.MagicMock()
            with mock.patch.dict(namespace, {
                "_identity": lambda _source: (SimpleNamespace(pw_dir=temp, pw_name="owner"), []),
                "_remote": lambda *_args: "https://github.com/LQ13ofc/asahi-system.git",
                "_load_committed_resolver": lambda *_args, **_kwargs: resolver,
                "_current_known_good": lambda: {"system_commit": good_system_sha,
                                                 "quickshell_commit": good_quickshell_sha},
                "_prefixed": lambda _root, _path: pathlib.Path(temp) / "no-known-good-record",
                "_save_known_good": save_good,
                "_target_check": lambda: None,
            }), mock.patch.object(namespace["os"], "geteuid", return_value=0):
                self.assertEqual(helper["main"]([
                    "--source-root", temp, "--bootstrap-commit", system_sha,
                    "--candidate-commit", system_sha, "--apply",
                ]), 0)
            save_good.assert_called_once_with()
            self.assertEqual(resolver.installed_snapshot.system_commit, system_sha)

            saved = {"system_commit": good_system_sha, "quickshell_commit": good_quickshell_sha}
            clear_good = mock.MagicMock()
            with mock.patch.dict(namespace, {
                "_identity": lambda _source: (SimpleNamespace(pw_dir=temp, pw_name="owner"), []),
                "_remote": lambda *_args: "https://github.com/LQ13ofc/asahi-system.git",
                "_load_committed_resolver": lambda *_args, **_kwargs: resolver,
                "_load_known_good": lambda: saved,
                "_clear_known_good": clear_good,
                "_target_check": lambda: None,
            }), mock.patch.object(namespace["os"], "geteuid", return_value=0):
                self.assertEqual(helper["main"]([
                    "--source-root", temp, "--bootstrap-commit", system_sha,
                    "--restore-known-good", "--apply",
                ]), 0)
            self.assertEqual(resolver.asserted_candidate, {
                "include_quickshell": True, "known_good_restore": True,
                "expected_system_commit": good_system_sha,
                "expected_quickshell_commit": good_quickshell_sha,
            })
            self.assertEqual(resolver.installed_snapshot.system_commit, good_system_sha)
            clear_good.assert_called_once_with()

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
            self.assertEqual(install_command.run_install(include_quickshell=True), 2)
        apply.assert_not_called()
        self.assertIn("no changes were applied", output.getvalue())

    def test_snapshot_child_runs_without_writing_python_bytecode(self):
        with self._snapshot() as snapshot:
            calls = []

            def runner(args, **kwargs):
                calls.append((args, kwargs))
                return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

            source_update.install_from_snapshot(snapshot, runner)

            self.assertEqual(calls[0][0][1:3], ["-I", "-B"])
            self.assertFalse(any(path.name == "__pycache__" for path in snapshot.root.rglob("__pycache__")))

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
                executed["snapshot_code"] = pathlib.Path(args[3]).read_bytes()
                executed["snapshot_cli"] = pathlib.Path(snapshot.root,
                                                         "niri_plus/install_command.py").read_bytes()
                executed["python_environment"] = {key: value for key, value in kwargs["env"].items()
                                                  if key.startswith("PYTHON")}
                return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

            with mock.patch.dict(os.environ, {"PYTHONHOME": "/tmp/untrusted-python",
                                               "PYTHONSTARTUP": "/tmp/untrusted-startup"}):
                source_update.install_from_snapshot(snapshot, root_runner)
            self.assertEqual(executed["snapshot_code"], pinned_entry)
            self.assertEqual(executed["snapshot_cli"], pinned)
            self.assertEqual(executed["args"][1:3], ["-I", "-B"])
            self.assertEqual(executed["python_environment"], {})
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

    def test_transaction_failure_restores_file_permissions_and_protected_paths_are_not_managed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = root / "etc/niri/config.kdl"
            config.parent.mkdir(parents=True)
            config.write_text("personal niri config\n")
            config.chmod(0o640)
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                with install_transaction.FilesystemTransaction([pathlib.Path("/etc/niri/config.kdl")], root=root):
                    config.write_text("candidate config\n")
                    config.chmod(0o600)
                    raise RuntimeError("injected failure")
            self.assertEqual(config.read_text(), "personal niri config\n")
            self.assertEqual(config.stat().st_mode & 0o777, 0o640)

        managed = {path.as_posix() for path in snapshot_install._managed_paths(install_command.install)}
        protected = {
            "/etc/sddm.conf", "/etc/sddm.conf.d", "/etc/selinux", "/boot",
            "/usr/lib/modules", "/usr/lib/firmware", "/usr/share/plasma",
        }
        self.assertFalse(managed & protected)

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
