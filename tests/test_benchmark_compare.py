import copy
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from niri_plus import benchmark, benchmark_compare as compare


def metric(value, status="AVAILABLE"):
    result = {"status": status}
    if value is not None:
        result["value"] = value
    return result


def command(value):
    return metric({"returncode": 0, "stdout": value, "stderr": ""})


def run(profile, available, session_pss, quickshell_pss=0, *, niri_version="Niri 26.04", missing_psi=False, missing_pss=False):
    components = {
        name: {
            "pss_bytes": metric(0 if name != "quickshell" else quickshell_pss),
            "cpu_seconds_delta": metric(0.0),
        }
        for name in (
            "niri", "quickshell", "quickshell_auxiliary", "polkit_agent", "pipewire",
            "wireplumber", "networkmanager", "xwayland_satellite",
        )
    }
    processes = [
        {"pid": 101, "name": "niri", "argv": ["/usr/bin/niri"], "pss_bytes": metric(20)},
    ]
    if profile == "niri-quickshell":
        processes.append({"pid": 102, "name": "qs", "argv": ["/usr/bin/qs"], "pss_bytes": metric(quickshell_pss)})
    psi_metric = metric(None, "UNAVAILABLE") if missing_psi else metric({
        "some_total_usec_delta": 12,
        "full_total_usec_delta": 0,
    })
    return {
        "schema_version": 2,
        "profile": profile,
        "profile_validation": {"status": "OK", "evidence": {}},
        "collector": {
            "read_only": True,
            "host_modified": False,
            "observation_window_seconds": 10.0,
            "profile_sample_period_seconds": 0.5,
        },
        "system": {
            "architecture": metric("aarch64"),
            "page_size_bytes": metric(16384),
            "fedora_asahi_release": metric({"ID": "fedora-asahi-remix", "VERSION_ID": "44"}),
            "asahi_hardware": metric("Apple MacBook Air (M1, 2020)"),
            "uname": command("Linux mba 6.17.0-asahi #1 aarch64"),
            "session_type": metric("wayland"),
            "niri_plus_version": metric("0.1.6"),
            "niri_compositor_version": command(niri_version),
            "quickshell_version": command("Quickshell 0.3.1"),
            "quickshell_expected_commit": metric("55e92880d0aff75d235f283c839ec0990eaa9e17"),
            "quickshell_installed_commit": metric("55e92880d0aff75d235f283c839ec0990eaa9e17"),
            "asahi_system_commit": command("58d7f45ec244900e69bbee7e927cdea3aa53e7f1"),
        },
        "memory": {
            "mem_total_bytes": metric(1024),
            "mem_available_bytes": metric(available),
            "used_bytes": metric(500 - available),
            "mem_free_bytes": metric(20),
            "buffers_bytes": metric(5),
            "cached_bytes": metric(30),
            "shmem_bytes": metric(4),
            "sreclaimable_bytes": metric(2),
            "sunreclaim_bytes": metric(1),
            "slab_bytes": metric(3),
            "anon_pages_bytes": metric(60),
            "mapped_bytes": metric(8),
            "kernel_stack_bytes": metric(1),
            "page_tables_bytes": metric(1),
            "unevictable_bytes": metric(0),
            "active_bytes": metric(100),
            "inactive_bytes": metric(200),
            "swap_used_bytes": metric(0),
        },
        "graphics_memory": {"drm_fdinfo": metric({"resident_bytes_by_region": {"memory": 9}})},
        "memory_scopes": {
            "session": {
                "pss_bytes": metric(None, "UNAVAILABLE") if missing_pss else metric(session_pss),
                "swap_pss_bytes": metric(0),
                "minor_page_faults_delta": metric(1),
                "major_page_faults_delta": metric(0),
                "pid_count": len(processes),
            },
        },
        "components": components,
        "cpu": {
            "idle_percent_delta": metric(99),
            "busy_percent_delta": metric(1),
            "context_switches_delta": metric(50),
            "interrupts_delta": metric(30),
        },
        "psi": {
            "delta": {
                key: psi_metric for key in ("cpu", "memory", "io")
            },
        },
        "systemd_user_cgroup": {"end": {"memory_current_bytes": metric(100_000)}},
        "memory_management": {
            "zswap_usage": {
                "stored_pages": metric("2"),
                "pool_total_size": metric("100"),
                "written_back_pages": metric("0"),
                "pool_limit_hit": metric("0"),
            },
        },
        "session": {"processes": metric(processes)},
    }


