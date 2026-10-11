from __future__ import annotations

import json
import pathlib
import runpy
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP = runpy.run_path(str(ROOT / "scripts/niri-plus-rc-bootstrap"))
PLUGIN_COMMIT = "d2fe6d57dc2b86e5f433a0f6282732b8d03ca7f3"


def write_json(path: pathlib.Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


class CandidatePluginSelectionTests(unittest.TestCase):
    def test_standalone_qs_binary_does_not_opt_into_candidate_plugin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            binary = root / "usr/bin/qs"
            binary.parent.mkdir(parents=True)
            binary.write_text("independent engine", encoding="utf-8")
            self.assertEqual(BOOTSTRAP["_candidate_plugin_plan"](root), (False, "none"))

    def test_explicit_opt_in_selects_first_install(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(BOOTSTRAP["_candidate_plugin_plan"](
                pathlib.Path(temp), explicit_opt_in=True,
            ), (True, "install"))

    def test_managed_manifest_selects_joint_update(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            write_json(root / "var/lib/niri-plus/plugins.json", {
                "schema_version": 1,
                "plugins": {"quickshell": {
                    "repository": "https://github.com/LQ13ofc/quickshell-.git",
                    "commit": PLUGIN_COMMIT,
                }},
            })
            self.assertEqual(BOOTSTRAP["_candidate_plugin_plan"](root), (True, "update"))

    def test_legacy_019_pin_selects_migration_without_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            write_json(root / "var/lib/niri-plus/bootstrap.json", {
                "schema_version": 2,
                "quickshell_expected_commit": PLUGIN_COMMIT,
            })
            self.assertEqual(BOOTSTRAP["_candidate_plugin_plan"](root), (True, "migrate"))

    def test_manifest_symlink_or_invalid_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other:
            root = pathlib.Path(temp)
            manifest = root / "var/lib/niri-plus/plugins.json"
            manifest.parent.mkdir(parents=True)
            target = pathlib.Path(other) / "manifest.json"
            write_json(target, {"schema_version": 1, "plugins": {}})
            manifest.symlink_to(target)
            with self.assertRaises(BOOTSTRAP["BootstrapError"]):
                BOOTSTRAP["_candidate_plugin_plan"](root)

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            write_json(root / "var/lib/niri-plus/plugins.json", {"schema_version": 9, "plugins": {}})
            with self.assertRaises(BOOTSTRAP["BootstrapError"]):
                BOOTSTRAP["_candidate_plugin_plan"](root)

    def test_qs_process_probe_distinguishes_running_absent_and_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = pathlib.Path(temp)
            process = proc / "1234"
            process.mkdir()
            (process / "cmdline").write_bytes(b"/usr/bin/qs\0--path\0shell.qml\0")
            self.assertIs(BOOTSTRAP["_quickshell_running"](proc), True)
            (process / "cmdline").write_bytes(b"/usr/bin/foot\0")
            (process / "exe").symlink_to("/usr/bin/foot")
            self.assertIs(BOOTSTRAP["_quickshell_running"](proc), False)
        self.assertIsNone(BOOTSTRAP["_quickshell_running"](pathlib.Path("/path/that/does/not/exist")))


if __name__ == "__main__":
    unittest.main()
