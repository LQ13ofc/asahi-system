"""Read-only memory accounting reports shared by the CLI collector and tests."""

from __future__ import annotations

import os
from typing import Any

from . import benchmark_metrics as bm


GRAPHICS_NAMES = {
    "niri",
    "qs",
    "Xwayland",
    "Xwayland-satellite",
    "xwayland-satellite",
    "kwin_wayland",
    "plasmashell",
    "Xorg",
}


def number(item: Any) -> int | float | None:
    if isinstance(item, dict) and item.get("status") == bm.AVAILABLE:
        value = item.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def metric_sum(rows: list[dict[str, Any]], key: str, *, coverage_ok: bool) -> dict[str, Any]:
    values = [number(row.get(key)) for row in rows]
    missing = [row.get("pid") for row, value in zip(rows, values) if value is None]
    if not coverage_ok or missing:
        return bm.metric(bm.UNAVAILABLE, note=(
            "process scan incomplete" if not coverage_ok else f"unavailable for PIDs {missing}"
        ))
    return bm.metric(bm.AVAILABLE, sum(values))


def process_enumeration_complete(coverage: dict[str, Any]) -> bool:
    """Separate complete PID identity enumeration from smaps readability."""
    if coverage.get("status") == bm.AVAILABLE:
        return True
    detail = coverage.get("value")
    if not isinstance(detail, dict):
        return False
    try:
        return (
            int(detail.get("pids_seen", 0)) > 0
            and int(detail.get("rows_included", -1)) == int(detail.get("pids_seen", -2))
            and int(detail.get("identity_changed", 0)) == 0
            and int(detail.get("stat_permission", 0)) == 0
            and int(detail.get("stat_unavailable", 0)) == 0
        )
    except (TypeError, ValueError):
        return False


def classify_process(row: dict[str, Any], current_uid: int) -> tuple[str, str | None]:
    name = row.get("name", "")
    argv = row.get("argv") or []
    executable = None
    argv_name = None
    exe = row.get("executable")
    if isinstance(exe, dict) and exe.get("status") == bm.AVAILABLE:
        executable = str(exe.get("value", "")).rsplit("/", 1)[-1]
    if argv:
        argv_name = str(argv[0]).rsplit("/", 1)[-1]
    graphics_matchers = {
        "niri": bm.COMPONENT_MATCHERS["niri"],
        "quickshell": bm.COMPONENT_MATCHERS["quickshell"],
        "xwayland_satellite": bm.COMPONENT_MATCHERS["xwayland_satellite"],
        "kwin_wayland": bm.COMPONENT_MATCHERS["kwin_wayland"],
        "plasma": bm.COMPONENT_MATCHERS["plasma"],
    }
    component = next((key for key, matcher in graphics_matchers.items() if matcher(row)), None)
    if name in GRAPHICS_NAMES or executable in GRAPHICS_NAMES or argv_name in GRAPHICS_NAMES:
        return "GRAPHICS_COMPONENTS", component or name
    cgroup = str(row.get("cgroup") or "")
    if "/system.slice/" in f"/{cgroup.strip('/')}/" or cgroup.startswith("/system.slice/") or cgroup == "/init.scope":
        return "SYSTEM_SERVICES", None
    if row.get("uid") == current_uid:
        return "SAME_UID_USER_PROCESSES", None
    return "OTHER_UID_OR_SCOPE", None


