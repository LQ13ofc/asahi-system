"""Offline, conservative comparison for validated Plasma/Niri A/B/C captures."""

from __future__ import annotations

import json
import math
import os
import pathlib
import re
import statistics
import tempfile
from typing import Any

SCHEMA_VERSION = 2
COMPARISON_SCHEMA_VERSION = 1
MIN_RUNS_FOR_CLASSIFICATION = 3
PROFILE_NAMES = {
    "plasma": "plasma",
    "niri_core": "niri-core",
    "niri_quickshell": "niri-quickshell",
}

METRIC_PATHS: dict[str, tuple[tuple[str, ...], str]] = {
    "memory.total_bytes": (("memory", "mem_total_bytes"), "bytes"),
    "memory.available_bytes": (("memory", "mem_available_bytes"), "bytes"),
    "memory.used_bytes": (("memory", "used_bytes"), "bytes"),
    "memory.free_bytes": (("memory", "mem_free_bytes"), "bytes"),
    "memory.buffers_bytes": (("memory", "buffers_bytes"), "bytes"),
    "memory.cached_bytes": (("memory", "cached_bytes"), "bytes"),
    "memory.shmem_bytes": (("memory", "shmem_bytes"), "bytes"),
    "memory.sreclaimable_bytes": (("memory", "sreclaimable_bytes"), "bytes"),
    "memory.sunreclaim_bytes": (("memory", "sunreclaim_bytes"), "bytes"),
    "memory.slab_bytes": (("memory", "slab_bytes"), "bytes; total slab, not additive to its components"),
    "memory.anon_pages_bytes": (("memory", "anon_pages_bytes"), "bytes"),
    "memory.mapped_bytes": (("memory", "mapped_bytes"), "bytes"),
    "memory.kernel_stack_bytes": (("memory", "kernel_stack_bytes"), "bytes"),
    "memory.page_tables_bytes": (("memory", "page_tables_bytes"), "bytes"),
    "memory.unevictable_bytes": (("memory", "unevictable_bytes"), "bytes"),
    "memory.active_bytes": (("memory", "active_bytes"), "bytes; overlaps other meminfo categories"),
    "memory.inactive_bytes": (("memory", "inactive_bytes"), "bytes; overlaps other meminfo categories"),
    "memory.swap_used_bytes": (("memory", "swap_used_bytes"), "bytes"),
    "memory.session_pss_bytes": (("memory_scopes", "session", "pss_bytes"), "bytes"),
    "memory.session_swap_pss_bytes": (("memory_scopes", "session", "swap_pss_bytes"), "bytes"),
    "graphics.drm_resident_system_memory_bytes": (("graphics_memory", "drm_fdinfo", "resident_bytes_by_region", "memory"), "bytes; driver-reported DRM resident buffers"),
    "memory.user_manager_cgroup_current_bytes": (("systemd_user_cgroup", "end", "memory_current_bytes"), "bytes"),
    "cpu.idle_percent": (("cpu", "idle_percent_delta"), "percent"),
    "cpu.busy_percent": (("cpu", "busy_percent_delta"), "percent"),
    "cpu.context_switches_delta": (("cpu", "context_switches_delta"), "count/window"),
    "cpu.interrupts_delta": (("cpu", "interrupts_delta"), "count/window"),
    "cpu.session_minor_page_faults_delta": (("memory_scopes", "session", "minor_page_faults_delta"), "count/window"),
    "cpu.session_major_page_faults_delta": (("memory_scopes", "session", "major_page_faults_delta"), "count/window"),
    "psi.cpu.some_usec_delta": (("psi", "delta", "cpu", "some_total_usec_delta"), "microseconds/window"),
    "psi.cpu.full_usec_delta": (("psi", "delta", "cpu", "full_total_usec_delta"), "microseconds/window"),
    "psi.memory.some_usec_delta": (("psi", "delta", "memory", "some_total_usec_delta"), "microseconds/window"),
    "psi.memory.full_usec_delta": (("psi", "delta", "memory", "full_total_usec_delta"), "microseconds/window"),
    "psi.io.some_usec_delta": (("psi", "delta", "io", "some_total_usec_delta"), "microseconds/window"),
    "psi.io.full_usec_delta": (("psi", "delta", "io", "full_total_usec_delta"), "microseconds/window"),
    "process.session_count": (("memory_scopes", "session", "pid_count"), "processes"),
}
for _component in (
    "niri", "quickshell", "quickshell_auxiliary", "polkit_agent", "pipewire",
    "wireplumber", "networkmanager", "xwayland_satellite",
):
    METRIC_PATHS[f"process.{_component}.pss_bytes"] = (("components", _component, "pss_bytes"), "bytes")
    METRIC_PATHS[f"process.{_component}.cpu_seconds_delta"] = (
        ("components", _component, "cpu_seconds_delta"), "seconds/window"
    )
