import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from niri_plus import benchmark, benchmark_metrics


class BenchmarkProfileTests(unittest.TestCase):
    @staticmethod
    def _proc_stat(pid: int, comm: str, start_time: int) -> str:
        tail = ["S"] + ["0"] * 19
        tail[1] = "1"
        tail[7] = "3"
        tail[9] = "1"
        tail[11] = "12"
        tail[12] = "4"
        tail[19] = str(start_time)
        return f"{pid} ({comm}) " + " ".join(tail) + "\n"

    def test_niri_core_fails_closed_if_quickshell_appears_in_window(self):
        timeline = [
            {
                "offset_seconds": 0.0,
                "niri_pids": [10],
                "kwin_wayland_pids": [],
                "plasmashell_pids": [],
                "qs_pids": [],
                "managed_qs_pids": [],
                "polkit_agent_pids": [30],
                "qs_argv": [],
            },
            {
                "offset_seconds": 0.5,
                "niri_pids": [10],
                "kwin_wayland_pids": [],
                "plasmashell_pids": [],
                "qs_pids": [20],
                "managed_qs_pids": [20],
                "polkit_agent_pids": [30],
                "qs_argv": [{"pid": 20, "argv": benchmark_metrics.QS_EXPECTED_ARGV}],
            },
        ]
        unit = {"EnableState": "masked-runtime", "ActiveState": "inactive"}
        result = benchmark_metrics.validate_profile("niri-core", timeline, unit, unit)
        self.assertEqual(result["status"], benchmark_metrics.PROFILE_FAIL)
        self.assertTrue(any("Quickshell appeared" in item for item in result["evidence"]["failures"]))

    def test_niri_quickshell_requires_one_managed_systemd_pid(self):
        timeline = [{
            "offset_seconds": 0.0,
            "niri_pids": [10],
            "kwin_wayland_pids": [],
            "plasmashell_pids": [],
            "qs_pids": [20],
            "managed_qs_pids": [20],
            "polkit_agent_pids": [30],
            "qs_argv": [{"pid": 20, "argv": benchmark_metrics.QS_EXPECTED_ARGV}],
        }]
        unit = {
            "EnableState": "enabled",
            "ActiveState": "active",
            "SubState": "running",
            "Result": "success",
            "NRestarts": "0",
            "ExecMainPID": "20",
        }
        result = benchmark_metrics.validate_profile("niri-quickshell", timeline, unit, unit)
        self.assertEqual(result["status"], benchmark_metrics.PROFILE_OK)

        missing_polkit = [dict(timeline[0], polkit_agent_pids=[])]
        result = benchmark_metrics.validate_profile("niri-quickshell", missing_polkit, unit, unit)
        self.assertEqual(result["status"], benchmark_metrics.PROFILE_FAIL)

        bad = dict(unit, ExecMainPID="21")
        result = benchmark_metrics.validate_profile("niri-quickshell", timeline, unit, bad)
        self.assertEqual(result["status"], benchmark_metrics.PROFILE_FAIL)

    def test_plasma_requires_plasmashell_and_kwin_and_forbids_niri_and_qs(self):
        good = [{
            "offset_seconds": 0.0,
            "niri_pids": [],
            "kwin_wayland_pids": [11],
            "plasmashell_pids": [12],
            "qs_pids": [],
            "managed_qs_pids": [],
            "polkit_agent_pids": [],
            "qs_argv": [],
        }]
        self.assertEqual(
            benchmark_metrics.validate_profile("plasma", good, {}, {})["status"],
            benchmark_metrics.PROFILE_OK,
        )
        bad = [dict(good[0], niri_pids=[13])]
        self.assertEqual(
            benchmark_metrics.validate_profile("plasma", bad, {}, {})["status"],
            benchmark_metrics.PROFILE_FAIL,
        )

    def test_session_pss_counts_each_pid_once_and_components_are_views(self):
        uid = os.getuid()

        def row(pid, ppid, name, pss):
            value = benchmark_metrics.metric(benchmark_metrics.AVAILABLE, pss)
            zero = benchmark_metrics.metric(benchmark_metrics.AVAILABLE, 0)
            return {
                "pid": pid,
                "ppid": ppid,
                "uid": uid,
                "name": name,
                "argv": benchmark_metrics.QS_EXPECTED_ARGV if name == "qs" else [name],
                "cgroup": "/user.slice/test",
                "pss_bytes": value,
                "rss_bytes": value,
                "private_clean_bytes": zero,
                "private_dirty_bytes": value,
                "swap_pss_bytes": zero,
                "cpu_seconds_delta": zero,
                "minor_page_faults_delta": zero,
                "major_page_faults_delta": zero,
                "io_read_bytes_delta": zero,
                "io_write_bytes_delta": zero,
                "voluntary_context_switches_delta": zero,
                "nonvoluntary_context_switches_delta": zero,
            }

        rows = [row(500001, 1, "qs", 100), row(500002, 500001, "udevadm", 30)]
        session, components = benchmark_metrics.aggregate_scopes(rows)
        self.assertEqual(session["pss_bytes"]["value"], 130)
        self.assertEqual(components["quickshell"]["pss_bytes"]["value"], 100)
        self.assertEqual(components["quickshell_auxiliary"]["pss_bytes"]["value"], 30)
        self.assertIn("never added back", session["definition"])

    def test_lightweight_signature_keeps_truncated_policykit_comm(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            proc = root / "proc"
            proc.mkdir()
            entry = proc / "123"
            entry.mkdir()
            entry.joinpath("stat").write_text(
                "123 (lxqt-policykit-) S 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
            )
            entry.joinpath("cmdline").write_bytes(
                b"/usr/libexec/lxqt-policykit-agent\0"
            )
            signature = benchmark_metrics.lightweight_signature(root)
            self.assertEqual(signature["polkit_agent_pids"], [123])

    def test_component_matching_uses_argv_for_long_linux_comm_names(self):
        xwayland = {"name": "xwayland-satell", "argv": ["/usr/bin/xwayland-satellite"]}
        polkit = {"name": "lxqt-policykit-a", "argv": ["/usr/libexec/lxqt-policykit-agent"]}
        self.assertTrue(benchmark_metrics.COMPONENT_MATCHERS["xwayland_satellite"](xwayland))
        self.assertTrue(benchmark_metrics.COMPONENT_MATCHERS["polkit_agent"](polkit))

    def test_process_counter_deltas_share_one_window(self):
        def base(pid, ticks, minor, major, read, write):
            return {
                "pid": pid,
                "_identity": (pid, 999),
                "_counters": {
                    "cpu_ticks": ticks,
                    "minor_page_faults": minor,
                    "major_page_faults": major,
                    "io_read_bytes": read,
                    "io_write_bytes": write,
                    "voluntary_context_switches": 4,
                    "nonvoluntary_context_switches": 2,
                },
            }

        before = [base(42, 100, 10, 1, 1000, 2000)]
        after = [base(42, 150, 17, 2, 1400, 2600)]
        benchmark_metrics.attach_process_deltas(before, after, elapsed=5.0, ticks_per_second=100)
        self.assertEqual(after[0]["cpu_seconds_delta"]["value"], 0.5)
        self.assertEqual(after[0]["minor_page_faults_delta"]["value"], 7)
        self.assertEqual(after[0]["major_page_faults_delta"]["value"], 1)
        self.assertEqual(after[0]["io_read_bytes_delta"]["value"], 400)
        self.assertEqual(after[0]["io_write_bytes_delta"]["value"], 600)
        self.assertAlmostEqual(after[0]["cpu_percent_one_core_delta"]["value"], 10.0)

    def test_system_cpu_counts_exclude_guest_and_include_context_switch_deltas(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "proc").mkdir()
            stat_path = root / "proc/stat"
            stat_path.write_text("cpu 10 2 3 40 5 6 7 8 100 200\nctxt 100\nintr 200\n")
            before = benchmark_metrics.system_cpu_counters(root)
            stat_path.write_text("cpu 20 2 8 70 5 8 9 10 150 250\nctxt 125\nintr 230\n")
            after = benchmark_metrics.system_cpu_counters(root)
            delta = benchmark_metrics.system_cpu_delta(before, after, 10)
        self.assertEqual(before["total"], sum((10, 2, 3, 40, 5, 6, 7, 8)))
        self.assertEqual(delta["context_switches_delta"]["value"], 25)
        self.assertEqual(delta["interrupts_delta"]["value"], 30)
        self.assertEqual(delta["idle_percent_delta"]["status"], benchmark_metrics.AVAILABLE)

    def test_system_cpu_window_excludes_full_process_scans(self):
        events = []
        outcomes = {
            "enumeration_available": 1,
            "identity_changed": 0,
            "stat_permission": 0,
            "stat_unavailable": 0,
            "pids_seen": 0,
            "rows_included": 0,
            "scan_wall_seconds": 0.0,
            "proc_file_reads": 0,
            "proc_read_wall_seconds": 0.0,
            "smaps_rollup_reads": 0,
            "smaps_rollup_wall_seconds": 0.0,
        }

        def process_snapshot(*_args):
            events.append("process-scan")
            return [], dict(outcomes)

        def cpu_snapshot(_root):
            events.append("system-cpu")
            return {"total": 100, "idle": 50, "ctxt": 1, "intr": 1}

        def psi_snapshot(_root):
            events.append("psi")
            return {}

        def timeline(*_args):
            events.append("window")
            return [{"offset_seconds": 0.0}]

        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(benchmark_metrics, "process_snapshot", side_effect=process_snapshot), \
             mock.patch.object(benchmark_metrics, "system_cpu_counters", side_effect=cpu_snapshot), \
             mock.patch.object(benchmark_metrics, "psi_snapshot", side_effect=psi_snapshot), \
             mock.patch.object(benchmark_metrics, "observe_timeline", side_effect=timeline), \
             mock.patch.object(benchmark_metrics, "cgroup_snapshot", return_value={}), \
             mock.patch.object(benchmark_metrics, "systemd_user_manager_cgroup", return_value=None):
            observation = benchmark_metrics.observe(root_prefix=pathlib.Path(temp), duration=5)

        self.assertEqual(events.count("process-scan"), 2)
        self.assertLess(events.index("process-scan"), events.index("system-cpu"))
        second_cpu = len(events) - 1 - events[::-1].index("system-cpu")
        last_process_scan = len(events) - 1 - events[::-1].index("process-scan")
        self.assertLess(events.index("system-cpu"), events.index("window"))
        self.assertLess(events.index("window"), second_cpu)
        self.assertLess(second_cpu, last_process_scan)
        self.assertIn("process_observation_window_seconds", observation)

    def test_process_snapshot_discards_pid_reused_during_smaps_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            entry = root / "proc/123"
            entry.mkdir(parents=True)
            (entry / "cmdline").write_bytes(b"/usr/bin/example\0")
            (entry / "status").write_text("Uid:\t1000\t1000\t1000\t1000\n")
            (entry / "cgroup").write_text("0::/user.slice/test\n")
            (entry / "io").write_text("read_bytes: 12\nwrite_bytes: 4\n")
            (entry / "smaps_rollup").write_text("Rss: 10 kB\nPss: 8 kB\n")
            original_read = benchmark_metrics.read_text
            stat_reads = {"count": 0}

            def read_with_pid_reuse(path):
                if path == entry / "stat":
                    stat_reads["count"] += 1
                    start = 100 if stat_reads["count"] == 1 else 101
                    return self._proc_stat(123, "example process", start), None
                return original_read(path)

            with mock.patch.object(benchmark_metrics, "read_text", side_effect=read_with_pid_reuse):
                rows, outcomes = benchmark_metrics.process_snapshot(root, 100)

            self.assertEqual(rows, [])
            self.assertEqual(outcomes["identity_changed"], 1)
            self.assertEqual(outcomes["rows_included"], 0)

    def test_partial_process_scan_does_not_report_zero_memory_as_available(self):
        session = {"pss_bytes": benchmark_metrics.metric(benchmark_metrics.AVAILABLE, 0)}
        components = {"niri": {"pss_bytes": benchmark_metrics.metric(benchmark_metrics.AVAILABLE, 0)}}
        coverage = benchmark_metrics.apply_process_coverage(
            session,
            components,
            {
                "enumeration_available": 1,
                "pids_seen": 1,
                "rows_included": 0,
                "identity_changed": 1,
                "stat_permission": 0,
                "stat_unavailable": 0,
            },
        )
        self.assertEqual(coverage["status"], benchmark_metrics.UNAVAILABLE)
        self.assertEqual(session["pss_bytes"]["status"], benchmark_metrics.UNAVAILABLE)
        self.assertEqual(components["niri"]["pss_bytes"]["status"], benchmark_metrics.UNAVAILABLE)

    def test_unreadable_process_smaps_invalidates_aggregate_pss(self):
        session = {"pss_bytes": benchmark_metrics.metric(benchmark_metrics.AVAILABLE, 10)}
        coverage = benchmark_metrics.apply_process_coverage(
            session,
            {},
            {"enumeration_available": 1, "permission": 1, "unavailable": 0},
        )
        self.assertEqual(coverage["status"], benchmark_metrics.UNAVAILABLE)
        self.assertEqual(coverage["value"]["smaps_permission"], 1)
        self.assertEqual(session["pss_bytes"]["status"], benchmark_metrics.UNAVAILABLE)

    def test_prepare_and_restore_niri_core_are_runtime_only_and_marker_owned(self):
        with tempfile.TemporaryDirectory() as temp:
            state = {"enabled": "enabled", "active": "active"}
            calls = []
            qs_state = {"items": [{"pid": 77, "argv": benchmark.QS_EXPECTED}]}

            def runner(args, **kwargs):
                calls.append(args)
                action = args[2]
                if action == "is-enabled":
                    code = 1 if state["enabled"].startswith("masked") else 0
                    return subprocess.CompletedProcess(args, code, stdout=state["enabled"] + "\n", stderr="")
                if action == "is-active":
                    code = 0 if state["active"] == "active" else 3
                    return subprocess.CompletedProcess(args, code, stdout=state["active"] + "\n", stderr="")
                if action == "mask":
                    self.assertIn("--runtime", args)
                    self.assertIn("--now", args)
                    state.update(enabled="masked-runtime", active="inactive")
                    qs_state["items"] = []
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                if action == "unmask":
                    self.assertIn("--runtime", args)
                    state["enabled"] = "enabled"
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                if action == "start":
                    state["active"] = "active"
                    qs_state["items"] = [{"pid": 88, "argv": benchmark.QS_EXPECTED}]
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                raise AssertionError(args)

            env = {"XDG_RUNTIME_DIR": temp, "XDG_CURRENT_DESKTOP": "niri"}
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(benchmark.os, "geteuid", return_value=1000), \
                 mock.patch.object(benchmark, "_qs_processes", side_effect=lambda: list(qs_state["items"])):
                self.assertEqual(benchmark.prepare_niri_core(runner), 0)
                marker = benchmark.runtime_marker()
                self.assertTrue(marker.is_file())
                self.assertEqual(state, {"enabled": "masked-runtime", "active": "inactive"})
                payload = __import__("json").loads(marker.read_text())
                self.assertEqual(payload["previous_active_state"], "active")
                self.assertEqual(payload["previous_qs_pid"], 77)
                self.assertEqual(benchmark.restore_niri_core(runner), 0)
                self.assertFalse(marker.exists())
                self.assertEqual(state, {"enabled": "enabled", "active": "active"})

            self.assertTrue(any(call[2] == "mask" and "--runtime" in call for call in calls))
            self.assertTrue(any(call[2] == "unmask" and "--runtime" in call for call in calls))

    def test_prepare_niri_core_refuses_non_known_good_quickshell_state(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {"XDG_RUNTIME_DIR": temp, "XDG_CURRENT_DESKTOP": "niri"}

            def runner(args, **kwargs):
                action = args[2]
                if action == "is-enabled":
                    return subprocess.CompletedProcess(args, 0, stdout="disabled\n", stderr="")
                if action == "is-active":
                    return subprocess.CompletedProcess(args, 3, stdout="inactive\n", stderr="")
                raise AssertionError("prepare must refuse before mutating systemd state")

            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(benchmark.os, "geteuid", return_value=1000), \
                 mock.patch.object(benchmark, "_qs_processes", return_value=[]):
                self.assertEqual(benchmark.prepare_niri_core(runner), 2)
                self.assertFalse(benchmark.runtime_marker().exists())

    def test_failed_prepare_restores_quickshell_after_partial_runtime_mask(self):
        with tempfile.TemporaryDirectory() as temp:
            state = {"enabled": "disabled", "active": "active"}
            qs_state = {"items": [{"pid": 77, "argv": benchmark.QS_EXPECTED}]}

            def runner(args, **kwargs):
                action = args[2]
                if action == "is-enabled":
                    code = 1 if state["enabled"].startswith("masked") else 0
                    return subprocess.CompletedProcess(args, code, stdout=state["enabled"] + "\n", stderr="")
                if action == "is-active":
                    code = 0 if state["active"] == "active" else 3
                    return subprocess.CompletedProcess(args, code, stdout=state["active"] + "\n", stderr="")
                if action == "mask":
                    state.update(enabled="masked-runtime", active="inactive")
                    qs_state["items"] = [{"pid": 99, "argv": benchmark.QS_EXPECTED}]
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                if action == "unmask":
                    state["enabled"] = "disabled"
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                if action == "start":
                    state["active"] = "active"
                    qs_state["items"] = [{"pid": 100, "argv": benchmark.QS_EXPECTED}]
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                raise AssertionError(args)

            env = {"XDG_RUNTIME_DIR": temp, "XDG_CURRENT_DESKTOP": "niri"}
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(benchmark.os, "geteuid", return_value=1000), \
                 mock.patch.object(benchmark, "_qs_processes", side_effect=lambda: list(qs_state["items"])):
                self.assertEqual(benchmark.prepare_niri_core(runner), 2)
                marker = benchmark.runtime_marker()
                self.assertTrue(marker.is_file())
                self.assertEqual(__import__("json").loads(marker.read_text())["phase"], "restore-required")
                # A foreign process appeared while the unit was being masked. Do not
                # kill or adopt it; preserve ownership state until it has exited.
                self.assertEqual(state, {"enabled": "masked-runtime", "active": "inactive"})
                qs_state["items"] = []
                self.assertEqual(benchmark.restore_niri_core(runner), 0)
                self.assertFalse(marker.exists())
                self.assertEqual(state, {"enabled": "disabled", "active": "active"})
                self.assertEqual(qs_state["items"], [{"pid": 100, "argv": benchmark.QS_EXPECTED}])

    def test_failed_restore_restart_preserves_marker_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            state = {"enabled": "enabled", "active": "active"}
            qs_state = {"items": [{"pid": 77, "argv": benchmark.QS_EXPECTED}]}
            failures = {"start": False}

            def runner(args, **kwargs):
                action = args[2]
                if action == "is-enabled":
                    return subprocess.CompletedProcess(args, 0, stdout=state["enabled"] + "\n", stderr="")
                if action == "is-active":
                    code = 0 if state["active"] == "active" else 3
                    return subprocess.CompletedProcess(args, code, stdout=state["active"] + "\n", stderr="")
                if action == "mask":
                    state.update(enabled="masked-runtime", active="inactive")
                    qs_state["items"] = []
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                if action == "unmask":
                    state["enabled"] = "enabled"
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                if action == "start":
                    if failures["start"]:
                        return subprocess.CompletedProcess(args, 1, stdout="", stderr="simulated start failure")
                    state["active"] = "active"
                    qs_state["items"] = [{"pid": 88, "argv": benchmark.QS_EXPECTED}]
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                raise AssertionError(args)

            env = {"XDG_RUNTIME_DIR": temp, "XDG_CURRENT_DESKTOP": "niri"}
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(benchmark.os, "geteuid", return_value=1000), \
                 mock.patch.object(benchmark, "_qs_processes", side_effect=lambda: list(qs_state["items"])):
                self.assertEqual(benchmark.prepare_niri_core(runner), 0)
                marker = benchmark.runtime_marker()
                failures["start"] = True
                self.assertEqual(benchmark.restore_niri_core(runner), 2)
                self.assertTrue(marker.is_file())
                self.assertEqual(__import__("json").loads(marker.read_text())["phase"], "restore-required")
                failures["start"] = False
                self.assertEqual(benchmark.restore_niri_core(runner), 0)
                self.assertFalse(marker.exists())
                self.assertEqual(state, {"enabled": "enabled", "active": "active"})

    def test_runtime_marker_symlink_is_refused_without_systemctl_mutations(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = pathlib.Path(temp)
            marker = runtime / "niri-plus" / "benchmark-niri-core.json"
            marker.parent.mkdir(mode=0o700)
            target = runtime / "outside.json"
            target.write_text("{}")
            marker.symlink_to(target)
            calls = []

            def runner(args, **kwargs):
                calls.append(args)
                raise AssertionError("a symlink marker must be rejected before probing systemd")

            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": temp, "XDG_CURRENT_DESKTOP": "niri"}, clear=False), \
                 mock.patch.object(benchmark.os, "geteuid", return_value=1000):
                self.assertEqual(benchmark.restore_niri_core(runner), 2)
            self.assertEqual(calls, [])
            self.assertTrue(marker.is_symlink())
            self.assertEqual(target.read_text(), "{}")

    def test_prepare_restore_lock_rejects_overlapping_operation(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = pathlib.Path(temp) / "niri-plus" / "benchmark-niri-core.json"
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": temp}, clear=False):
                with benchmark._runtime_state_lock(marker):
                    with self.assertRaises(benchmark.BenchmarkStateError):
                        with benchmark._runtime_state_lock(marker):
                            self.fail("second lock acquisition must not run")

    def test_systemctl_timeout_is_reported_without_raising(self):
        def runner(args, **kwargs):
            self.assertEqual(kwargs["timeout"], 10)
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])

        result = benchmark._systemctl(["mask", "--runtime", benchmark.QS_UNIT], runner)
        self.assertEqual(result.returncode, 124)
        self.assertIn("timed out", result.stderr)


if __name__ == "__main__":
    unittest.main()
