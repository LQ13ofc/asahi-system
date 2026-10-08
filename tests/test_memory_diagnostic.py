import unittest

from niri_plus import benchmark_metrics as bm, memory_report


def item(value, status=bm.AVAILABLE, note=None):
    return bm.metric(status, value, note)


def process(pid, name, pss, uid=1000, ppid=1, cgroup="/user.slice/user-1000.slice/session-2.scope", dirty=None, start=100):
    return {
        "pid": pid,
        "ppid": ppid,
        "uid": uid,
        "name": name,
        "argv": [f"/usr/bin/{name}"],
        "executable": item(f"/usr/bin/{name}"),
        "cgroup": cgroup,
        "pss_bytes": item(pss),
        "rss_bytes": item(pss + 10),
        "private_clean_bytes": item(10),
        "private_dirty_bytes": item(dirty if dirty is not None else pss // 2),
        "swap_pss_bytes": item(0),
        "cpu_seconds_delta": item(0.25),
        "cpu_percent_one_core_delta": item(5.0),
        "threads": item(4),
        "children_count": item(0),
        "runtime_seconds": item(300.0),
        "start_time_ticks": start,
    }


def diagnostic_for(rows, used=4 * 1024**3, coverage=bm.AVAILABLE, excluded=None, coverage_detail=None):
    components = {}
    for name in ("niri", "quickshell", "quickshell_auxiliary", "xwayland_satellite", "pipewire"):
        selected = [row for row in rows if row["name"] == ("qs" if name == "quickshell" else name)]
        components[name] = bm.aggregate_rows(selected)
    return memory_report.build_memory_diagnostic(
        collected_at="2026-10-08T00:00:00+00:00",
        observation={
            "processes": rows,
            "observation_window_seconds": 2.0,
            "process_observation_window_seconds": 0.1,
            "memory_scopes": {
                "process_scan_coverage": item(coverage_detail or {"process_count": len(rows)}, coverage),
                "session": {"excluded_benchmark_pids": excluded or []},
            },
            "components": components,
            "psi_start": {"memory": item("some avg10=0.00")},
            "psi_end": {"memory": item("some avg10=0.01")},
            "psi_delta": {"memory": item({"some_total_usec_delta": 20})},
            "systemd_user_cgroup": {"start": {}, "end": {"memory_current_bytes": item(3 * 1024**3)}},
            "quickshell_cgroup": {"start": {}, "end": {"memory_current_bytes": item(300 * 1024**2)}},
        },
        memory={
            "mem_total_bytes": item(8 * 1024**3),
            "mem_available_bytes": item(4 * 1024**3),
            "used_bytes": item(used),
            "cached_bytes": item(1024**3),
            "shmem_bytes": item(100 * 1024**2),
            "swap_total_bytes": item(2 * 1024**3),
            "swap_used_bytes": item(100 * 1024**2),
            "zswap_usage": {"pool_total_size": item("64")},
        },
        cgroup_v2={"memory_current": item(1000)},
        systemd_user_cgroup={"start": {}, "end": {"memory_current_bytes": item(3 * 1024**3)}},
        quickshell_cgroup={"start": {}, "end": {"memory_current_bytes": item(300 * 1024**2)}},
        memory_management={"zswap_enabled": item("Y")},
        drm={"status": "NOT_ACCOUNTED", "note": "driver did not expose counters", "value": None},
        current_uid=1000,
    )


class MemoryDiagnosticTests(unittest.TestCase):
    def test_memory_views_do_not_add_overlapping_process_and_cgroup_metrics(self):
        rows = [
            process(10, "niri", 500 * 1024**2),
            process(11, "qs", 300 * 1024**2, ppid=10),
            process(12, "unknown-helper", 50 * 1024**2, ppid=10),
            process(20, "systemd-resolved", 40 * 1024**2, uid=0, cgroup="/system.slice/systemd-resolved.service"),
        ]
        report = diagnostic_for(rows, excluded=[12])
        processes = report["processes"]
        self.assertEqual(processes["all_process_pss_bytes"]["value"], sum(row["pss_bytes"]["value"] for row in rows))
        self.assertEqual(processes["graphics_components"]["niri"]["pss_bytes"]["value"], 500 * 1024**2)
        self.assertEqual(processes["graphics_components"]["quickshell"]["pss_bytes"]["value"], 300 * 1024**2)
        self.assertEqual(processes["system_service_processes"]["pss_bytes"]["value"], 40 * 1024**2)
        self.assertEqual(processes["same_uid_user_processes"]["pid_count"], 2)
        self.assertEqual(processes["graphical_session_processes"]["status"], bm.NOT_ACCOUNTED)
        self.assertEqual(processes["non_graphics_component_process_count"], 2)
        self.assertEqual(report["systemd_user_cgroup"]["end"]["memory_current_bytes"]["value"], 3 * 1024**3)
        self.assertEqual(report["cgroup_v2"]["memory_current"]["value"], 1000)
        self.assertIn("cannot be assigned", report["unattributed_memory_estimate_bytes"]["note"])
        self.assertEqual(report["interpretation"]["baseline_target"]["status"], "HIGH_BASELINE_CANDIDATE")
        self.assertIn("not asserted to be the exact logind", processes["group_semantics"])
        rendered = memory_report.render_memory_report(report)
        self.assertIn("Do not add", rendered)
        self.assertIn("DRM/GPU memory", rendered)
        self.assertIn("Graphical-session PSS", rendered)
        self.assertIn("Used estimate (total-available)", rendered)
        self.assertIn("Unattributed residual", rendered)

    def test_partial_process_scan_keeps_pss_and_residual_unavailable(self):
        row = process(10, "niri", 500)
        report = diagnostic_for([row], coverage=bm.UNAVAILABLE)
        self.assertEqual(report["processes"]["all_process_pss_bytes"]["status"], bm.UNAVAILABLE)
        self.assertEqual(report["unattributed_memory_estimate_bytes"]["status"], bm.UNAVAILABLE)
        self.assertEqual(report["processes"]["graphics_components"]["niri"]["pss_bytes"]["status"], bm.UNAVAILABLE)

    def test_readable_component_pss_survives_unrelated_global_smaps_denial(self):
        niri = process(10, "niri", 500)
        quickshell = process(11, "qs", 300, ppid=10)
        system = process(20, "systemd-resolved", 40, uid=0,
                         cgroup="/system.slice/systemd-resolved.service")
        system["pss_bytes"] = item(None, bm.PERMISSION_REQUIRED)
        report = diagnostic_for(
            [niri, quickshell, system],
            coverage=bm.UNAVAILABLE,
            coverage_detail={
                "pids_seen": 3, "rows_included": 3, "identity_changed": 0,
                "stat_permission": 0, "stat_unavailable": 0,
                "smaps_permission": 1, "smaps_unavailable": 0,
            },
        )
        self.assertEqual(report["processes"]["all_process_pss_bytes"]["status"], bm.UNAVAILABLE)
        components = report["processes"]["graphics_components"]
        self.assertEqual(components["niri"]["pss_bytes"]["value"], 500)
        self.assertEqual(components["quickshell"]["pss_bytes"]["value"], 300)
        self.assertEqual(report["processes"]["system_service_processes"]["pss_bytes"]["status"], bm.UNAVAILABLE)

    def test_process_inventory_keeps_unknown_names_visible(self):
        row = process(77, "mystery-service", 123, uid=0, cgroup="/system.slice/mystery.service")
        report = diagnostic_for([row], used=1000)
        inventory = report["processes"]["process_inventory"]
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["name"], "mystery-service")
        self.assertEqual(inventory[0]["observed_group"], "SYSTEM_SERVICES")
        self.assertIn("minor_page_faults_delta", inventory[0])
        self.assertEqual(
            report["processes"]["graphics_components"]["niri"]["pss_bytes"]["status"],
            bm.NOT_APPLICABLE,
        )

    def test_longitudinal_trend_reports_growth_and_stable_process_candidate_only(self):
        samples = []
        for index, value in enumerate((100, 110, 120, 130)):
            row = process(50, "worker", value, dirty=value // 2, start=123)
            report = diagnostic_for([row], used=value)
            samples.append({"elapsed_seconds": index * 300, "diagnostic": report})
        analysis = memory_report.analyze_memory_series(samples)
        self.assertEqual(analysis["growth_over_time"], "OBSERVED_MONOTONIC_PSS_GROWTH")
        self.assertEqual(analysis["trends"]["all_process_pss_bytes"]["value"]["delta"], 30)
        self.assertEqual(len(analysis["possible_leak_candidates"]), 1)
        self.assertEqual(analysis["possible_leak_candidates"][0]["classification"], "POSSIBLE_LEAK_CANDIDATE")

    def test_series_with_noise_is_not_reported_as_monotonic_growth_or_leak(self):
        samples = []
        for index, value in enumerate((100, 130, 110, 140)):
            row = process(50, "worker", value, dirty=value // 2, start=123)
            report = diagnostic_for([row], used=value)
            samples.append({"elapsed_seconds": index * 300, "diagnostic": report})
        analysis = memory_report.analyze_memory_series(samples)
        self.assertEqual(analysis["growth_over_time"], "NOT_ESTABLISHED")
        self.assertEqual(analysis["possible_leak_candidates"], [])


if __name__ == "__main__":
    unittest.main()