for _counter in ("stored_pages", "pool_total_size", "written_back_pages", "pool_limit_hit"):
    METRIC_PATHS[f"memory.zswap.{_counter}"] = (
        ("memory_management", "zswap_usage", _counter), "pages_or_counter"
    )

COMPATIBILITY_PATHS: dict[str, tuple[str, ...]] = {
    "architecture": ("system", "architecture"),
    "page_size_bytes": ("system", "page_size_bytes"),
    "Fedora ID": ("system", "fedora_asahi_release", "ID"),
    "Fedora VERSION_ID": ("system", "fedora_asahi_release", "VERSION_ID"),
    "hardware model": ("system", "asahi_hardware"),
    "kernel uname": ("system", "uname", "stdout"),
    "session type": ("system", "session_type"),
    "Niri+ version": ("system", "niri_plus_version"),
    "Niri compositor version": ("system", "niri_compositor_version", "stdout"),
    "Quickshell version": ("system", "quickshell_version", "stdout"),
    "Quickshell expected commit": ("system", "quickshell_expected_commit"),
    "Quickshell installed commit": ("system", "quickshell_installed_commit"),
}
OPTIONAL_COMPATIBILITY_PATHS = {
    "asahi-system commit": ("system", "asahi_system_commit", "stdout"),
}


class ComparisonError(ValueError):
    """An input capture is invalid or has untrustworthy profile evidence."""


def _walk(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for part in path:
        if isinstance(current, dict) and "status" in current:
            if current.get("status") != "AVAILABLE":
                return None
            current = current.get("value")
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    if isinstance(current, dict) and "status" in current:
        if current.get("status") != "AVAILABLE":
            return None
        return current.get("value")
    return current


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str) and re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value.strip()):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    return None


def _validate_capture(path: pathlib.Path, expected_profile: str) -> dict[str, Any]:
    try:
        capture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"{path}: cannot read valid JSON ({exc})") from exc
    if not isinstance(capture, dict) or capture.get("schema_version") != SCHEMA_VERSION:
        raise ComparisonError(f"{path}: expected collector schema_version {SCHEMA_VERSION}")
    if capture.get("read_only") is not True:
        raise ComparisonError(f"{path}: capture is not marked read-only")
    if capture.get("profile") != expected_profile:
        raise ComparisonError(f"{path}: expected profile {expected_profile!r}, found {capture.get('profile')!r}")
    validation = capture.get("profile_validation")
    if not isinstance(validation, dict) or validation.get("status") != "OK":
        raise ComparisonError(f"{path}: profile validation did not pass")
    runs = capture.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ComparisonError(f"{path}: no individual runs were captured")
    for index, run in enumerate(runs, 1):
        if not isinstance(run, dict) or run.get("schema_version") != SCHEMA_VERSION:
            raise ComparisonError(f"{path}: run {index} does not use schema_version {SCHEMA_VERSION}")
        if run.get("profile") != expected_profile:
            raise ComparisonError(f"{path}: run {index} is not profile {expected_profile!r}")
        run_validation = run.get("profile_validation")
        if not isinstance(run_validation, dict) or run_validation.get("status") != "OK":
            raise ComparisonError(f"{path}: run {index} failed profile validation")
        collector = run.get("collector")
        if not isinstance(collector, dict) or collector.get("read_only") is not True or collector.get("host_modified") is not False:
            raise ComparisonError(f"{path}: run {index} lacks read-only/no-host-change evidence")
    return capture


