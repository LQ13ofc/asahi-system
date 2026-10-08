import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "collect-performance-baseline"
LOADER = importlib.machinery.SourceFileLoader("collect_performance_baseline", str(SCRIPT))
SPEC = importlib.util.spec_from_loader("collect_performance_baseline", LOADER)
collector_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector_module)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        (self.root / "proc").mkdir()
        (self.root / "sys").mkdir()
        (self.root / "etc").mkdir()
        (self.root / "etc/os-release").write_text('NAME="Fedora Linux"\nVERSION_ID="44"\n')
        (self.root / "proc/meminfo").write_text(
            "MemTotal: 1000 kB\nMemAvailable: 400 kB\nCached: 200 kB\nShmem: 20 kB\n"
            "SwapTotal: 500 kB\nSwapFree: 300 kB\n"
        )
        (self.root / "proc/uptime").write_text("123.5 20.0\n")
        (self.root / "proc/stat").write_text("cpu 100 2 50 300 10 4 4 0 0 0\nctxt 900\nintr 1200\n")
        (self.root / "proc/loadavg").write_text("0.10 0.20 0.30 1/20 200\n")
        (self.root / "proc/pressure").mkdir()
        for kind in ("cpu", "memory", "io"):
            (self.root / f"proc/pressure/{kind}").write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        (self.root / "proc/modules").write_text("brcmfmac 1 0 - Live 0\ncfg80211 1 0 - Live 0\n")
        (self.root / "proc/self").mkdir()
        (self.root / "proc/self/cgroup").write_text("0::/user.slice/test.scope\n")
        cgroup = self.root / "sys/fs/cgroup/user.slice/test.scope"
        cgroup.mkdir(parents=True)
        (cgroup / "memory.current").write_text("12345\n")
        (cgroup / "memory.stat").write_text("anon 100\nfile 20\n")
        (cgroup / "memory.events").write_text("low 0\nhigh 0\n")
        (cgroup / "memory.pressure").write_text("some avg10=0.00 total=0\n")
        (cgroup / "cpu.stat").write_text("usage_usec 40\n")
        (cgroup / "io.stat").write_text("8:0 rbytes=0 wbytes=0\n")
        self.collector = collector_module.Collector(self.root, observation_window=0)

    def tearDown(self):
        self.temp.cleanup()

    def test_installed_collector_imports_modules_from_split_lib_share_layout(self):
        """Regression: installed script lives in share/, Python package in lib/."""
        with tempfile.TemporaryDirectory() as temp:
            local = pathlib.Path(temp) / "usr/local"
            script_dir = local / "share/niri-plus/scripts"
            package_dir = local / "lib/niri-plus/niri_plus"
            script_dir.mkdir(parents=True)
            package_dir.mkdir(parents=True)
            installed_script = script_dir / "collect-performance-baseline"
            shutil.copy2(SCRIPT, installed_script)
            source_package = SCRIPT.parents[1] / "niri_plus"
            for filename in ("__init__.py", "benchmark_metrics.py"):
                shutil.copy2(source_package / filename, package_dir / filename)

            # Isolation makes accidentally importing this checkout impossible.
            completed = subprocess.run(
                [sys.executable, "-I", str(installed_script), "--help"],
                cwd=temp,
                env={**os.environ, "PYTHONPATH": ""},
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("--profile", completed.stdout)

    def test_memory_units_and_used_definition(self):
        memory = self.collector.memory()
        self.assertEqual(memory["mem_total_bytes"]["value"], 1_024_000)
        self.assertEqual(memory["mem_available_bytes"]["value"], 409_600)
        self.assertEqual(memory["used_bytes"]["value"], 614_400)
        self.assertEqual(memory["cached_bytes"]["value"], 204_800)
        self.assertEqual(memory["shmem_bytes"]["value"], 20_480)
        self.assertEqual(memory["swap_used_bytes"]["value"], 204_800)

    def test_absent_kernel_interfaces_are_status_values_not_exceptions(self):
        memory = self.collector.memory()
        self.assertEqual(memory["zswap_config"]["enabled"]["status"], collector_module.UNAVAILABLE)
        self.assertEqual(memory["mglru_enabled"]["status"], collector_module.UNAVAILABLE)
        self.assertEqual(memory["page_cluster"]["status"], collector_module.UNAVAILABLE)
        cgroup = self.collector.cgroup()
        self.assertEqual(cgroup["memory_current"]["value"], 12345)
        self.assertEqual(cgroup["memory_stat"]["value"]["anon"], 100)

    def test_permission_denied_has_distinct_status(self):
        with mock.patch.object(collector_module, "read_text", return_value=(None, "[Errno 13] Permission denied")):
            item = self.collector.text_metric("/sys/kernel/debug/example")
        self.assertEqual(item["status"], collector_module.PERMISSION_REQUIRED)

    def test_missing_cgroup_v2_keeps_all_requested_metrics_in_schema(self):
        (self.root / "proc/self/cgroup").write_text("0::/missing.scope\n")
        result = self.collector.cgroup()
        for name in ("unified", "path", "memory_current", "memory_stat", "memory_events", "memory_pressure", "cpu_stat", "io_stat"):
            self.assertIn(name, result)
        self.assertEqual(result["unified"]["status"], collector_module.NOT_APPLICABLE)

    def test_process_smaps_rollup_and_faults_use_live_metrics_path(self):
        proc = self.root / "proc/101"
        proc.mkdir()
        tail = ["S"] + ["0"] * 19
        tail[1], tail[7], tail[9], tail[11], tail[12], tail[19] = "1", "7", "2", "50", "10", "1234"
        (proc / "stat").write_text("101 (demo process) " + " ".join(tail) + "\n")
        (proc / "cmdline").write_bytes(b"/usr/bin/demo\0")
        (proc / "status").write_text("Uid:\t1000\t1000\t1000\t1000\n")
        (proc / "cgroup").write_text("0::/user.slice/test.scope\n")
        (proc / "io").write_text("read_bytes: 10\nwrite_bytes: 20\n")
        (proc / "smaps_rollup").write_text("Rss: 100 kB\nPss: 70 kB\nPrivate_Clean: 10 kB\nPrivate_Dirty: 20 kB\nSwapPss: 3 kB\n")
        processes, outcomes = collector_module.benchmark_metrics.process_snapshot(self.root, 100)
        item = next(row for row in processes if row["pid"] == 101)
        self.assertEqual(item["name"], "demo process")
        self.assertEqual(item["pss_bytes"]["value"], 70 * 1024)
        self.assertEqual(item["rss_bytes"]["value"], 100 * 1024)
        self.assertEqual(item["private_clean_bytes"]["value"], 10 * 1024)
        self.assertEqual(item["private_dirty_bytes"]["value"], 20 * 1024)
        self.assertEqual(item["swap_pss_bytes"]["value"], 3 * 1024)
        self.assertEqual(item["minor_page_faults"]["value"], 7)
        self.assertEqual(item["major_page_faults"]["value"], 2)
        self.assertGreaterEqual(outcomes["available"], 1)
        self.assertEqual(outcomes["identity_changed"], 0)

    def test_profile_validation_failure_does_not_write_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp) / "invalid-result.json"
            failed = {
                "profile_validation": {
                    "status": collector_module.benchmark_metrics.PROFILE_FAIL,
                    "evidence": {"failures": ["wrong session"]},
                }
            }
            argv = [
                "collect-performance-baseline",
                "--profile", "niri-core",
                "--runs", "1",
                "--window", "1",
                "--output", str(output),
            ]
            with mock.patch.object(collector_module.sys, "argv", argv), \
                 mock.patch.object(collector_module.Collector, "snapshot", return_value=failed):
                self.assertEqual(collector_module.main(), 3)
            self.assertFalse(output.exists())

    def test_atomic_output_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp) / "baseline.json"
            output.write_text("previous complete result\n")
            with mock.patch.object(collector_module.os, "replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaisesRegex(OSError, "simulated replace failure"):
                    collector_module.write_output_atomic(output, '{"new": true}\n')
            self.assertEqual(output.read_text(), "previous complete result\n")
            self.assertEqual(list(pathlib.Path(temp).glob("*.tmp")), [])
            self.assertEqual(list(pathlib.Path(temp).glob(".*.tmp")), [])

    def test_atomic_output_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp) / "nested" / "baseline.json"
            collector_module.write_output_atomic(output, '{"complete": true}\n')
            self.assertEqual(json.loads(output.read_text()), {"complete": True})
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_overhead_diagnostic_is_separate_and_never_subtracts_estimates(self):
        snapshot = {
            "collector": {"read_only": True, "host_modified": False, "process_scans": {
                "start": {"proc_file_reads": 10, "scan_wall_seconds": 0.01},
                "end": {"proc_file_reads": 10, "scan_wall_seconds": 0.01},
            }}
        }
        with mock.patch.object(collector_module.Collector, "snapshot", return_value=snapshot):
            result = collector_module.diagnose_collector_overhead(1)
        self.assertEqual(result["mode"], "collector-overhead-diagnostic")
        self.assertTrue(result["read_only"])
        self.assertFalse(result["subtracted_from_benchmark"])
        self.assertIn("process_scans", result["runs"][0])
        self.assertIn("process_cpu_seconds", result["runs"][0])

    def test_cloud_architecture_and_schema_are_partial_and_read_only(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        with mock.patch.object(collector_module.platform, "machine", return_value="x86_64"), \
             mock.patch.object(collector_module.platform, "release", return_value="6.0.0-cloud"), \
             mock.patch.object(collector_module, "command", return_value=collector_module.metric(collector_module.UNAVAILABLE, note="mocked")), \
             mock.patch("pathlib.Path.home", return_value=self.root / "no-home"):
            snapshot = self.collector.snapshot()
        after = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        self.assertEqual(before, after)
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertTrue(snapshot["collector"]["read_only"])
        self.assertIn("process_observation_window_seconds", snapshot["collector"])
        self.assertEqual(snapshot["system"]["architecture"]["value"], "x86_64")
        self.assertEqual(snapshot["system"]["asahi_hardware"]["status"], collector_module.NOT_APPLICABLE)
        self.assertEqual(snapshot["system"]["niri_plus_version"]["status"], collector_module.AVAILABLE)
        self.assertIn("quickshell_expected_commit", snapshot["system"])
        self.assertIn("niri_compositor_version", snapshot["system"])
        self.assertEqual(snapshot["cgroup_v2"]["memory_current"]["value"], 12345)
        encoded = json.dumps(snapshot)
        self.assertIn('"status": "AVAILABLE"', encoded)
        self.assertIn('"status": "UNAVAILABLE"', encoded)
        self.assertIn('"status": "NOT_APPLICABLE"', encoded)


if __name__ == "__main__":
    unittest.main()