def process_view(rows: list[dict[str, Any]], current_uid: int, coverage_ok: bool,
                 enumeration_complete: bool,
                 excluded_pids: set[int] | None = None,
                 component_views: dict[str, Any] | None = None) -> dict[str, Any]:
    excluded_pids = excluded_pids or set()
    decorated = []
    groups: dict[str, list[dict[str, Any]]] = {
        "SYSTEM_SERVICES": [],
        "SAME_UID_USER_PROCESSES": [],
        "GRAPHICS_COMPONENTS": [],
        "COLLECTOR_AND_ANCESTORS": [],
        "OTHER_UID_OR_SCOPE": [],
    }
    graphics_components: dict[str, list[dict[str, Any]]] = {}
    total_threads = 0
    missing_threads = []
    for source in rows:
        row = dict(source)
        group, component = classify_process(row, current_uid)
        if row.get("pid") in excluded_pids:
            group, component = "COLLECTOR_AND_ANCESTORS", None
        row["observed_group"] = group
        if component:
            row["graphics_component"] = component
            graphics_components.setdefault(component, []).append(row)
        groups[group].append(row)
        threads = number(row.get("threads"))
        if threads is None:
            missing_threads.append(row.get("pid"))
        else:
            total_threads += int(threads)
        private_clean = number(row.get("private_clean_bytes"))
        private_dirty = number(row.get("private_dirty_bytes"))
        if private_clean is not None and private_dirty is not None:
            row["uss_approx_bytes"] = bm.metric(
                bm.AVAILABLE,
                int(private_clean + private_dirty),
                "Private_Clean + Private_Dirty; approximate unique resident set",
            )
        else:
            row["uss_approx_bytes"] = bm.metric(bm.UNAVAILABLE, note="requires both private smaps_rollup fields")
        decorated.append(row)

    def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "pid_count": len(items),
            "pss_bytes": metric_sum(items, "pss_bytes", coverage_ok=enumeration_complete),
            "rss_bytes": metric_sum(items, "rss_bytes", coverage_ok=enumeration_complete),
            "private_clean_bytes": metric_sum(items, "private_clean_bytes", coverage_ok=enumeration_complete),
            "private_dirty_bytes": metric_sum(items, "private_dirty_bytes", coverage_ok=enumeration_complete),
            "uss_approx_bytes": metric_sum(items, "uss_approx_bytes", coverage_ok=enumeration_complete),
            "swap_pss_bytes": metric_sum(items, "swap_pss_bytes", coverage_ok=enumeration_complete),
        }

    def component_aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
        summary = aggregate(items)
        if not items:
            for key in (
                "pss_bytes", "rss_bytes", "private_clean_bytes", "private_dirty_bytes",
                "uss_approx_bytes", "swap_pss_bytes",
            ):
                summary[key] = bm.metric(bm.NOT_APPLICABLE, note="no process for this component was observed")
        return summary

    decorated.sort(key=lambda row: number(row.get("pss_bytes")) if number(row.get("pss_bytes")) is not None else -1, reverse=True)
    inventory = []
    for row in decorated:
        inventory.append({
            "pid": row.get("pid"),
            "ppid": row.get("ppid"),
            "uid": row.get("uid"),
            "name": row.get("name"),
            "argv": row.get("argv", []),
            "executable": row.get("executable"),
            "cgroup": row.get("cgroup"),
            "observed_group": row.get("observed_group"),
            "graphics_component": row.get("graphics_component"),
            "pss_bytes": row.get("pss_bytes"),
            "rss_bytes": row.get("rss_bytes"),
            "private_clean_bytes": row.get("private_clean_bytes"),
            "private_dirty_bytes": row.get("private_dirty_bytes"),
            "uss_approx_bytes": row.get("uss_approx_bytes"),
            "swap_pss_bytes": row.get("swap_pss_bytes"),
            "cpu_seconds_delta": row.get("cpu_seconds_delta"),
            "cpu_percent_one_core_delta": row.get("cpu_percent_one_core_delta"),
            "minor_page_faults_delta": row.get("minor_page_faults_delta"),
            "major_page_faults_delta": row.get("major_page_faults_delta"),
            "io_read_bytes_delta": row.get("io_read_bytes_delta"),
            "io_write_bytes_delta": row.get("io_write_bytes_delta"),
            "voluntary_context_switches_delta": row.get("voluntary_context_switches_delta"),
            "nonvoluntary_context_switches_delta": row.get("nonvoluntary_context_switches_delta"),
            "threads": row.get("threads"),
            "children_count": row.get("children_count"),
            "runtime_seconds": row.get("runtime_seconds"),
            "start_time_ticks": row.get("start_time_ticks"),
        })

    graphics = {name: component_aggregate(items) for name, items in sorted(graphics_components.items())}
    # Keep the names requested by the desktop investigation stable even when a
    # component is absent; absence is a measured zero only with complete scan.
    for name in ("niri", "quickshell", "quickshell_auxiliary", "xwayland_satellite"):
        if name == "quickshell_auxiliary":
            qs_pids = {row.get("pid") for row in graphics_components.get("quickshell", [])}
            descendants: set[int] = set()
            children: dict[int, list[int]] = {}
            for row in decorated:
                children.setdefault(row.get("ppid", -1), []).append(row.get("pid"))
            stack = list(qs_pids)
            while stack:
                parent = stack.pop()
                for child in children.get(parent, []):
                    if child not in descendants and child not in qs_pids:
                        descendants.add(child)
                        stack.append(child)
            items = [row for row in decorated if row.get("pid") in descendants]
        else:
            items = graphics_components.get(name, [])
        graphics[name] = component_aggregate(items)

    same_uid_rows = [row for row in groups["SAME_UID_USER_PROCESSES"] if row.get("pid") not in excluded_pids] + [
        row for row in groups["GRAPHICS_COMPONENTS"]
        if row.get("uid") == current_uid and row.get("pid") not in excluded_pids
    ]
    # Niri/Quickshell systemd user units may live outside session-*.scope.
    # This is explicitly a UID-owned user-process view, not an exact graphical
    # session boundary; component groups overlap it and are never added again.
    return {
        "coverage": bm.metric(bm.AVAILABLE if coverage_ok else bm.UNAVAILABLE,
                              {"process_count": len(rows), "thread_count": total_threads,
                               "thread_count_status": bm.AVAILABLE if not missing_threads else bm.UNAVAILABLE,
                               "thread_count_unavailable_pids": missing_threads}),
        "all_process_pss_bytes": metric_sum(rows, "pss_bytes", coverage_ok=coverage_ok),
        "all_process_private_dirty_bytes": metric_sum(rows, "private_dirty_bytes", coverage_ok=coverage_ok),
        "all_process_private_clean_bytes": metric_sum(rows, "private_clean_bytes", coverage_ok=coverage_ok),
        "all_process_swap_pss_bytes": metric_sum(rows, "swap_pss_bytes", coverage_ok=coverage_ok),
        "same_uid_user_processes": aggregate(same_uid_rows),
        "graphical_session_processes": bm.metric(
            bm.NOT_ACCOUNTED,
            note=(
                "the collector cannot prove the complete logind graphical-session boundary; "
                "same_uid_user_processes is a broader UID-owned proxy"
            ),
        ),
        "system_service_processes": aggregate(groups["SYSTEM_SERVICES"]),
        "other_uid_or_scope_processes": aggregate(groups["OTHER_UID_OR_SCOPE"]),
        "graphics_components": graphics,
        "component_views": component_views or {},
        "group_semantics": (
            "Process groups are overlapping views, not additive partitions. Same-UID user processes include user services and desktop applications; this is not asserted to be the exact logind graphical-session cgroup. System services are selected by system.slice cgroup path. Every observed PID remains in process_inventory, including unclassified processes."
        ),
        "process_inventory": inventory,
        "largest_by_pss": inventory[:20],
        "non_graphics_component_process_count": sum(1 for row in decorated if not row.get("graphics_component")),
    }