def assess_compatibility(
    captures: dict[str, dict[str, Any]], *,
    allow_quickshell_pin_change: bool = False,
    allow_asahi_system_commit_change: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    required_missing = False
    known_values: dict[str, dict[str, Any]] = {}
    for label, path in COMPATIBILITY_PATHS.items():
        observed = []
        missing = False
        for profile, capture in captures.items():
            values = [_walk(run, path) for run in capture["runs"]]
            if not values or any(value is None or value == "" for value in values):
                missing = True
                required_missing = True
                warnings.append(f"{profile}: {label} is unavailable; matching could not be verified")
                continue
            distinct = {json.dumps(value, sort_keys=True, ensure_ascii=False) for value in values}
            if len(distinct) != 1:
                issues.append(f"{profile}: {label} changes within the capture")
                continue
            observed.append((profile, values[0]))
        if not missing and len(observed) == len(captures):
            known_values[label] = {profile: value for profile, value in observed}
            distinct = {json.dumps(value, sort_keys=True, ensure_ascii=False) for _, value in observed}
            if len(distinct) != 1:
                if allow_quickshell_pin_change and label in {
                    "Quickshell expected commit", "Quickshell installed commit",
                }:
                    warnings.append(f"{label} differs between candidate captures as the tested variable")
                else:
                    issues.append(f"profiles disagree on {label}")
    for label, path in OPTIONAL_COMPATIBILITY_PATHS.items():
        observed = []
        for profile, capture in captures.items():
            values = [_walk(run, path) for run in capture["runs"]]
            if not values or any(value is None or value == "" for value in values):
                warnings.append(f"{profile}: optional {label} is unavailable")
                continue
            if len({json.dumps(value, sort_keys=True) for value in values}) != 1:
                issues.append(f"{profile}: {label} changes within the capture")
            else:
                observed.append((profile, values[0]))
        if len({json.dumps(value, sort_keys=True) for _, value in observed}) > 1:
            if allow_asahi_system_commit_change and label == "asahi-system commit":
                warnings.append(
                    "asahi-system commit differs; review that the candidate revision changes only the Quickshell pin/runtime selection"
                )
            else:
                issues.append(f"profiles disagree on {label}")

    for label, key, tolerance in (
        ("observation window", "observation_window_seconds", 0.05),
        ("profile sample period", "profile_sample_period_seconds", 0.01),
    ):
        per_profile: dict[str, float] = {}
        for profile, capture in captures.items():
            values = [
                _number(run.get("collector", {}).get(key))
                if isinstance(run.get("collector"), dict) else None
                for run in capture["runs"]
            ]
            if not values or any(value is None for value in values):
                required_missing = True
                warnings.append(f"{profile}: {label} is unavailable")
                continue
            per_profile[profile] = statistics.median(value for value in values if value is not None)
        if len(per_profile) == len(captures):
            observed_values = list(per_profile.values())
            threshold = max(tolerance, max(observed_values) * tolerance)
            if max(observed_values) - min(observed_values) > threshold:
                issues.append(f"profiles used different {label}s: {per_profile}")
            known_values[label] = per_profile

    expected = known_values.get("Quickshell expected commit", {})
    installed = known_values.get("Quickshell installed commit", {})
    if len(expected) == len(captures) and len(installed) == len(captures):
        for profile in captures:
            if expected[profile] != installed[profile]:
                issues.append(f"{profile}: installed Quickshell commit differs from the expected lock")
    status = "INCOMPATIBLE" if issues else "UNVERIFIED" if required_missing else "COMPATIBLE"
    return {
        "status": status,
        "issues": list(dict.fromkeys(issues)),
        "warnings": list(dict.fromkeys(warnings)),
        "verified_values": known_values,
        "required_fields": sorted(COMPATIBILITY_PATHS),
        "optional_fields": sorted(OPTIONAL_COMPATIBILITY_PATHS),
    }


def _summarize(values: list[float]) -> dict[str, float | int]:
    median = statistics.median(values)
    return {
        "n": len(values),
        "median": median,
        "mad": statistics.median(abs(value - median) for value in values),
        "min": min(values),
        "max": max(values),
    }


def _samples(capture: dict[str, Any], path: tuple[str, ...]) -> tuple[list[float], int]:
    values = []
    total = len(capture["runs"])
    for run in capture["runs"]:
        number = _number(_walk(run, path))
        if number is not None:
            values.append(number)
    return values, total


def compare_metric(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    path: tuple[str, ...],
    unit: str,
    comparable: bool,
) -> dict[str, Any]:
    before, before_total = _samples(baseline, path)
    after, after_total = _samples(candidate, path)
    result: dict[str, Any] = {
        "unit": unit,
        "baseline": _summarize(before) if before else None,
        "candidate": _summarize(after) if after else None,
        "delta": None,
        "delta_percent": None,
        "classification": "UNAVAILABLE",
        "missing_runs": {"baseline": before_total - len(before), "candidate": after_total - len(after)},
        "classification_rule": (
            f"requires at least {MIN_RUNS_FOR_CLASSIFICATION} complete runs on each side; "
            "direction must exceed twice the larger MAD. This is a conservative heuristic, not a significance test."
        ),
    }
    if not before or not after:
        return result
    before_stats = _summarize(before)
    after_stats = _summarize(after)
    delta = float(after_stats["median"]) - float(before_stats["median"])
    result["delta"] = delta
    baseline_median = float(before_stats["median"])
    result["delta_percent"] = delta * 100.0 / abs(baseline_median) if baseline_median != 0 else None
    if len(before) != before_total or len(after) != after_total:
        return result
    if not comparable or min(len(before), len(after)) < MIN_RUNS_FOR_CLASSIFICATION:
        result["classification"] = "INCONCLUSIVE"
    else:
        noise = 2 * max(float(before_stats["mad"]), float(after_stats["mad"]))
        if delta > noise:
            result["classification"] = "MEASURED_INCREASE"
        elif delta < -noise:
            result["classification"] = "MEASURED_DECREASE"
        else:
            result["classification"] = "INCONCLUSIVE"
    return result


def _metric_results(captures: dict[str, dict[str, Any]], comparable: bool) -> dict[str, Any]:
    pairs = (
        ("A_plasma_to_B_niri_core", "plasma", "niri_core"),
        ("B_niri_core_to_C_niri_quickshell", "niri_core", "niri_quickshell"),
        ("A_plasma_to_C_niri_quickshell", "plasma", "niri_quickshell"),
    )
    return {
        label: {
            name: compare_metric(captures[base], captures[candidate], path, unit, comparable)
            for name, (path, unit) in METRIC_PATHS.items()
        }
        for label, base, candidate in pairs
    }


def _process_identity(row: dict[str, Any]) -> str:
    argv = row.get("argv") or []
    executable = pathlib.Path(argv[0]).name if argv else row.get("name", "unknown")
    return f"{row.get('name', 'unknown')} [{executable}]"


def process_inventory(
    captures: dict[str, dict[str, Any]],
    baseline_key: str = "niri_core",
    candidate_key: str = "niri_quickshell",
) -> dict[str, Any]:
    inventory: dict[str, dict[str, dict[str, Any]]] = {}
    largest: dict[str, list[dict[str, Any]]] = {}
    for profile, capture in captures.items():
        counts: dict[str, int] = {}
        per_run_pss: list[dict[str, float | None] | None] = []
        for run in capture["runs"]:
            rows = _walk(run, ("session", "processes"))
            if not isinstance(rows, list):
                per_run_pss.append(None)
                continue
            pss_by_identity: dict[str, float | None] = {}
            present: set[str] = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                identity = _process_identity(row)
                present.add(identity)
                pss = _number(_walk(row, ("pss_bytes",)))
                # Multiple processes can share an executable identity. A missing
                # PSS for any one of them invalidates that run's aggregate.
                if pss is None:
                    pss_by_identity[identity] = None
                elif identity not in pss_by_identity:
                    pss_by_identity[identity] = pss
                elif pss_by_identity[identity] is not None:
                    pss_by_identity[identity] += pss
            for identity in present:
                counts[identity] = counts.get(identity, 0) + 1
            per_run_pss.append(pss_by_identity)
        inventory[profile] = {}
        for identity, count in sorted(counts.items()):
            # Process absence is not zero PSS. Summarize only runs where the
            # process was present, and never report a complete median when
            # smaps_rollup was unavailable for a present instance.
            observed = [
                run_pss[identity]
                for run_pss in per_run_pss
                if run_pss is not None and identity in run_pss and run_pss[identity] is not None
            ]
            inventory[profile][identity] = {
                "runs_present": count,
                "runs_total": len(capture["runs"]),
                "runs_with_pss": len(observed),
                "median_pss_bytes": statistics.median(observed) if len(observed) == count else None,
            }
        largest[profile] = sorted(
            (
                {"process": identity, **data}
                for identity, data in inventory[profile].items()
                if data["median_pss_bytes"] is not None
            ),
            key=lambda row: row["median_pss_bytes"],
            reverse=True,
        )[:10]
    baseline = set(inventory.get(baseline_key, {}))
    candidate = set(inventory.get(candidate_key, {}))
    result = {
        "by_profile": inventory,
        "largest_pss_by_profile": largest,
        "process_presence_changes": {
            "baseline_capture": baseline_key,
            "candidate_capture": candidate_key,
            "present_only_in_candidate": sorted(candidate - baseline),
            "present_only_in_baseline": sorted(baseline - candidate),
        },
        "notes": [
            "Process identity uses comm plus executable basename, not PID, so profiles compare across fresh processes.",
            "PSS is reported separately from system memory and cgroup memory; these totals are never added together.",
        ],
    }
    if baseline_key == "niri_core" and candidate_key == "niri_quickshell":
        result["quickshell_incremental_processes"] = {
            "present_only_with_quickshell": sorted(candidate - baseline),
            "present_only_without_quickshell": sorted(baseline - candidate),
        }
    return result


def compare_captures(
    plasma: dict[str, Any],
    niri_core: dict[str, Any],
    niri_quickshell: dict[str, Any],
) -> dict[str, Any]:
    captures = {"plasma": plasma, "niri_core": niri_core, "niri_quickshell": niri_quickshell}
    compatibility = assess_compatibility(captures)
    return {
        "schema_version": 1,
        "profiles": {name: {"profile": capture["profile"], "runs": len(capture["runs"])} for name, capture in captures.items()},
        "comparability": compatibility,
        "classification_policy": (
            f"At least {MIN_RUNS_FOR_CLASSIFICATION} complete runs per profile are required. "
            "The direction must exceed twice the larger median absolute deviation. This descriptive noise "
            "heuristic is not a formal significance test; incompatible or unverified metadata makes metrics inconclusive."
        ),
        "conditions_not_automatically_verified": [
            "display resolution/scale and monitor count",
            "theme and visual settings",
            "battery/charger and thermal conditions",
            "Wi-Fi/network activity and unrelated background workload",
        ],
        "comparisons": _metric_results(captures, compatibility["status"] == "COMPATIBLE"),
        "process_inventory": process_inventory(captures),
    }


def compare_files(plasma_path: pathlib.Path, core_path: pathlib.Path, quickshell_path: pathlib.Path) -> dict[str, Any]:
    paths = {
        "plasma": pathlib.Path(plasma_path),
        "niri_core": pathlib.Path(core_path),
        "niri_quickshell": pathlib.Path(quickshell_path),
    }
    captures = {
        name: _validate_capture(path, PROFILE_NAMES[name])
        for name, path in paths.items()
    }
    return compare_captures(captures["plasma"], captures["niri_core"], captures["niri_quickshell"])


def compare_quickshell_candidate_captures(
    known_good: dict[str, Any],
    candidate: dict[str, Any],
    *,
    allow_asahi_system_commit_change: bool = False,
) -> dict[str, Any]:
    """Compare two C captures while treating each capture's locked QS commit as the variable."""
    captures = {"known_good": known_good, "candidate": candidate}
    compatibility = assess_compatibility(
        captures,
        allow_quickshell_pin_change=True,
        allow_asahi_system_commit_change=allow_asahi_system_commit_change,
    )
    comparable = compatibility["status"] == "COMPATIBLE"
    commits = {}
    for name, capture in captures.items():
        commits[name] = {
            "quickshell_version": _walk(capture["runs"][-1], COMPATIBILITY_PATHS["Quickshell version"]),
            "quickshell_expected_commit": _walk(capture["runs"][-1], COMPATIBILITY_PATHS["Quickshell expected commit"]),
            "quickshell_installed_commit": _walk(capture["runs"][-1], COMPATIBILITY_PATHS["Quickshell installed commit"]),
            "asahi_system_commit": _walk(capture["runs"][-1], OPTIONAL_COMPATIBILITY_PATHS["asahi-system commit"]),
        }
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "mode": "quickshell-candidate-comparison",
        "profiles": {
            name: {"profile": capture["profile"], "runs": len(capture["runs"])}
            for name, capture in captures.items()
        },
        "revisions": commits,
        "comparability": compatibility,
        "classification_policy": (
            f"At least {MIN_RUNS_FOR_CLASSIFICATION} complete runs per side are required. "
            "The direction must exceed twice the larger median absolute deviation. This descriptive "
            "noise heuristic is not a formal significance test. A changed asahi-system source commit "
            "requires explicit review before its results are comparable."
        ),
        "conditions_not_automatically_verified": [
            "display resolution/scale and monitor count",
            "theme and visual settings",
            "battery/charger and thermal conditions",
            "Wi-Fi/network activity and unrelated background workload",
            "when asahi-system commits differ, review that the only intended system delta is selecting the Quickshell candidate",
        ],
        "comparisons": {
            name: compare_metric(known_good, candidate, path, unit, comparable)
            for name, (path, unit) in METRIC_PATHS.items()
        },
        "process_inventory": process_inventory(captures, "known_good", "candidate"),
    }


