from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.graphify_project import (
    EXPECTED_REPOSITORIES,
    LOCK_PATH,
    canonical_github_remote,
    graphify_command,
    load_lock,
    verify_repo,
)


class GraphifyToolTests(unittest.TestCase):
    def test_lock_is_an_immutable_upstream_pin(self) -> None:
        lock = load_lock(LOCK_PATH)
        self.assertEqual(lock["version"], "0.9.84")
        self.assertEqual(lock["commit"], "1d1e03b4c88abf94a3f450c838e3c744ae514ef9")
        self.assertEqual(lock["license"], "Apache-2.0")

    def test_command_is_pinned_and_local_code_only(self) -> None:
        lock = load_lock(LOCK_PATH)
        target = Path("/tmp/asahi-system").resolve()
        command = graphify_command("uvx", lock, target)
        self.assertEqual(command[0:3], [
            "uvx",
            "--from",
            "git+https://github.com/Graphify-Labs/graphify.git@1d1e03b4c88abf94a3f450c838e3c744ae514ef9",
        ])
        self.assertIn("--code-only", command)
        self.assertIn("--no-cluster", command)
        self.assertEqual(command[command.index("--out") + 1], str(target))
        self.assertNotIn("--backend", command)

    def test_github_ssh_and_https_remotes_normalize_to_same_repo(self) -> None:
        self.assertEqual(
            canonical_github_remote("git@github.com:LQ13ofc/quickshell-.git"),
            canonical_github_remote(EXPECTED_REPOSITORIES["quickshell"][1]),
        )

    def test_wrong_remote_is_rejected_without_echoing_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "remote",
                    "add",
                    "origin",
                    "https://test-user:test-password@github.com/elsewhere/private.git",
                ],
                check=True,
                capture_output=True,
            )
            with self.assertRaises(ValueError) as error:
                verify_repo(root, EXPECTED_REPOSITORIES["quickshell"][1], "quickshell-")
            self.assertNotIn("test-password", str(error.exception))


if __name__ == "__main__":
    unittest.main()
