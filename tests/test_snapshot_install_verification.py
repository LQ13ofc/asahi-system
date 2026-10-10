from __future__ import annotations

import hashlib
import contextlib
import io
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from niri_plus import install, recovery_core, snapshot_install


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _host_path(root: pathlib.Path, absolute: str) -> pathlib.Path:
    return root / absolute.lstrip("/")


def _copy_or_replace(source: pathlib.Path, destination: pathlib.Path) -> None:
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.is_dir():
        shutil.rmtree(destination)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _seed_managed_host(root: pathlib.Path, *, version: str, system_commit: str) -> None:
    """Materialize the real managed maps and installed Niri+ data under a fixture root."""
    for absolute, source in install.MANAGED_FILES.items():
        target = _host_path(root, absolute)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for absolute, link_target in install.MANAGED_LINKS.items():
        target = _host_path(root, absolute)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() and target.readlink().as_posix() == link_target:
            continue
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(link_target)

    lib = root / "usr/local/lib/niri-plus/niri_plus"
    data = root / "usr/local/share/niri-plus"
    _copy_or_replace(ROOT / "niri_plus", lib)
    data.mkdir(parents=True, exist_ok=True)
    (data / "VERSION").write_text(version + "\n", encoding="utf-8")
    for name in ("niri", "sessions", "integration"):
        _copy_or_replace(ROOT / name, data / name)
    scripts = data / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("collect-performance-baseline", "collect-m1-release-gate.sh"):
        _copy_or_replace(ROOT / "scripts" / name, scripts / name)
    bin_path = root / "usr/local/bin/niri+"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\n# fixture launcher\n", encoding="utf-8")
    bin_path.chmod(0o755)
    recovery_helper = root / "usr/local/libexec/niri-plus-recover"
    recovery_helper.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "niri_plus/recovery_core.py", recovery_helper)
    bootstrap = root / "var/lib/niri-plus/bootstrap.json"
    bootstrap.parent.mkdir(parents=True, exist_ok=True)
    bootstrap.write_text(json.dumps({
        "version": version,
        "source_commit": system_commit,
        "source_channel": "production",
        "quickshell_expected_commit": None,
    }) + "\n", encoding="utf-8")

    entries = {}
    for absolute, source in install.MANAGED_FILES.items():
        entries[absolute] = {
            "kind": "file",
            "backup": f"/var/lib/asahi-system/niri-performance/backups{absolute}",
            "original_target": None,
            "installed_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    for absolute, target in install.MANAGED_LINKS.items():
        entries[absolute] = {
            "kind": "symlink", "backup": None, "original_target": "previous-target",
            "installed_target": target,
        }
    install.write_state(root, {
        "schema_version": 1,
        "applied_version": version,
        "quickshell": {"expected_commit": None, "known_good_commit": None},
        "packages_installed_by_us": [],
        "entries": entries,
    })


def _candidate_manifest(version_commit: str) -> dict:
    return {
        "channel": "production",
        "system": {"commit": version_commit},
        "quickshell": {"included": False, "commit": "b" * 40},
    }


class SnapshotInstallVerificationTests(unittest.TestCase):
    def test_root_normalization_always_returns_path_and_rejects_traversal(self):
        real_root_path = snapshot_install._rooted_install_path(
            pathlib.Path("/"), "/etc/niri/config.kdl",
        )
        self.assertIsInstance(real_root_path, pathlib.Path)
        self.assertEqual(real_root_path, pathlib.Path("/etc/niri/config.kdl"))

        with tempfile.TemporaryDirectory() as temp:
            test_root_path = snapshot_install._rooted_install_path(
                pathlib.Path(temp), "/etc/niri/config.kdl",
            )
            self.assertIsInstance(test_root_path, pathlib.Path)
            self.assertEqual(test_root_path, pathlib.Path(temp) / "etc/niri/config.kdl")

        for invalid in ("etc/niri/config.kdl", "/etc/../shadow"):
            with self.subTest(invalid=invalid), self.assertRaises(snapshot_install.InstallVerificationError):
                snapshot_install._rooted_install_path(pathlib.Path("/"), invalid)

    def test_full_post_install_verification_uses_real_string_managed_maps(self):
        self.assertTrue(all(isinstance(key, str) for key in install.MANAGED_FILES))
        self.assertTrue(all(isinstance(key, str) for key in install.MANAGED_LINKS))
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        manifest = _candidate_manifest("a" * 40)
        with tempfile.TemporaryDirectory() as temp:
            host = pathlib.Path(temp) / "host"
            host.mkdir()
            _seed_managed_host(host, version=version, system_commit="a" * 40)

            # No path map, loader, verifier, or filesystem operation is mocked:
            # this checks every real managed-file key, symlink key, and state record.
            snapshot_install._verify_committed_install(ROOT, manifest, install, host)

    def test_programming_errors_are_not_misclassified_as_outdated_install(self):
        with mock.patch.object(snapshot_install, "_verify_committed_install",
                               side_effect=AttributeError("implementation bug")):
            with self.assertRaisesRegex(AttributeError, "implementation bug"):
                snapshot_install._installed_snapshot_matches(ROOT, {}, install, pathlib.Path("/"))

        with mock.patch.object(snapshot_install, "_verify_committed_install",
                               side_effect=snapshot_install.InstallVerificationError("managed file mismatch")):
            self.assertFalse(snapshot_install._installed_snapshot_matches(
                ROOT, {}, install, pathlib.Path("/"),
            ))

    def test_snapshot_cli_success_text_follows_a_successful_transaction_return(self):
        argv = ["--snapshot-root", str(ROOT), "--manifest", str(ROOT / "manifest.json")]
        with mock.patch.object(snapshot_install.os, "geteuid", return_value=0), \
             mock.patch.object(snapshot_install, "apply_snapshot") as apply, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(snapshot_install.main(argv), 0)
        apply.assert_called_once()
        self.assertIn("snapshot installation committed", output.getvalue())

        with mock.patch.object(snapshot_install.os, "geteuid", return_value=0), \
             mock.patch.object(snapshot_install, "apply_snapshot",
                               side_effect=RuntimeError("verification failed")), \
             contextlib.redirect_stdout(io.StringIO()) as output, \
             contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(snapshot_install.main(argv), 2)
        self.assertNotIn("snapshot installation committed", output.getvalue())
        self.assertIn("verification failed", errors.getvalue())

    def test_post_apply_finalize_failure_rolls_back_and_keeps_offline_bundle_valid(self):
        old_commit = "c" * 40
        new_commit = "a" * 40
        old_version = "0.1.13"
        new_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        manifest = _candidate_manifest(new_commit)
        rpm_unavailable = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 127, "", "rpm unavailable")

        with tempfile.TemporaryDirectory() as temp:
            host = pathlib.Path(temp) / "host"
            host.mkdir()
            _seed_managed_host(host, version=old_version, system_commit=old_commit)
            bundle = recovery_core.prepare_bundle(host, runner=rpm_unavailable)
            bundle_digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
            managed_paths = snapshot_install._managed_paths(install)
            before = {
                path.as_posix(): snapshot_install._tree_signature(_host_path(host, path.as_posix()))
                for path in managed_paths
            }

            def stage_bootstrap():
                _seed_managed_host(host, version=new_version, system_commit=new_commit)

            def apply_managed_session():
                install.install_files(host, include_quickshell=False)
                state = install.load_state(host)
                state["applied_version"] = new_version
                install.update_quickshell_state(state, None, included=False)
                install.write_state(host, state)

            def verify_committed_state():
                snapshot_install._verify_committed_install(ROOT, manifest, install, host)

            def fail_after_verify_during_finalize():
                raise RuntimeError("injected package-finalization failure")

            with self.assertRaisesRegex(RuntimeError, "package-finalization failure"):
                snapshot_install.run_install_transaction(
                    managed_paths, stage_bootstrap, apply_managed_session,
                    verify_committed_state, fail_after_verify_during_finalize, root=host,
                )

            after = {
                path.as_posix(): snapshot_install._tree_signature(_host_path(host, path.as_posix()))
                for path in managed_paths
            }
            self.assertEqual(after, before)
            self.assertTrue(bundle.is_file())
            self.assertEqual(hashlib.sha256(bundle.read_bytes()).hexdigest(), bundle_digest)
            recovery_core._verify_bundle(bundle, host)
            self.assertIn("READY", recovery_core.status(host))

            config = _host_path(host, "/etc/niri/config.kdl")
            known_good_config = config.read_bytes()
            config.write_text("interrupted candidate write\n", encoding="utf-8")
            recovery_core.restore(bundle, host)
            self.assertEqual(config.read_bytes(), known_good_config)
            self.assertEqual(hashlib.sha256(bundle.read_bytes()).hexdigest(), bundle_digest)
            recovery_core._verify_bundle(bundle, host)


if __name__ == "__main__":
    unittest.main()