def compare_quickshell_candidate_files(
    known_good_path: pathlib.Path,
    candidate_path: pathlib.Path,
    *,
    allow_asahi_system_commit_change: bool = False,
) -> dict[str, Any]:
    known_good = _validate_capture(pathlib.Path(known_good_path), "niri-quickshell")
    candidate = _validate_capture(pathlib.Path(candidate_path), "niri-quickshell")
    return compare_quickshell_candidate_captures(
        known_good,
        candidate,
        allow_asahi_system_commit_change=allow_asahi_system_commit_change,
    )


def render_markdown(result: dict[str, Any]) -> str:
    code = chr(96)
    lines = [
        "# Niri+ A/B/C benchmark comparison",
        "",
        f"**Comparability:** {result['comparability']['status']}",
        "",
        "| Profile | Captured runs |",
        "|---|---:|",
    ]
    for info in result["profiles"].values():
        lines.append(f"| {code}{info['profile']}{code} | {info['runs']} |")
    lines.extend(["", "## Environment check", ""])
    lines.extend(f"- **Mismatch:** {item}" for item in result["comparability"]["issues"])
    lines.extend(f"- **Unverified:** {item}" for item in result["comparability"]["warnings"])
    if not result["comparability"]["issues"] and not result["comparability"]["warnings"]:
        lines.append("Required compatibility fields match across all captures.")
    lines += ["", "Conditions to keep and record manually:", ""]
    lines.extend(f"- {item}" for item in result["conditions_not_automatically_verified"])
    lines += ["", result["classification_policy"], ""]
    for pair, metrics in result["comparisons"].items():
        lines += [
            f"## {pair.replace('_', ' ')}",
            "",
            "| Metric | Baseline median | Candidate median | Δ | Δ % | MAD A/B | Range A/B | N A/B | Classification |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for name, item in metrics.items():
            baseline, candidate = item["baseline"], item["candidate"]
            if baseline is None or candidate is None:
                lines.append(
                    f"| {code}{name}{code} | unavailable | unavailable | — | — | — | "
                    f"— | {item['missing_runs']['baseline']}/{item['missing_runs']['candidate']} missing | {item['classification']} |"
                )
                continue
            percent = "—" if item["delta_percent"] is None else f"{item['delta_percent']:+.2f}%"
            lines.append(
                f"| {code}{name}{code} | {baseline['median']:.3f} {item['unit']} | "
                f"{candidate['median']:.3f} {item['unit']} | {item['delta']:+.3f} | {percent} | "
                f"{baseline['mad']:.3f}/{candidate['mad']:.3f} | {baseline['min']:.3f}–{baseline['max']:.3f} / "
                f"{candidate['min']:.3f}–{candidate['max']:.3f} | {baseline['n']}/{candidate['n']} | "
                f"{item['classification']} |"
            )
        lines.append("")
    inventory = result["process_inventory"]
    lines += ["## Quickshell incremental process inventory", ""]
    for key, label in (
        ("present_only_with_quickshell", "Observed only with Quickshell"),
        ("present_only_without_quickshell", "Observed only in Niri core"),
    ):
        names = inventory["quickshell_incremental_processes"][key]
        lines.append(label + ": " + (", ".join(f"{code}{name}{code}" for name in names) or "none observed"))
        lines.append("")
    for profile, entries in inventory["largest_pss_by_profile"].items():
        lines += [
            f"### Largest PSS — {profile}", "",
            "| Process | Median PSS | Runs present |", "|---|---:|---:|",
        ]
        if not entries:
            lines.append("| unavailable | — | — |")
        for item in entries:
            lines.append(
                f"| {code}{item['process']}{code} | {item['median_pss_bytes'] / (1024 * 1024):.2f} MiB | "
                f"{item['runs_present']}/{item['runs_total']} |"
            )
    lines += [
        "",
        "System-wide memory comes from /proc/meminfo. Per-process PSS and cgroup memory remain separate views.",
        "",
    ]
    return "\n".join(lines)


def render_quickshell_candidate_markdown(result: dict[str, Any]) -> str:
    code = chr(96)
    lines = [
        "# Niri+ Quickshell C0/C1 comparison",
        "",
        f"**Comparability:** {result['comparability']['status']}",
        "",
        "| Capture | Runs | Quickshell version | Expected commit | Installed commit | asahi-system commit |",
        "|---|---:|---|---|---|---|",
    ]
    for name, info in result["profiles"].items():
        revision = result["revisions"].get(name, {})
        value = lambda field: revision.get(field) or "UNAVAILABLE"
        lines.append(
            f"| {code}{name}{code} | {info['runs']} | {value('quickshell_version')} | "
            f"{value('quickshell_expected_commit')} | {value('quickshell_installed_commit')} | "
            f"{value('asahi_system_commit')} |"
        )
    lines += ["", "## Comparability review", ""]
    lines.extend(f"- **Mismatch:** {item}" for item in result["comparability"]["issues"])
    lines.extend(f"- **Review:** {item}" for item in result["comparability"]["warnings"])
    if not result["comparability"]["issues"] and not result["comparability"]["warnings"]:
        lines.append("Required host and runtime metadata match; each installed commit matches its capture lock.")
    lines += ["", result["classification_policy"], ""]
    lines += [
        "A = KNOWN-GOOD C0; B = Performance Candidate C1. Per-process PSS and global memory are "
        "separate views. The Quickshell commit is the intentional independent variable.",
        "",
        "## Metrics",
        "",
        "| Metric | C0 median | C1 median | Δ | Δ % | MAD C0/C1 | Range C0/C1 | N C0/C1 | Classification |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, item in result["comparisons"].items():
        before, after = item["baseline"], item["candidate"]
        if before is None or after is None:
            lines.append(
                f"| {code}{name}{code} | unavailable | unavailable | — | — | — | — | "
                f"{item['missing_runs']['baseline']}/{item['missing_runs']['candidate']} missing | "
                f"{item['classification']} |"
            )
            continue
        percent = "—" if item["delta_percent"] is None else f"{item['delta_percent']:+.2f}%"
        lines.append(
            f"| {code}{name}{code} | {before['median']:.3f} {item['unit']} | "
            f"{after['median']:.3f} {item['unit']} | {item['delta']:+.3f} | {percent} | "
            f"{before['mad']:.3f}/{after['mad']:.3f} | {before['min']:.3f}–{before['max']:.3f} / "
            f"{after['min']:.3f}–{after['max']:.3f} | {before['n']}/{after['n']} | "
            f"{item['classification']} |"
        )
    inventory = result["process_inventory"]
    delta = inventory["process_presence_changes"]
    lines += ["", "## Process presence", ""]
    lines.append("Present only in C1: " + (", ".join(f"{code}{name}{code}" for name in delta["present_only_in_candidate"]) or "none observed"))
    lines.append("Present only in C0: " + (", ".join(f"{code}{name}{code}" for name in delta["present_only_in_baseline"]) or "none observed"))
    for capture_name, rows in inventory["largest_pss_by_profile"].items():
        lines += ["", f"### Largest PSS — {capture_name}", "", "| Process | Median PSS | Runs present |", "|---|---:|---:|"]
        if not rows:
            lines.append("| unavailable | — | — |")
        for row in rows:
            lines.append(
                f"| {code}{row['process']}{code} | {row['median_pss_bytes'] / (1024 * 1024):.2f} MiB | "
                f"{row['runs_present']}/{row['runs_total']} |"
            )
    lines += ["", "Review capture privacy metadata before sharing. No result here establishes M1 performance unless the source captures are M1 captures.", ""]
    return "\n".join(lines)


def write_atomic(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = pathlib.Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
