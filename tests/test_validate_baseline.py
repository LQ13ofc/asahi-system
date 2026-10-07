from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validate_baseline import BASELINE_RELATIVE, REPOSITORY_ROOT, inspect_baseline


class BaselineValidationTests(unittest.TestCase):
    def test_tracked_baseline_matches_the_declared_target(self) -> None:
        errors, summary = inspect_baseline(REPOSITORY_ROOT)

        self.assertEqual([], errors)
        self.assertEqual("aarch64", summary["architecture"])
        self.assertIn("44", summary["release"])
        self.assertGreater(int(summary["package_count"]), 0)

    def test_missing_files_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / BASELINE_RELATIVE).mkdir(parents=True)

            errors, _ = inspect_baseline(root)

        self.assertTrue(any("missing baseline file: os-release.txt" in error for error in errors))

    def test_wrong_fedora_release_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = REPOSITORY_ROOT / BASELINE_RELATIVE
            target = root / BASELINE_RELATIVE
            shutil.copytree(source, target)
            release_file = target / "os-release.txt"
            release_file.write_text(
                release_file.read_text(encoding="utf-8").replace("VERSION_ID=44", "VERSION_ID=43", 1),
                encoding="utf-8",
            )

            errors, _ = inspect_baseline(root)

        self.assertIn("os-release.txt must report Fedora version 44", errors)

    def test_empty_zram_capture_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = REPOSITORY_ROOT / BASELINE_RELATIVE
            target = root / BASELINE_RELATIVE
            shutil.copytree(source, target)
            (target / "zram.txt").write_text("", encoding="utf-8")

            errors, _ = inspect_baseline(root)

        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
