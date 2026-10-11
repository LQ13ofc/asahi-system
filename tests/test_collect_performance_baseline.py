import importlib.machinery
import importlib.util
import json
import os
import pathlib
import runpy
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
            "MemTotal: 1000 kB\nMemAvailable: 400 kB\nMemFree: 100 kB\nBuffers: 10 kB\n"
            "Cached: 200 kB\nShmem: 20 kB\nSReclaimable: 30 kB\nSUnreclaim: 5 kB\nSlab: 35 kB\n"
            "AnonPages: 300 kB\nMapped: 40 kB\nKernelStack: 2 kB\nPageTables: 3 kB\n"
            "Unevictable: 4 kB\nActive: 500 kB\nInactive: 200 kB\n"
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
        """Run niri+ memory through the installed split lib/share layout."""
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            local = root / "usr/local"
            script_dir = local / "share/niri-plus/scripts"
            package_dir = local / "lib/niri-plus/niri_plus"
            bin_dir = local / "bin"
            script_dir.mkdir(parents=True)
            bin_dir.mkdir(parents=True)
            installed_script = script_dir / "collect-performance-baseline"
            shutil.copy2(SCRIPT, installed_script)
            source_package = SCRIPT.parents[1] / "niri_plus"
            shutil.copytree(source_package, package_dir)
            data_dir = local / "share/niri-plus"
            (data_dir / "VERSION").write_text("0.1.10\n")
            installed_cli = bin_dir / "niri+"
            bootstrap = runpy.run_path(
                str(SCRIPT.parents[1] / "scripts/bootstrap-niri-plus"), run_name="bootstrap-test",
            )
            launcher_text = bootstrap["launcher"]()
            launcher_text = launcher_text.replace("/usr/local/share/niri-plus", str(data_dir))
            launcher_text = launcher_text.replace("/usr/local/lib/niri-plus", str(local / "lib/niri-plus"))
            installed_cli.write_text(launcher_text)
            installed_cli.chmod(0o755)

            # Isolation makes accidentally importing this checkout impossible.
            env = {**os.environ, "PYTHONPATH": ""}
            version = subprocess.run(
                [sys.executable, "-I", str(installed_cli), "--version"], cwd=temp, env=env,
                text=True, capture_output=True, check=False, timeout=15,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn("Niri+ 0.1.10", version.stdout)

            brightness_help = subprocess.run(
                [sys.executable, "-I", str(installed_cli), "brightness", "set", "--help"],
                cwd=temp, env=env, text=True, capture_output=True, check=False, timeout=15,
            )
            self.assertEqual(brightness_help.returncode, 0, brightness_help.stderr)
            self.assertIn("--device", brightness_help.stdout)

            output = root / "memory.json"
            completed = subprocess.run(
                [sys.executable, "-I", str(installed_cli), "memory", "--window", "0.5",
                 "--sample-period", "0.2", "--json-output", str(output)],
                cwd=temp, env=env, text=True, capture_output=True, check=False, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("PRIVACY WARNING", completed.stdout)
            self.assertLess(completed.stdout.index("PRIVACY WARNING"), completed.stdout.index("Niri+ memory diagnostic"))
            result = json.loads(output.read_text())
            self.assertEqual(result["mode"], "memory-diagnostic")
            self.assertTrue(result["read_only"])
            self.assertTrue(result["privacy"]["review_before_sharing"])
            self.assertIn("process_inventory", result["processes"])

            blocked_parent = root / "not-a-directory"
            blocked_parent.write_text("file")
            failed_output = subprocess.run(
                [sys.executable, "-I", str(installed_cli), "memory", "--window", "0.5",
                 "--sample-period", "0.2", "--json-output", str(blocked_parent / "memory.json")],
                cwd=temp, env=env, text=True, capture_output=True, check=False, timeout=30,
            )
            self.assertEqual(failed_output.returncode, 2)
            self.assertIn("cannot write memory diagnostic output", failed_output.stderr)

            installed_script.unlink()
            unavailable = subprocess.run(
                [sys.executable, "-I", str(installed_cli), "memory"], cwd=temp, env=env,
                text=True, capture_output=True, check=False, timeout=15,
            )
            self.assertEqual(unavailable.returncode, 2)
            self.assertIn("collector not found", unavailable.stderr)

    def test_memory_units_and_used_definition(self):
        memory = self.collector.memory()
        self.assertEqual(memory["mem_total_bytes"]["value"], 1_024_000)
        self.assertEqual(memory["mem_available_bytes"]["value"], 409_600)
        self.assertEqual(memory["used_bytes"]["value"], 614_400)
        self.assertEqual(memory["cached_bytes"]["value"], 204_800)
        self.assertEqual(memory["shmem_bytes"]["value"], 20_480)
        self.assertEqual(memory["swap_used_bytes"]["value"], 204_800)
        self.assertEqual(memory["sreclaimable_bytes"]["value"], 30_720)
        self.assertEqual(memory["sunreclaim_bytes"]["value"], 5_120)
        self.assertEqual(memory["kernel_slab_bytes"]["value"], 35_840)
        self.assertEqual(memory["anon_pages_bytes"]["value"], 307_200)
        self.assertIn("not process PSS", memory["meminfo_semantics"]["value"]["used_bytes"])
        scalar = collector_module.scalar_samples({"memory": memory})
        self.assertEqual(scalar["memory.used_bytes"], 614_400)
        self.assertEqual(scalar["memory.buffers_bytes"], 10_240)
        self.assertEqual(scalar["memory.slab_bytes"], 35_840)

    def test_invalid_negative_or_inconsistent_global_memory_is_unavailable(self):
        meminfo = self.root / "proc/meminfo"
        meminfo.write_text("MemTotal: 100 kB\nMemAvailable: 200 kB\nSwapTotal: 10 kB\nSwapFree: 20 kB\n")
        memory = self.collector.memory()
        self.assertEqual(memory["used_bytes"]["status"], collector_module.UNAVAILABLE)
        self.assertEqual(memory["swap_used_bytes"]["status"], collector_module.UNAVAILABLE)
        meminfo.write_text("MemTotal: -1 kB\nMemAvailable: 0 kB\n")
        memory = self.collector.memory()
        self.assertEqual(memory["mem_total_bytes"]["status"], collector_module.UNAVAILABLE)

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

    def test_drm_fdinfo_parser_accepts_standard_sizes_but_rejects_unknown_units(self):
        parsed = collector_module.benchmark_metrics.parse_drm_fdinfo(
            "drm-driver: asahi\ndrm-client-id: 42\ndrm-resident-memory: 2 MiB\n"
            "drm-resident-local: 8 KiB\n"
        )
        self.assertEqual(parsed["driver"], "asahi")
        self.assertEqual(parsed["resident_bytes_by_region"], {"memory": 2 * 1024 * 1024, "local": 8 * 1024})
        malformed = collector_module.benchmark_metrics.parse_drm_fdinfo(
            "drm-driver: asahi\ndrm-client-id: 42\ndrm-resident-memory: 3 GB\n"
        )
        self.assertEqual(malformed["malformed_keys"], ["drm-resident-memory"])

    def test_drm_fdinfo_deduplicates_same_client_across_pids_and_marks_unaccounted(self):
        rows = [{"pid": 101, "name": "niri", "start_time_ticks": 1010},
                {"pid": 102, "name": "qs", "start_time_ticks": 1020}]
        for pid, start_time in ((101, 1010), (102, 1020)):
            proc = self.root / f"proc/{pid}"
            proc.mkdir(parents=True)
            tail = ["S"] + ["0"] * 19
            tail[19] = str(start_time)
            (proc / "stat").write_text(f"{pid} (gpu client) " + " ".join(tail) + "\n")
            fdinfo = self.root / f"proc/{pid}/fdinfo"
            fdinfo.mkdir(parents=True)
            (fdinfo / "3").write_text(
                "drm-driver: asahi\ndrm-client-id: 7\ndrm-resident-memory: 2 MiB\n"
            )
        result = collector_module.benchmark_metrics.drm_fdinfo_snapshot(self.root, rows)
        self.assertEqual(result["status"], collector_module.AVAILABLE)
        self.assertEqual(result["value"]["resident_bytes_by_region"]["memory"], 2 * 1024 * 1024)
        self.assertEqual(result["clients"][0]["pids"], [101, 102])
        (self.root / "proc/102/fdinfo/3").write_text("drm-driver: asahi\ndrm-client-id: 9\n")
        result = collector_module.benchmark_metrics.drm_fdinfo_snapshot(self.root, rows)
        self.assertEqual(result["status"], collector_module.NOT_ACCOUNTED)
        self.assertIsNone(result["value"])

    def test_drm_fdinfo_discards_observations_if_pid_is_reused_during_scan(self):
        pid = 101
        start_time = 1010
        proc = self.root / f"proc/{pid}"
        proc.mkdir(parents=True)
        stat = proc / "stat"

        def write_stat(value):
            tail = ["S"] + ["0"] * 19
            tail[19] = str(value)
            stat.write_text(f"{pid} (gpu client) " + " ".join(tail) + "\n")

        write_stat(start_time)
        fdinfo = proc / "fdinfo"
        fdinfo.mkdir()
        fd = fdinfo / "3"
        fd.write_text("drm-driver: asahi\ndrm-client-id: 7\ndrm-resident-memory: 2 MiB\n")
        real_read_text = collector_module.benchmark_metrics.read_text

        def reuse_pid_after_fdinfo_read(path):
            result = real_read_text(path)
            if path == fd:
                write_stat(start_time + 1)
            return result

        with mock.patch.object(
            collector_module.benchmark_metrics, "read_text", side_effect=reuse_pid_after_fdinfo_read,
        ):
            result = collector_module.benchmark_metrics.drm_fdinfo_snapshot(
                self.root, [{"pid": pid, "name": "niri", "start_time_ticks": start_time}],
            )
        self.assertEqual(result["status"], collector_module.UNAVAILABLE)
        self.assertIsNone(result["value"])
        self.assertEqual(result["clients"], [])
        self.assertEqual(result["coverage"]["identity_changed"], 1)

    def test_drm_fdinfo_is_unavailable_when_process_identity_is_missing(self):
        pid = 101
        fdinfo = self.root / f"proc/{pid}/fdinfo"
        fdinfo.mkdir(parents=True)
        (fdinfo / "3").write_text(
            "drm-driver: asahi\ndrm-client-id: 7\ndrm-resident-memory: 2 MiB\n"
        )
        result = collector_module.benchmark_metrics.drm_fdinfo_snapshot(
            self.root, [{"pid": pid, "name": "niri"}],
        )
        self.assertEqual(result["status"], collector_module.UNAVAILABLE)
        self.assertIsNone(result["value"])
        self.assertEqual(result["coverage"]["identity_unavailable"], 1)
        self.assertEqual(result["clients"], [])

    def test_process_smaps_rollup_and_faults_use_live_metrics_path(self):
        proc = self.root / "proc/101"
        proc.mkdir()
        tail = ["S"] + ["0"] * 19
        tail[1], tail[7], tail[9], tail[11], tail[12], tail[19] = "1", "7", "2", "50", "10", "1234"
        (proc / "stat").write_text("101 (demo process) " + " ".join(tail) + "\n")
        (proc / "cmdline").write_bytes(b"/usr/bin/demo\0")
        (proc / "status").write_text("Uid:\t1000\t1000\t1000\t1000\nThreads:\t3\n")
        (proc / "cgroup").write_text("0::/user.slice/test.scope\n")
        (proc / "io").write_text("read_bytes: 10\nwrite_bytes: 20\n")
        (proc / "smaps_rollup").write_text("Rss: 100 kB\nPss: 70 kB\nPrivate_Clean: 10 kB\nPrivate_Dirty: 20 kB\nSwapPss: 3 kB\n")
        (proc / "exe").symlink_to("/usr/bin/demo")
        child = self.root / "proc/102"
        child.mkdir()
        child_tail = ["S"] + ["0"] * 19
        child_tail[1], child_tail[19] = "101", "1300"
        (child / "stat").write_text("102 (demo child) " + " ".join(child_tail) + "\n")
        (child / "cmdline").write_bytes(b"/usr/bin/demo-child\0")
        (child / "status").write_text("Uid:\t1000\t1000\t1000\t1000\nThreads:\t1\n")
        (child / "cgroup").write_text("0::/user.slice/test.scope\n")
        (child / "io").write_text("read_bytes: 0\nwrite_bytes: 0\n")
        (child / "smaps_rollup").write_text("Rss: 10 kB\nPss: 8 kB\nPrivate_Clean: 0 kB\nPrivate_Dirty: 2 kB\nSwapPss: 0 kB\n")
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
        self.assertEqual(item["threads"]["value"], 3)
        self.assertEqual(item["executable"]["value"], "/usr/bin/demo")
        self.assertEqual(item["runtime_seconds"]["status"], collector_module.AVAILABLE)
        self.assertEqual(item["children_count"]["value"], 1)
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

    def test_longitudinal_collection_uses_finite_schedule_and_atomic_partial_json(self):
        values = iter((100, 90, 200, 85))
        class FakeCollector:
            def memory_diagnostic(self):
                value = next(values)
                return {
                    "memory": {
                        "used_bytes": collector_module.metric(collector_module.AVAILABLE, value),
                        "mem_available_bytes": collector_module.metric(collector_module.AVAILABLE, 1000 - value),
                        "cached_bytes": collector_module.metric(collector_module.AVAILABLE, 30),
                        "swap_used_bytes": collector_module.metric(collector_module.AVAILABLE, 0),
                    },
                    "processes": {
                        "all_process_pss_bytes": collector_module.metric(collector_module.AVAILABLE, value // 2),
                        "all_process_private_dirty_bytes": collector_module.metric(collector_module.AVAILABLE, value // 4),
                        "same_uid_user_processes": {"pss_bytes": collector_module.metric(collector_module.AVAILABLE, value // 3)},
                        "graphics_components": {
                            "niri": {"pss_bytes": collector_module.metric(collector_module.AVAILABLE, value // 5)},
                            "quickshell": {"pss_bytes": collector_module.metric(collector_module.AVAILABLE, value // 6)},
                        },
                        "process_inventory": [],
                    },
                }

        clock = {"now": 0.0, "sleeps": []}
        def monotonic():
            return clock["now"]
        def sleeper(seconds):
            clock["sleeps"].append(seconds)
            clock["now"] += seconds
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp) / "series.json"
            messages = []
            result = collector_module.collect_memory_series(
                window=1,
                sample_period=0.5,
                output=output,
                collector_factory=FakeCollector,
                sleeper=sleeper,
                monotonic=monotonic,
                emit=messages.append,
            )
            saved = json.loads(output.read_text())
            self.assertFalse(list(pathlib.Path(temp).glob(".*.tmp")))
        self.assertEqual(result["schedule_seconds"], [0, 300, 900, 1800])
        self.assertTrue(result["complete"])
        self.assertEqual(len(saved["samples"]), 4)
        self.assertEqual([sample["scheduled_offset_seconds"] for sample in saved["samples"]], [0, 300, 900, 1800])
        self.assertEqual(clock["sleeps"], [300, 600, 900])
        self.assertIn("trend_analysis", saved)

    def test_longitudinal_collection_can_add_optional_sixty_minute_sample(self):
        class FakeCollector:
            def memory_diagnostic(self):
                return {"memory": {}, "processes": {"process_inventory": []}}

        clock = {"now": 0.0, "sleeps": []}
        def monotonic():
            return clock["now"]
        def sleeper(seconds):
            clock["sleeps"].append(seconds)
            clock["now"] += seconds
        result = collector_module.collect_memory_series(
            window=1,
            sample_period=0.5,
            include_60_minutes=True,
            collector_factory=FakeCollector,
            sleeper=sleeper,
            monotonic=monotonic,
            emit=lambda _line: None,
        )
        self.assertEqual(result["schedule_seconds"], [0, 300, 900, 1800, 3600])
        self.assertEqual(len(result["samples"]), 5)
        self.assertEqual(clock["sleeps"], [300, 600, 900, 1800])

    def test_cancelled_series_preserves_previous_json_and_completed_partial_samples(self):
        class FakeCollector:
            def memory_diagnostic(self):
                return {"memory": {}, "processes": {"process_inventory": []}}

        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp) / "series.json"
            output.write_text('{"previous": true}\n')
            messages = []

            def cancel(_seconds):
                raise KeyboardInterrupt

            with self.assertRaises(KeyboardInterrupt):
                collector_module.collect_memory_series(
                    window=1, sample_period=0.5, output=output, collector_factory=FakeCollector,
                    sleeper=cancel, monotonic=lambda: 0.0, emit=messages.append,
                )

            self.assertEqual(json.loads(output.read_text()), {"previous": True})
            partials = list(pathlib.Path(temp).glob("series.json.*.partial"))
            self.assertEqual(len(partials), 1)
            partial = json.loads(partials[0].read_text())
            self.assertFalse(partial["complete"])
            self.assertEqual(len(partial["samples"]), 1)
            self.assertTrue(partial["privacy"]["review_before_sharing"])
            self.assertTrue(any("PRIVACY WARNING" in line for line in messages))
            self.assertTrue(any(str(partials[0]) in line for line in messages))
            warning_index = next(index for index, line in enumerate(messages) if "PRIVACY WARNING" in line)
            sample_index = next(index for index, line in enumerate(messages) if "Sample 1/4" in line)
            self.assertLess(warning_index, sample_index)

    def test_memory_series_keyboard_interrupt_returns_cancel_status(self):
        argv = [
            "collect-performance-baseline", "--memory-series", "--memory-window", "0.5",
            "--memory-sample-period", "0.2",
        ]
        with mock.patch.object(collector_module.sys, "argv", argv), \
             mock.patch.object(collector_module, "collect_memory_series", side_effect=KeyboardInterrupt), \
             mock.patch.object(collector_module.sys, "stderr") as stderr:
            self.assertEqual(collector_module.main(), 130)
            self.assertIn("previous JSON destination is unchanged", "".join(
                call.args[0] for call in stderr.write.call_args_list
            ))

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

    def test_memory_cli_reports_json_output_failure_without_traceback(self):
        argv = [
            "collect-performance-baseline", "--memory-diagnostic", "--memory-window", "0.5",
            "--memory-sample-period", "0.2", "--output", "/unwritable/memory.json",
        ]
        with mock.patch.object(collector_module.sys, "argv", argv), \
             mock.patch.object(collector_module, "collect_memory_diagnostic", side_effect=OSError("permission denied")), \
             mock.patch.object(collector_module.sys, "stderr") as stderr:
            self.assertEqual(collector_module.main(), 2)
            self.assertIn("cannot write memory diagnostic output", "".join(call.args[0] for call in stderr.write.call_args_list))

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