def build_memory_diagnostic(
    *,
    collected_at: str,
    system: dict[str, Any] | None = None,
    observation: dict[str, Any],
    memory: dict[str, Any],
    cgroup_v2: dict[str, Any],
    systemd_user_cgroup: dict[str, Any],
    quickshell_cgroup: dict[str, Any],
    memory_management: dict[str, Any],
    drm: dict[str, Any],
    current_uid: int | None = None,
) -> dict[str, Any]:
    uid = os.getuid() if current_uid is None else current_uid
    processes = observation.get("processes", [])
    coverage = observation.get("memory_scopes", {}).get("process_scan_coverage", {})
    coverage_ok = coverage.get("status") == bm.AVAILABLE
    enumeration_complete = process_enumeration_complete(coverage)
    all_pss = metric_sum(processes, "pss_bytes", coverage_ok=coverage_ok)
    used = number(memory.get("used_bytes"))
    pss_total = number(all_pss)
    residual = (
        bm.metric(bm.AVAILABLE, used - pss_total,
                  "Arithmetic residual only: global MemTotal-MemAvailable minus all-process PSS. Snapshot times and accounting domains differ; it cannot be assigned to kernel, cache, or GPU.")
        if used is not None and pss_total is not None
        else bm.metric(bm.UNAVAILABLE, note="requires valid global used estimate and complete all-process PSS")
    )
    excluded = set(observation.get("memory_scopes", {}).get("session", {}).get("excluded_benchmark_pids", []))
    process_summary = process_view(
        processes, uid, coverage_ok, enumeration_complete, excluded,
        observation.get("components", {}),
    )
    process_summary["same_uid_user_processes"].update({
        "excluded_collector_ancestor_pids": sorted(excluded),
        "scope_definition": "all observed processes owned by the invoking UID, excluding the collector and its ancestor chain; includes user services, not an exact graphical-session boundary",
    })
    niri_count = observation.get("components", {}).get("niri", {}).get("pid_count", 0)
    if not niri_count:
        target_status = bm.NOT_APPLICABLE
        target_note = "Niri process was not observed; Niri idle objective is not evaluated for this capture"
    elif used is None:
        target_status = bm.UNAVAILABLE
        target_note = "global used estimate unavailable"
    elif used > 2 * 1024 ** 3:
        target_status = "HIGH_BASELINE_CANDIDATE"
        target_note = "above the project goal of 2 GiB; this compares MemTotal-MemAvailable and is not a diagnosis"
    else:
        target_status = "AT_OR_BELOW_PROJECT_TARGET"
        target_note = "global used estimate is at or below the project goal; workload/session conditions still matter"
    return {
        "schema_version": 1,
        "mode": "memory-diagnostic",
        "collected_at": collected_at,
        "system": system or {},
        "read_only": True,
        "host_modified": False,
        "observation_window_seconds": observation.get("observation_window_seconds"),
        "process_observation_window_seconds": observation.get("process_observation_window_seconds"),
        "memory": memory,
        "processes": process_summary,
        "process_scan_coverage": coverage,
        "unattributed_memory_estimate_bytes": residual,
        "cgroup_v2": cgroup_v2,
        "systemd_user_cgroup": systemd_user_cgroup,
        "quickshell_cgroup": quickshell_cgroup,
        "psi": {
            "start": observation.get("psi_start", {}),
            "end": observation.get("psi_end", {}),
            "delta": observation.get("psi_delta", {}),
        },
        "memory_management": memory_management,
        "graphics_memory": {
            "drm_fdinfo": drm,
            "accounting_note": (
                "Apple Silicon uses unified physical memory. DRM client counters are optional driver reports and can describe buffers backed by the same system DRAM already represented by PSS/global counters. Never add them to either total. Missing driver counters are not evidence of zero GPU memory."
            ),
        },
        "interpretation": {
            "baseline_target": {"status": target_status, "note": target_note, "target_bytes": 2 * 1024 ** 3},
            "global_used": "MemTotal - MemAvailable is a global availability estimate, not process PSS or memory used only by Niri.",
            "cache": "Cached, Shmem, Mapped, Slab components, and process PSS overlap in accounting; report separately and do not sum them.",
            "unattributed": "The residual is approximate and cannot identify a device or subsystem. Investigate kernel/cgroup/DRM interfaces separately.",
            "expected_residency": "Not inferred from counters alone; compare the same workload and session across repeated captures.",
        },
    }


