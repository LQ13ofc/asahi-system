from __future__ import annotations

import pathlib
import runpy
import tempfile
import unittest

from niri_plus.install import update_quickshell_state


BOOTSTRAP = pathlib.Path(__file__).resolve().parents[1] / "scripts/bootstrap-niri-plus"
preserve_optional_quickshell = runpy.run_path(
    str(BOOTSTRAP), run_name="niri_plus_bootstrap_test_helpers",
)["preserve_optional_quickshell"]


class OptionalQuickshellTests(unittest.TestCase):
    def test_core_only_upgrade_preserves_existing_visual_runtime_and_pin_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            current = root / "current"
            staged = root / "staged"
            (current / "quickshell").mkdir(parents=True)
            (current / "quickshell/shell.qml").write_text("visual runtime\n", encoding="utf-8")
            (current / "quickshell.snapshot.json").write_text('{"commit":"abc"}\n', encoding="utf-8")
            staged.mkdir()

            preserve_optional_quickshell(current, staged)

            self.assertEqual((staged / "quickshell/shell.qml").read_text(encoding="utf-8"), "visual runtime\n")
            self.assertEqual((staged / "quickshell.snapshot.json").read_text(encoding="utf-8"), '{"commit":"abc"}\n')
            self.assertTrue((current / "quickshell/shell.qml").is_file())

    def test_core_only_install_keeps_old_visual_pin_but_fresh_install_has_none(self):
        state = {"quickshell": {"expected_commit": "a" * 40, "known_good_commit": "b" * 40}}
        update_quickshell_state(state, "c" * 40, included=False)
        self.assertEqual(state["quickshell"]["expected_commit"], "a" * 40)
        self.assertEqual(state["quickshell"]["known_good_commit"], "b" * 40)

        fresh = {}
        update_quickshell_state(fresh, "c" * 40, included=False)
        self.assertIsNone(fresh["quickshell"]["expected_commit"])

    def test_explicit_visual_install_updates_the_expected_pin(self):
        state = {"quickshell": {"expected_commit": "a" * 40}}
        update_quickshell_state(state, "c" * 40, included=True)
        self.assertEqual(state["quickshell"]["expected_commit"], "c" * 40)


if __name__ == "__main__":
    unittest.main()