def capture(profile, available, session_pss, **kwargs):
    baseline = available[0]
    runs = [run(profile, value, session_pss + (value - baseline), kwargs.get("quickshell_pss", 0),
                niri_version=kwargs.get("niri_version", "Niri 26.04"),
                missing_psi=kwargs.get("missing_psi", False),
                missing_pss=kwargs.get("missing_pss_run") == index)
            for index, value in enumerate(available)]
    return {
        "schema_version": 2,
        "read_only": True,
        "profile": profile,
        "profile_validation": {"status": "OK", "runs": [{} for _ in runs]},
        "runs": runs,
    }


class BenchmarkCompareTests(unittest.TestCase):
    def setUp(self):
        self.plasma = capture("plasma", [100, 101, 99, 100, 100], 80)
        self.core = capture("niri-core", [110, 111, 109, 110, 110], 50)
        self.quickshell = capture(
            "niri-quickshell",
            [108, 109, 107, 108, 108],
            55,
            quickshell_pss=50,
        )

    def test_known_medians_mad_absolute_and_percent_deltas(self):
        result = compare.compare_captures(self.plasma, self.core, self.quickshell)
        self.assertEqual(result["comparability"]["status"], "COMPATIBLE")
        item = result["comparisons"]["A_plasma_to_B_niri_core"]["memory.available_bytes"]
        self.assertEqual(item["baseline"], {"n": 5, "median": 100.0, "mad": 0.0, "min": 99.0, "max": 101.0})
        self.assertEqual(item["candidate"]["median"], 110.0)
        self.assertEqual(item["delta"], 10.0)
        self.assertEqual(item["delta_percent"], 10.0)
        self.assertEqual(item["classification"], "MEASURED_INCREASE")
        qs_cost = result["comparisons"]["B_niri_core_to_C_niri_quickshell"]["memory.session_pss_bytes"]
        self.assertEqual(qs_cost["delta"], 5.0)
        self.assertEqual(qs_cost["classification"], "MEASURED_INCREASE")

    def test_noisy_overlap_is_inconclusive_not_claimed_as_significance(self):
        plasma = capture("plasma", [100, 110, 90, 105, 95], 80)
        core = capture("niri-core", [101, 111, 91, 106, 96], 80)
        result = compare.compare_captures(plasma, core, self.quickshell)
        item = result["comparisons"]["A_plasma_to_B_niri_core"]["memory.available_bytes"]
        self.assertEqual(item["classification"], "INCONCLUSIVE")
        self.assertIn("not a significance test", item["classification_rule"])

    def test_one_missing_sample_makes_metric_unavailable(self):
        core = capture("niri-core", [110, 111, 109, 110, 110], 50, missing_pss_run=2)
        result = compare.compare_captures(self.plasma, core, self.quickshell)
        item = result["comparisons"]["A_plasma_to_B_niri_core"]["memory.session_pss_bytes"]
        self.assertEqual(item["classification"], "UNAVAILABLE")
        self.assertEqual(item["missing_runs"]["candidate"], 1)

    def test_incompatible_compositor_versions_make_numeric_deltas_inconclusive(self):
        core = copy.deepcopy(self.core)
        core["runs"][2]["system"]["niri_compositor_version"] = command("Niri 26.05")
        result = compare.compare_captures(self.plasma, core, self.quickshell)
        self.assertEqual(result["comparability"]["status"], "INCOMPATIBLE")
        item = result["comparisons"]["A_plasma_to_B_niri_core"]["memory.available_bytes"]
        self.assertEqual(item["classification"], "INCONCLUSIVE")
        self.assertTrue(any("changes within the capture" in issue for issue in result["comparability"]["issues"]))

    def test_missing_required_metadata_prevents_claiming_a_measurement(self):
        core = copy.deepcopy(self.core)
        core["runs"][0]["system"]["quickshell_installed_commit"] = metric(None, "UNAVAILABLE")
        result = compare.compare_captures(self.plasma, core, self.quickshell)
        self.assertEqual(result["comparability"]["status"], "UNVERIFIED")
        self.assertEqual(
            result["comparisons"]["A_plasma_to_B_niri_core"]["memory.available_bytes"]["classification"],
            "INCONCLUSIVE",
        )

    def test_different_observation_windows_make_profiles_incompatible(self):
        core = copy.deepcopy(self.core)
        for run in core["runs"]:
            run["collector"]["observation_window_seconds"] = 20.0
        result = compare.compare_captures(self.plasma, core, self.quickshell)
        self.assertEqual(result["comparability"]["status"], "INCOMPATIBLE")
        self.assertTrue(any("different observation windows" in item for item in result["comparability"]["issues"]))
        self.assertTrue(result["conditions_not_automatically_verified"])

    def test_profile_and_read_only_validation_rejects_bad_input(self):
        bad = copy.deepcopy(self.plasma)
        bad["runs"][1]["profile"] = "niri-core"
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "capture.json"
            path.write_text(json.dumps(bad))
            with self.assertRaisesRegex(compare.ComparisonError, "run 2 is not profile"):
                compare._validate_capture(path, "plasma")
            bad["runs"][1]["profile"] = "plasma"
            bad["runs"][1]["collector"]["host_modified"] = True
            path.write_text(json.dumps(bad))
            with self.assertRaisesRegex(compare.ComparisonError, "no-host-change"):
                compare._validate_capture(path, "plasma")

    def test_process_inventory_identifies_quickshell_increment(self):
        result = compare.compare_captures(self.plasma, self.core, self.quickshell)
        inventory = result["process_inventory"]
        self.assertEqual(
            inventory["quickshell_incremental_processes"]["present_only_with_quickshell"],
            ["qs [qs]"],
        )
        self.assertEqual(
            inventory["largest_pss_by_profile"]["niri_quickshell"][0]["process"],
            "qs [qs]",
        )

    def test_inventory_does_not_impute_zero_pss_when_process_is_absent(self):
        candidate = copy.deepcopy(self.quickshell)
        candidate["runs"][1]["session"]["processes"]["value"] = [
            row for row in candidate["runs"][1]["session"]["processes"]["value"]
            if row["name"] != "qs"
        ]
        result = compare.compare_captures(self.plasma, self.core, candidate)
        qs = result["process_inventory"]["by_profile"]["niri_quickshell"]["qs [qs]"]
        self.assertEqual(qs["runs_present"], 4)
        self.assertEqual(qs["runs_with_pss"], 4)
        self.assertEqual(qs["median_pss_bytes"], 50)

    def test_inventory_missing_pss_is_unavailable_not_a_partial_median(self):
        candidate = copy.deepcopy(self.quickshell)
        rows = candidate["runs"][2]["session"]["processes"]["value"]
        for row in rows:
            if row["name"] == "qs":
                row["pss_bytes"] = metric(None, "UNAVAILABLE")
        result = compare.compare_captures(self.plasma, self.core, candidate)
        qs = result["process_inventory"]["by_profile"]["niri_quickshell"]["qs [qs]"]
        self.assertEqual(qs["runs_present"], 5)
        self.assertEqual(qs["runs_with_pss"], 4)
        self.assertIsNone(qs["median_pss_bytes"])

    def test_markdown_keeps_global_memory_and_pss_separate(self):
        result = compare.compare_captures(self.plasma, self.core, self.quickshell)
        text = compare.render_markdown(result)
        self.assertIn("memory.available_bytes", text)
        self.assertIn("memory.session_pss_bytes", text)
        self.assertIn("PSS and cgroup memory remain separate", text)
        self.assertIn("B niri core to C niri quickshell", text)

    def test_comparator_exposes_non_additive_meminfo_and_optional_drm_fields(self):
        run = self.core["runs"][0]
        for name in ("memory.buffers_bytes", "memory.slab_bytes", "memory.active_bytes", "memory.inactive_bytes"):
            path, _unit = compare.METRIC_PATHS[name]
            self.assertIsNotNone(compare._walk(run, path), name)
        drm_path, _unit = compare.METRIC_PATHS["graphics.drm_resident_system_memory_bytes"]
        self.assertEqual(compare._walk(run, drm_path), 9)

    def test_atomic_report_write_preserves_existing_report_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "report.md"
            path.write_text("old complete report")
            with mock.patch.object(compare.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    compare.write_atomic(path, "new report")
            self.assertEqual(path.read_text(), "old complete report")
            self.assertEqual(list(pathlib.Path(temp).glob(".*.tmp")), [])

    def test_compare_command_writes_markdown_and_optional_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            inputs = {
                "plasma": root / "plasma.json",
                "niri-core": root / "core.json",
                "niri-quickshell": root / "quickshell.json",
            }
            for profile, path, values in (
                ("plasma", inputs["plasma"], self.plasma),
                ("niri-core", inputs["niri-core"], self.core),
                ("niri-quickshell", inputs["niri-quickshell"], self.quickshell),
            ):
                path.write_text(json.dumps(values))
            markdown = root / "report.md"
            json_output = root / "report.json"
            self.assertEqual(benchmark.compare_benchmarks(
                str(inputs["plasma"]), str(inputs["niri-core"]), str(inputs["niri-quickshell"]),
                str(markdown), str(json_output),
            ), 0)
            self.assertIn("A/B/C benchmark comparison", markdown.read_text())
            self.assertEqual(json.loads(json_output.read_text())["comparability"]["status"], "COMPATIBLE")


if __name__ == "__main__":
    unittest.main()
