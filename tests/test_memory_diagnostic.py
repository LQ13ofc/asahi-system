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

    def test_incomplete_pid_scan_does_not_claim_niri_or_components_are_absent(self):
        other = process(10, "shell", 500)
        report = diagnostic_for(
            [other],
            coverage=bm.UNAVAILABLE,
            coverage_detail={
                "pids_seen": 2, "rows_included": 1, "identity_changed": 0,
                "stat_permission": 1, "stat_unavailable": 0,
                "smaps_permission": 0, "smaps_unavailable": 0,
            },
        )
        niri = report["processes"]["graphics_components"]["niri"]["pss_bytes"]
        self.assertEqual(niri["status"], bm.UNAVAILABLE)
        self.assertIn("absence cannot be confirmed", niri["note"])
        target = report["interpretation"]["baseline_target"]
        self.assertEqual(target["status"], bm.UNAVAILABLE)
        self.assertIn("cannot determine whether Niri was running", target["note"])

    def test_complete_pid_scan_can_report_niri_not_applicable(self):
        report = diagnostic_for([process(10, "shell", 500)])
        niri = report["processes"]["graphics_components"]["niri"]["pss_bytes"]
        self.assertEqual(niri["status"], bm.NOT_APPLICABLE)
        self.assertIn("complete PID scan", niri["note"])
        self.assertEqual(report["interpretation"]["baseline_target"]["status"], bm.NOT_APPLICABLE)

    def test_missing_pss_for_one_process_makes_global_pss_and_residual_unavailable(self):
        rows = [process(10, "niri", 500), process(11, "other", 200)]
        rows[1]["pss_bytes"] = item(None, bm.PERMISSION_REQUIRED, "smaps_rollup denied")
        report = diagnostic_for(rows)
        self.assertEqual(report["processes"]["all_process_pss_bytes"]["status"], bm.UNAVAILABLE)
        self.assertIn("11", report["processes"]["all_process_pss_bytes"]["note"])
        self.assertEqual(report["unattributed_memory_estimate_bytes"]["status"], bm.UNAVAILABLE)

    def test_residual_is_arithmetic_only_even_when_negative_and_unknown_is_not_gpu(self):
        row = process(10, "unknown-render-helper", 6 * 1024**3)
        report = diagnostic_for([row], used=4 * 1024**3)
        self.assertEqual(report["unattributed_memory_estimate_bytes"]["value"], -2 * 1024**3)
        self.assertIn("cannot be assigned", report["unattributed_memory_estimate_bytes"]["note"])
        self.assertEqual(report["processes"]["process_inventory"][0]["observed_group"], "SAME_UID_USER_PROCESSES")
        self.assertNotIn("gpu_bytes", report)
        self.assertEqual(report["graphics_memory"]["drm_fdinfo"]["status"], "NOT_ACCOUNTED")

    def test_rendered_report_labels_bytes_percentages_counts_and_durations(self):
        row = process(10, "niri", 1024**2)
        row["minor_page_faults_delta"] = item(12)
        row["major_page_faults_delta"] = item(3)
        report = diagnostic_for([row])
        report["memory"]["zswap_usage"]["pool_total_size"] = item("67108864")
        report["memory_management"].update({
            "zswap_max_pool_percent": item("20"),
            "swappiness": item("60"),
            "page_cluster": item("3"),
            "mglru_min_ttl_ms": item("1000"),
        })
        report["psi"]["end"]["memory"] = item("some avg10=0.05 avg60=0.10 avg300=0.15 total=2500")
        report["cgroup_v2"]["memory_stat"] = item({"anon": 1024, "file": 2048})
        report["cgroup_v2"]["memory_events"] = item({"high": 2, "oom": 1, "oom_kill": 0})
        rendered = memory_report.render_memory_report(report)
        self.assertRegex(rendered, r"zswap max pool\s+20%")
        self.assertRegex(rendered, r"swappiness\s+60\n")
        self.assertRegex(rendered, r"page-cluster\s+3\n")
        self.assertRegex(rendered, r"MGLRU min TTL\s+1000 ms")
        self.assertIn("zswap pool size         64.00 MiB", rendered)
        self.assertIn("avg10=0.05%", rendered)
        self.assertIn("total=2500 us", rendered)
        self.assertIn("anon=1.00 KiB", rendered)
        self.assertIn("high=2 events", rendered)
        self.assertIn("CPU 5.00% one core", rendered)
        self.assertIn("age 300 s", rendered)
        self.assertIn("faults(min/maj) 12/3 counts", rendered)
        self.assertNotIn("20.00 B", rendered)

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
        base = 8 * 1024 * 1024
        values = (base, base + 256 * 1024, base + 768 * 1024, base + 1024 * 1024)
        for index, value in enumerate(values):
            row = process(50, "worker", value, dirty=value // 2, start=123)
            report = diagnostic_for([row], used=value)
            samples.append({"elapsed_seconds": index * 300, "diagnostic": report})
        analysis = memory_report.analyze_memory_series(samples)
        self.assertEqual(analysis["growth_over_time"], "OBSERVED_MONOTONIC_PSS_GROWTH")
        self.assertEqual(analysis["trends"]["all_process_pss_bytes"]["value"]["delta"], 1024 * 1024)
        self.assertEqual(len(analysis["possible_leak_candidates"]), 1)
        self.assertEqual(analysis["possible_leak_candidates"][0]["classification"], "POSSIBLE_LEAK_CANDIDATE")
        self.assertEqual(analysis["possible_leak_candidates"][0]["minimum_candidate_growth_bytes"], 1024 * 1024)

    def test_small_monotonic_process_growth_is_visible_but_not_a_leak_candidate(self):
        samples = []
        for index, value in enumerate((100, 110, 120, 130)):
            row = process(50, "worker", value, dirty=value // 2, start=123)
            samples.append({"elapsed_seconds": index * 300, "diagnostic": diagnostic_for([row], used=value)})
        analysis = memory_report.analyze_memory_series(samples)
        self.assertEqual(analysis["growth_over_time"], "OBSERVED_MONOTONIC_PSS_GROWTH")
        self.assertEqual(analysis["possible_leak_candidates"], [])
        observed = analysis["subthreshold_process_growth"]
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["pss_delta_bytes"], 30)
        self.assertEqual(observed[0]["classification"], "OBSERVED_GROWTH_BELOW_SCREENING_FLOOR")

    def test_series_with_noise_is_not_reported_as_monotonic_growth_or_leak(self):
        samples = []
        for index, value in enumerate((100, 130, 110, 140)):
            row = process(50, "worker", value, dirty=value // 2, start=123)
            report = diagnostic_for([row], used=value)
            samples.append({"elapsed_seconds": index * 300, "diagnostic": report})
        analysis = memory_report.analyze_memory_series(samples)
        self.assertEqual(analysis["growth_over_time"], "NOT_ESTABLISHED")
        self.assertEqual(analysis["possible_leak_candidates"], [])

    def test_pid_reuse_and_disappearing_process_do_not_become_leak_candidates(self):
        samples = []
        series_rows = (
            [process(50, "worker", 100, dirty=50, start=123)],
            [process(50, "worker", 900, dirty=800, start=124)],
            [],
            [process(50, "worker", 1200, dirty=1100, start=124)],
        )
        for index, rows in enumerate(series_rows):
            samples.append({"elapsed_seconds": index * 300, "diagnostic": diagnostic_for(rows, used=1500)})
        analysis = memory_report.analyze_memory_series(samples)
        self.assertEqual(analysis["possible_leak_candidates"], [])

    def test_same_tick_pid_reuse_with_changed_process_metadata_is_not_a_leak(self):
        samples = []
        for index, (name, value) in enumerate((("worker", 100), ("other", 900), ("other", 1000), ("other", 1100))):
            row = process(50, name, value, dirty=value // 2, start=123)
            samples.append({"elapsed_seconds": index * 300, "diagnostic": diagnostic_for([row], used=1500)})
        analysis = memory_report.analyze_memory_series(samples)
        self.assertEqual(analysis["possible_leak_candidates"], [])

    def test_incomplete_per_process_smaps_never_creates_leak_candidate(self):
        samples = []
        for index, value in enumerate((100, 110, 120, 130)):
            row = process(50, "worker", value, dirty=value // 2, start=123)
            if index == 2:
                row["private_dirty_bytes"] = item(None, bm.PERMISSION_REQUIRED)
            samples.append({"elapsed_seconds": index * 300, "diagnostic": diagnostic_for([row], used=1000)})
        analysis = memory_report.analyze_memory_series(samples)
        self.assertEqual(analysis["possible_leak_candidates"], [])


if __name__ == "__main__":
    unittest.main()
