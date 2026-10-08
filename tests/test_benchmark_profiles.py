import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from niri_plus import benchmark, benchmark_metrics


class BenchmarkProfileTests(unittest.TestCase):
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
                self.assertFalse(benchmark.runtime_marker().exists())
                self.assertEqual(state, {"enabled": "disabled", "active": "active"})
                self.assertEqual(qs_state["items"], [{"pid": 100, "argv": benchmark.QS_EXPECTED}])


if __name__ == "__main__":
    unittest.main()