def render_memory_report(report: dict[str, Any], *, top: int = 10) -> str:
    memory = report.get("memory", {})
    processes = report.get("processes", {})
    system = report.get("system", {})
    lines = ["Niri+ memory diagnostic (read-only)", f"Collected: {report.get('collected_at', 'unknown')}"]
    architecture = format_metric(system.get("architecture"))
    session = format_metric(system.get("desktop"))
    release = system.get("fedora_asahi_release", {})
    release_value = release.get("value", {}) if isinstance(release, dict) else {}
    release_name = release_value.get("PRETTY_NAME") or release_value.get("NAME") or "UNAVAILABLE"
    lines.append(f"Host: {release_name} / {architecture}; session={session}")

    def show(label: str, key: str) -> None:
        item = memory.get(key, {})
        value = number(item)
        lines.append(f"  {label:<25} {format_bytes(value) if value is not None else item.get('status', 'UNAVAILABLE')}")

    lines.append("\nSystem memory")
    show("Total", "mem_total_bytes")
    show("Available", "mem_available_bytes")
    show("Used estimate (total-available)", "used_bytes")
    for label, key in (
        ("Free", "mem_free_bytes"), ("Buffers", "buffers_bytes"), ("Cached", "cached_bytes"),
        ("Shmem", "shmem_bytes"), ("AnonPages", "anon_pages_bytes"), ("Mapped", "mapped_bytes"),
        ("Slab total", "slab_bytes"), ("SReclaimable", "sreclaimable_bytes"),
        ("SUnreclaim", "sunreclaim_bytes"), ("KernelStack", "kernel_stack_bytes"),
        ("PageTables", "page_tables_bytes"), ("Unevictable", "unevictable_bytes"),
    ):
        show(label, key)

    lines.append("\nProcess and desktop accounting (overlapping views)")
    session_boundary = processes.get("graphical_session_processes", {})
    lines.append(f"  {'Graphical-session PSS':<25} {format_metric(session_boundary)}")
    if session_boundary.get("note"):
        lines.append(f"    session scope: {session_boundary['note']}")
    lines.append(f"  {'All-process PSS':<25} {format_metric(processes.get('all_process_pss_bytes'))}")
    lines.append(f"  {'Same-UID user PSS proxy':<25} {format_metric(processes.get('same_uid_user_processes', {}).get('pss_bytes'))}")
    lines.append(f"  {'System.slice PSS':<25} {format_metric(processes.get('system_service_processes', {}).get('pss_bytes'))}")
    lines.append(f"  {'All private dirty':<25} {format_metric(processes.get('all_process_private_dirty_bytes'))}")
    for label, key in (("Niri", "niri"), ("Quickshell", "quickshell"), ("Quickshell helpers", "quickshell_auxiliary"), ("Xwayland-satellite", "xwayland_satellite")):
        lines.append(f"  {label:<25} {format_metric(processes.get('graphics_components', {}).get(key, {}).get('pss_bytes'))}")
    coverage = processes.get("coverage", {})
    coverage_value = coverage.get("value", {})
    lines.append(
        f"  {'Process/thread count':<25} {coverage_value.get('process_count', 'UNAVAILABLE')} processes / "
        f"{coverage_value.get('thread_count', 'UNAVAILABLE')} threads; PSS coverage {coverage.get('status', 'UNAVAILABLE')}"
    )
    if coverage.get("note"):
        lines.append(f"    coverage note: {coverage['note']}")

    lines.append("\nLargest processes by PSS")
    inventory = processes.get("largest_by_pss", [])[:top]
    if not inventory:
        lines.append("  UNAVAILABLE (no complete process inventory)")
    for row in inventory:
        pss = format_metric(row.get("pss_bytes"))
        rss = format_metric(row.get("rss_bytes"))
        uss = format_metric(row.get("uss_approx_bytes"))
        cpu = format_metric(row.get("cpu_percent_one_core_delta"))
        lines.append(
            f"  PID {str(row.get('pid', '?')):<7} {str(row.get('name', '?'))[:22]:<22} "
            f"PSS {pss:<11} RSS {rss:<11} USS~ {uss:<11} CPU {cpu} "
            f"threads {format_number_metric(row.get('threads'))} direct-children {format_number_metric(row.get('children_count'))} "
            f"age {format_number_metric(row.get('runtime_seconds'), 's')} faults(min/maj) "
            f"{format_number_metric(row.get('minor_page_faults_delta'))}/{format_number_metric(row.get('major_page_faults_delta'))} "
            f"[{row.get('observed_group', 'UNKNOWN')}]"
        )
        exe = row.get("executable", {})
        exe_name = str(exe.get("value", "UNAVAILABLE")).rsplit("/", 1)[-1] if isinstance(exe, dict) else "UNAVAILABLE"
        lines.append(f"    uid={row.get('uid', 'UNAVAILABLE')} exe={exe_name} cgroup={row.get('cgroup') or 'UNAVAILABLE'}")

    lines.append("\nSwap / zswap")
    show("Swap total", "swap_total_bytes")
    show("Swap used", "swap_used_bytes")
    zswap = report.get("memory_management", {})
    for label, key in (
        ("zswap enabled", "zswap_enabled"), ("zswap compressor", "zswap_compressor"),
        ("zswap max pool %", "zswap_max_pool_percent"), ("swappiness", "swappiness"),
        ("page-cluster", "page_cluster"), ("MGLRU enabled", "mglru_enabled"),
        ("MGLRU min TTL", "mglru_min_ttl_ms"),
    ):
        lines.append(f"  {label:<25} {format_metric(zswap.get(key))}")
    lines.append(f"  zswap pool bytes        {format_metric(memory.get('zswap_usage', {}).get('pool_total_size'))}")
    lines.append(f"  SwapPss all processes   {format_metric(processes.get('all_process_swap_pss_bytes'))}")

    psi = report.get("psi", {}).get("end", {})
    psi_memory = psi.get("memory", {})
    lines.append("\nMemory pressure")
    lines.append(f"  PSI memory              {format_metric(psi_memory)}")
    lines.append(f"  Unattributed residual  {format_metric(report.get('unattributed_memory_estimate_bytes'))}")
    target = report.get("interpretation", {}).get("baseline_target", {})
    lines.append(f"  Niri idle target        {target.get('status', 'UNAVAILABLE')}: {target.get('note', '')}")
    lines.append("\nCgroup memory (separate views)")
    def cgroup_detail(label: str, snapshot: dict[str, Any]) -> None:
        current = snapshot.get("memory_current_bytes", snapshot.get("memory_current"))
        lines.append(f"  {label:<24} {format_metric(current)}")
        for field, key in (("memory.stat", "memory_stat"), ("memory.events", "memory_events")):
            item = snapshot.get(key, {})
            value = item.get("value") if isinstance(item, dict) and item.get("status") == bm.AVAILABLE else None
            if isinstance(value, dict):
                selected = ("anon", "file", "shmem", "slab", "kernel", "high", "oom", "oom_kill")
                detail = ", ".join(f"{name}={value[name]}" for name in selected if name in value)
                if detail:
                    lines.append(f"    {field:<22} {detail}")
            elif isinstance(value, str) and field == "memory.events":
                lines.append(f"    {field:<22} {value.replace(chr(10), '; ')}")

    cgroup_detail("Collector cgroup", report.get("cgroup_v2", {}))
    user_cgroup = report.get("systemd_user_cgroup", {}).get("end", {})
    cgroup_detail("systemd --user", user_cgroup)
    cgroup_detail("Quickshell unit", report.get("quickshell_cgroup", {}).get("end", {}))
    gpu = report.get("graphics_memory", {}).get("drm_fdinfo", {})
    lines.append("\nDRM/GPU memory")
    lines.append(f"  status                  {gpu.get('status', 'UNAVAILABLE')}: {gpu.get('note', '')}")
    gpu_value = gpu.get("value") or {}
    for region, size in (gpu_value.get("resident_bytes_by_region") or {}).items():
        lines.append(f"  resident {region:<15} {format_bytes(size)}")
    lines.append("\nAccounting groups overlap. Do not add PSS, cgroup memory.current, DRM counters, or meminfo components together.")
    return "\n".join(lines)


def format_bytes(value: int | float | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    amount = float(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit = 0
    while amount >= 1024 and unit < len(units) - 1:
        amount /= 1024
        unit += 1
    return f"{sign}{amount:.2f} {units[unit]}"


def format_metric(item: Any) -> str:
    value = number(item)
    if value is not None:
        if isinstance(value, float):
            return f"{value:.2f}"
        return format_bytes(value)
    if isinstance(item, dict):
        raw = item.get("value")
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            return ", ".join(f"{key}={value}" for key, value in sorted(raw.items()))
        return str(item.get("status", "UNAVAILABLE"))
    return "UNAVAILABLE"


def format_number_metric(item: Any, suffix: str = "") -> str:
    value = number(item)
    if value is None:
        return str(item.get("status", "UNAVAILABLE")) if isinstance(item, dict) else "UNAVAILABLE"
    return f"{value:g}{suffix}"


def analyze_memory_series(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Report observed changes without treating a trend as proof of a leak."""
    fields = {
        "global_used_bytes": ("memory", "used_bytes"),
        "mem_available_bytes": ("memory", "mem_available_bytes"),
        "all_process_pss_bytes": ("processes", "all_process_pss_bytes"),
        "all_process_private_dirty_bytes": ("processes", "all_process_private_dirty_bytes"),
        "same_uid_process_pss_bytes": ("processes", "same_uid_user_processes", "pss_bytes"),
        "niri_pss_bytes": ("processes", "graphics_components", "niri", "pss_bytes"),
        "quickshell_pss_bytes": ("processes", "graphics_components", "quickshell", "pss_bytes"),
        "swap_used_bytes": ("memory", "swap_used_bytes"),
        "cached_bytes": ("memory", "cached_bytes"),
    }
    trends: dict[str, Any] = {}
    for name, path in fields.items():
        values = []
        for sample in samples:
            value: Any = sample.get("diagnostic", {})
            for part in path:
                value = value.get(part) if isinstance(value, dict) else None
            values.append(number(value))
        present = [(sample.get("elapsed_seconds"), value) for sample, value in zip(samples, values) if value is not None]
        if len(present) < 2:
            trends[name] = bm.metric(bm.UNAVAILABLE, note="fewer than two valid samples")
            continue
        first, last = present[0][1], present[-1][1]
        monotonic = all(right[1] >= left[1] for left, right in zip(present, present[1:]))
        trends[name] = {
            "status": bm.AVAILABLE,
            "value": {
                "first": first,
                "last": last,
                "delta": last - first,
                "monotonic_non_decreasing": monotonic,
                "valid_samples": len(present),
            },
        }

    leak_candidates = []
    if len(samples) >= 4:
        by_identity: dict[tuple[int, int], list[dict[str, Any] | None]] = {}
        for index, sample in enumerate(samples):
            inventory = sample.get("diagnostic", {}).get("processes", {}).get("process_inventory", [])
            current = {(row.get("pid"), row.get("start_time_ticks")): row for row in inventory}
            for identity, existing in by_identity.items():
                existing.append(current.get(identity))
            for identity, row in current.items():
                if identity not in by_identity:
                    by_identity[identity] = [None] * index + [row]
        for (pid, start_ticks), rows in by_identity.items():
            if len(rows) != len(samples) or any(row is None for row in rows):
                continue
            if any(row.get("observed_group") == "COLLECTOR_AND_ANCESTORS" for row in rows):
                continue
            pss = [number(row.get("pss_bytes")) for row in rows]
            dirty = [number(row.get("private_dirty_bytes")) for row in rows]
            if any(value is None for value in pss + dirty):
                continue
            if all(b >= a for a, b in zip(pss, pss[1:])) and all(b >= a for a, b in zip(dirty, dirty[1:])) and (pss[-1] > pss[0] or dirty[-1] > dirty[0]):
                leak_candidates.append({
                    "pid": pid,
                    "start_time_ticks": start_ticks,
                    "name": rows[-1].get("name"),
                    "pss_delta_bytes": pss[-1] - pss[0],
                    "private_dirty_delta_bytes": dirty[-1] - dirty[0],
                    "classification": "POSSIBLE_LEAK_CANDIDATE",
                    "note": "stable PID identity and monotonic PSS/private-dirty growth across all samples; workload and functional retention still require investigation",
                })
    return {
        "sample_count": len(samples),
        "trends": trends,
        "growth_over_time": "OBSERVED_MONOTONIC_PSS_GROWTH" if trends.get("all_process_pss_bytes", {}).get("value", {}).get("monotonic_non_decreasing") and trends["all_process_pss_bytes"]["value"]["delta"] > 0 else "NOT_ESTABLISHED",
        "possible_leak_candidates": leak_candidates,
        "cached_memory": "Reported as separate meminfo fields; recoverability is not inferred.",
        "expected_residency": "Not inferred from counters alone; workload and session-specific A/B comparison are required.",
        "note": "Longitudinal growth is observational, not a leak diagnosis. Cloud fixtures validate schema and trend logic only; hardware measurements are required for the target system.",
    }
