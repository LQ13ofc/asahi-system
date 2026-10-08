"""Low-overhead process/cgroup observation for the existing performance collector."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import time
from typing import Any

AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
NOT_APPLICABLE = "NOT_APPLICABLE"
PROFILE_OK = "OK"
PROFILE_FAIL = "FAIL"

QS_UNIT = "asahi-quickshell.service"
QS_EXPECTED_ARGV = [
    "/usr/bin/qs",
    "--path",
    "/usr/local/share/niri-plus/quickshell/shell.qml",
]

def process_name(row: dict[str, Any]) -> str:
    argv = row.get("argv") or []
    if argv:
        return pathlib.Path(argv[0]).name
    return str(row.get("name", ""))


def named(*names: str):
    expected = set(names)
    return lambda row: row.get("name") in expected or process_name(row) in expected


COMPONENT_MATCHERS = {
    "niri": named("niri"),
    "quickshell": named("qs"),
    "pipewire": named("pipewire", "pipewire-pulse"),
    "wireplumber": named("wireplumber"),
    "networkmanager": named("NetworkManager"),
    # /proc/PID/stat comm is capped at TASK_COMM_LEN, so long executable names
    # must also be matched through argv[0].
    "xwayland_satellite": named("xwayland-satellite"),
    "polkit_agent": named("lxqt-policykit-agent"),
    "plasma": named("plasmashell"),
    "kwin_wayland": named("kwin_wayland"),
}


def metric(status: str, value: Any = None, note: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status}
    if value is not None:
        result["value"] = value
    if note:
        result["note"] = note
    return result


def read_text(path: pathlib.Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8", errors="replace"), None
    except PermissionError as exc:
        return None, str(exc)
    except (FileNotFoundError, NotADirectoryError, ProcessLookupError) as exc:
        return None, str(exc)
    except OSError as exc:
        return None, str(exc)


def error_status(error: str | None) -> str:
    if error and ("Permission denied" in error or "Operation not permitted" in error):
        return PERMISSION_REQUIRED
    return UNAVAILABLE


def command(args: list[str], timeout: float = 4.0) -> dict[str, Any]:
    if not shutil.which(args[0]):
        return metric(UNAVAILABLE, note=f"tool not installed: {args[0]}")
    try:
        done = subprocess.run(args, text=True, capture_output=True, check=False, timeout=timeout)
    except PermissionError as exc:
        return metric(PERMISSION_REQUIRED, note=str(exc))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return metric(UNAVAILABLE, note=str(exc))
    return metric(
        AVAILABLE if done.returncode == 0 else UNAVAILABLE,
        {"returncode": done.returncode, "stdout": done.stdout.strip(), "stderr": done.stderr.strip()},
    )


def parse_kv_lines(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            result[fields[0].rstrip(":")] = int(fields[1])
        except ValueError:
            continue
    return result


def parse_proc_stat(text: str) -> dict[str, int] | None:
    close = text.rfind(")")
    open_ = text.find("(")
    if open_ < 0 or close < open_:
        return None
    tail = text[close + 2 :].split()
    try:
        return {
            "ppid": int(tail[1]),
            "minor_faults": int(tail[7]),
            "major_faults": int(tail[9]),
            "cpu_ticks": int(tail[11]) + int(tail[12]),
            "start_time_ticks": int(tail[19]),
        }
    except (IndexError, ValueError):
        return None


def parse_proc_io(text: str | None) -> dict[str, int]:
    if not text:
        return {}
    result: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        try:
            result[key.strip()] = int(value.strip())
        except ValueError:
            continue
    return result


def parse_proc_status(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    result: dict[str, Any] = {}
    for line in text.splitlines():
        if line.startswith("Uid:"):
            fields = line.split()
            if len(fields) >= 2 and fields[1].isdigit():
                result["uid"] = int(fields[1])
        elif line.startswith("voluntary_ctxt_switches:"):
            try:
                result["voluntary_ctxt_switches"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("nonvoluntary_ctxt_switches:"):
            try:
                result["nonvoluntary_ctxt_switches"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return result


def parse_smaps_rollup(text: str | None) -> dict[str, int]:
    values = parse_kv_lines(text or "")
    return {key: value * 1024 for key, value in values.items()}


def parse_cgroup(text: str | None) -> str | None:
    for line in (text or "").splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0":
            return "/" + fields[2].lstrip("/")
    return None


def process_snapshot(root: pathlib.Path, ticks_per_second: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    proc = root / "proc"
    rows: list[dict[str, Any]] = []
    outcomes = {"available": 0, "permission": 0, "unavailable": 0}
    try:
        pid_dirs = sorted((p for p in proc.iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
    except PermissionError:
        return rows, {"available": 0, "permission": 1, "unavailable": 0}
    except OSError:
        return rows, {"available": 0, "permission": 0, "unavailable": 1}

    for pid_dir in pid_dirs:
        pid = int(pid_dir.name)
        stat_text, stat_error = read_text(pid_dir / "stat")
        parsed = parse_proc_stat(stat_text or "")
        if not parsed:
            continue
        open_ = (stat_text or "").find("(")
        close = (stat_text or "").rfind(")")
        name = (stat_text or "")[open_ + 1 : close] if open_ >= 0 and close > open_ else str(pid)
        cmdline_raw, _ = read_text(pid_dir / "cmdline")
        if cmdline_raw is not None:
            argv = [part for part in cmdline_raw.split("\x00") if part]
        else:
            argv = []
        status_text, _ = read_text(pid_dir / "status")
        status_values = parse_proc_status(status_text)
        cgroup_text, _ = read_text(pid_dir / "cgroup")
        io_text, io_error = read_text(pid_dir / "io")
        io_values = parse_proc_io(io_text)

        rollup_text, rollup_error = read_text(pid_dir / "smaps_rollup")
        smaps = parse_smaps_rollup(rollup_text)
        smaps_status = error_status(rollup_error) if rollup_error else AVAILABLE
        if smaps_status == AVAILABLE:
            outcomes["available"] += 1
        elif smaps_status == PERMISSION_REQUIRED:
            outcomes["permission"] += 1
        else:
            outcomes["unavailable"] += 1

        def smaps_metric(source: str) -> dict[str, Any]:
            if source in smaps:
                return metric(AVAILABLE, smaps[source])
            return metric(smaps_status, note=rollup_error or f"{source} unavailable")

        row = {
            "pid": pid,
            "ppid": parsed["ppid"],
            "uid": status_values.get("uid"),
            "name": name,
            "argv": argv,
            "cgroup": parse_cgroup(cgroup_text),
            "rss_bytes": smaps_metric("Rss"),
            "pss_bytes": smaps_metric("Pss"),
            "private_clean_bytes": smaps_metric("Private_Clean"),
            "private_dirty_bytes": smaps_metric("Private_Dirty"),
            "swap_pss_bytes": smaps_metric("SwapPss"),
            "cpu_ticks": metric(AVAILABLE, parsed["cpu_ticks"]),
            "cpu_seconds_accumulated": metric(AVAILABLE, parsed["cpu_ticks"] / ticks_per_second),
            "minor_page_faults": metric(AVAILABLE, parsed["minor_faults"]),
            "major_page_faults": metric(AVAILABLE, parsed["major_faults"]),
            "io_read_bytes": metric(AVAILABLE, io_values["read_bytes"]) if "read_bytes" in io_values else metric(error_status(io_error), note=io_error or "read_bytes unavailable"),
            "io_write_bytes": metric(AVAILABLE, io_values["write_bytes"]) if "write_bytes" in io_values else metric(error_status(io_error), note=io_error or "write_bytes unavailable"),
            "voluntary_context_switches": metric(AVAILABLE, status_values["voluntary_ctxt_switches"]) if "voluntary_ctxt_switches" in status_values else metric(UNAVAILABLE),
            "nonvoluntary_context_switches": metric(AVAILABLE, status_values["nonvoluntary_ctxt_switches"]) if "nonvoluntary_ctxt_switches" in status_values else metric(UNAVAILABLE),
            "_identity": (pid, parsed["start_time_ticks"]),
            "_counters": {
                "cpu_ticks": parsed["cpu_ticks"],
                "minor_page_faults": parsed["minor_faults"],
                "major_page_faults": parsed["major_faults"],
                "io_read_bytes": io_values.get("read_bytes"),
                "io_write_bytes": io_values.get("write_bytes"),
                "voluntary_context_switches": status_values.get("voluntary_ctxt_switches"),
                "nonvoluntary_context_switches": status_values.get("nonvoluntary_ctxt_switches"),
            },
        }
        rows.append(row)
    return rows, outcomes


def _delta(before: int | None, after: int | None) -> int | None:
    if before is None or after is None or after < before:
        return None
    return after - before


def attach_process_deltas(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    elapsed: float,
    ticks_per_second: int,
) -> None:
    previous = {tuple(row["_identity"]): row for row in before}
    for row in after:
        start = previous.get(tuple(row["_identity"]))
        if not start:
            for key in ("cpu_seconds_delta", "minor_page_faults_delta", "major_page_faults_delta",
                        "io_read_bytes_delta", "io_write_bytes_delta",
                        "voluntary_context_switches_delta", "nonvoluntary_context_switches_delta"):
                row[key] = metric(UNAVAILABLE, note="process did not exist at observation start")
            row["cpu_percent_one_core_delta"] = metric(UNAVAILABLE, note="process did not exist at observation start")
            row.pop("_identity", None)
            row.pop("_counters", None)
            continue
        a, b = start["_counters"], row["_counters"]
        cpu_ticks = _delta(a["cpu_ticks"], b["cpu_ticks"])
        cpu_seconds = cpu_ticks / ticks_per_second if cpu_ticks is not None else None
        row["cpu_seconds_delta"] = metric(AVAILABLE, cpu_seconds) if cpu_seconds is not None else metric(UNAVAILABLE)
        row["cpu_percent_one_core_delta"] = (
            metric(AVAILABLE, 100.0 * cpu_seconds / max(elapsed, 1e-9), f"observation window {elapsed:.3f}s")
            if cpu_seconds is not None else metric(UNAVAILABLE)
        )
        for source, output in (
            ("minor_page_faults", "minor_page_faults_delta"),
            ("major_page_faults", "major_page_faults_delta"),
            ("io_read_bytes", "io_read_bytes_delta"),
            ("io_write_bytes", "io_write_bytes_delta"),
            ("voluntary_context_switches", "voluntary_context_switches_delta"),
            ("nonvoluntary_context_switches", "nonvoluntary_context_switches_delta"),
        ):
            value = _delta(a.get(source), b.get(source))
            row[output] = metric(AVAILABLE, value) if value is not None else metric(UNAVAILABLE)
        row.pop("_identity", None)
        row.pop("_counters", None)
    for row in after:
        row.pop("_identity", None)
        row.pop("_counters", None)


def system_cpu_counters(root: pathlib.Path) -> dict[str, int] | None:
    text, _ = read_text(root / "proc/stat")
    line = next((line for line in (text or "").splitlines() if line.startswith("cpu ")), None)
    if not line:
        return None
    try:
        fields = [int(value) for value in line.split()[1:9]]  # through steal; guest counters remain excluded
    except ValueError:
        return None
    idle = fields[3] + fields[4]
    return {"total": sum(fields), "idle": idle}


def system_cpu_delta(before: dict[str, int] | None, after: dict[str, int] | None, elapsed: float) -> dict[str, Any]:
    if not before or not after:
        return {"idle_percent_delta": metric(UNAVAILABLE), "busy_percent_delta": metric(UNAVAILABLE)}
    total = after["total"] - before["total"]
    idle = after["idle"] - before["idle"]
    if total <= 0 or idle < 0:
        return {"idle_percent_delta": metric(UNAVAILABLE), "busy_percent_delta": metric(UNAVAILABLE)}
    idle_percent = max(0.0, min(100.0, 100.0 * idle / total))
    return {
        "idle_percent_delta": metric(AVAILABLE, idle_percent, f"observation window {elapsed:.3f}s"),
        "busy_percent_delta": metric(AVAILABLE, 100.0 - idle_percent, f"observation window {elapsed:.3f}s"),
    }


def parse_psi(text: str | None) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for line in (text or "").splitlines():
        fields = line.split()
        if not fields:
            continue
        values: dict[str, float | int] = {}
        for field in fields[1:]:
            if "=" not in field:
                continue
            key, raw = field.split("=", 1)
            try:
                values[key] = int(raw) if key == "total" else float(raw)
            except ValueError:
                continue
        result[fields[0]] = values
    return result


def psi_snapshot(root: pathlib.Path) -> dict[str, Any]:
    result = {}
    for kind in ("cpu", "memory", "io"):
        text, error = read_text(root / f"proc/pressure/{kind}")
        result[kind] = metric(AVAILABLE, parse_psi(text)) if text is not None else metric(error_status(error), note=error)
    return result


def psi_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for kind in ("cpu", "memory", "io"):
        a = before.get(kind, {})
        b = after.get(kind, {})
        if a.get("status") != AVAILABLE or b.get("status") != AVAILABLE:
            result[kind] = metric(UNAVAILABLE)
            continue
        values = {}
        for mode in ("some", "full"):
            av = a["value"].get(mode, {}).get("total")
            bv = b["value"].get(mode, {}).get("total")
            if isinstance(av, int) and isinstance(bv, int) and bv >= av:
                values[mode + "_total_usec_delta"] = bv - av
        result[kind] = metric(AVAILABLE, values)
    return result


def _qs_managed(row: dict[str, Any]) -> bool:
    argv = row.get("argv") or []
    if len(argv) != len(QS_EXPECTED_ARGV):
        return False
    return pathlib.Path(argv[0]).name == "qs" and argv[1:] == QS_EXPECTED_ARGV[1:]


def profile_signature(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def matching(name: str) -> list[dict[str, Any]]:
        return [row for row in rows if COMPONENT_MATCHERS[name](row)]
    qs = matching("quickshell")
    return {
        "niri_pids": [row["pid"] for row in matching("niri")],
        "kwin_wayland_pids": [row["pid"] for row in matching("kwin_wayland")],
        "plasmashell_pids": [row["pid"] for row in matching("plasma")],
        "polkit_agent_pids": [row["pid"] for row in matching("polkit_agent")],
        "qs_pids": [row["pid"] for row in qs],
        "managed_qs_pids": [row["pid"] for row in qs if _qs_managed(row)],
        "qs_argv": [{"pid": row["pid"], "argv": row.get("argv", [])} for row in qs],
    }


def lightweight_signature(root: pathlib.Path) -> dict[str, Any]:
    names = {"niri", "kwin_wayland", "plasmashell", "qs", "lxqt-policykit-"}
    rows = []
    try:
        entries = [entry for entry in (root / "proc").iterdir() if entry.name.isdigit()]
    except OSError:
        return profile_signature(rows)
    for entry in entries:
        stat, _ = read_text(entry / "stat")
        if not stat:
            continue
        open_, close = stat.find("("), stat.rfind(")")
        name = stat[open_ + 1 : close] if open_ >= 0 and close > open_ else ""
        if name not in names:
            continue
        raw, _ = read_text(entry / "cmdline")
        argv = [part for part in (raw or "").split("\x00") if part]
        rows.append({"pid": int(entry.name), "name": name, "argv": argv})
    return profile_signature(rows)


def observe_timeline(root: pathlib.Path, duration: float, sample_period: float) -> list[dict[str, Any]]:
    timeline = [{"offset_seconds": 0.0, **lightweight_signature(root)}]
    if duration <= 0:
        return timeline
    started = time.monotonic()
    period = max(0.2, sample_period)
    while True:
        remaining = duration - (time.monotonic() - started)
        if remaining <= 0:
            break
        time.sleep(min(period, remaining))
        timeline.append({"offset_seconds": time.monotonic() - started, **lightweight_signature(root)})
    return timeline


def systemd_user_unit(unit: str) -> dict[str, Any]:
    if not shutil.which("systemctl"):
        return {"probe_status": UNAVAILABLE, "EnableState": UNAVAILABLE}
    try:
        shown = subprocess.run(
            ["systemctl", "--user", "show", unit,
             "--property=ActiveState,SubState,Result,NRestarts,ExecMainPID,ControlGroup"],
            text=True, capture_output=True, check=False, timeout=4,
        )
        # is-enabled intentionally returns non-zero for masked/disabled states;
        # stdout is still the authoritative state and must not be discarded.
        enabled = subprocess.run(
            ["systemctl", "--user", "is-enabled", unit],
            text=True, capture_output=True, check=False, timeout=4,
        )
    except PermissionError:
        return {"probe_status": PERMISSION_REQUIRED, "EnableState": UNAVAILABLE}
    except (OSError, subprocess.TimeoutExpired):
        return {"probe_status": UNAVAILABLE, "EnableState": UNAVAILABLE}
    values: dict[str, Any] = {}
    values.update(line.split("=", 1) for line in (shown.stdout or "").splitlines() if "=" in line)
    values["EnableState"] = (enabled.stdout or "").strip() or "UNAVAILABLE"
    values["probe_status"] = AVAILABLE if shown.returncode == 0 else UNAVAILABLE
    return values


def systemd_user_manager_cgroup() -> str | None:
    if not shutil.which("systemctl"):
        return None
    try:
        shown = subprocess.run(
            ["systemctl", "--user", "show", "--property=ControlGroup", "--value"],
            text=True, capture_output=True, check=False, timeout=4,
        )
    except (PermissionError, OSError, subprocess.TimeoutExpired):
        return None
    value = (shown.stdout or "").strip()
    return value if shown.returncode == 0 and value.startswith("/") else None


def cgroup_snapshot(root: pathlib.Path, cgroup_path: str | None) -> dict[str, Any]:
    if not cgroup_path:
        return {
            "path": metric(UNAVAILABLE),
            "memory_current_bytes": metric(UNAVAILABLE),
            "cpu_usage_usec": metric(UNAVAILABLE),
            "io_read_bytes": metric(UNAVAILABLE),
            "io_write_bytes": metric(UNAVAILABLE),
        }
    base = root / "sys/fs/cgroup" / cgroup_path.lstrip("/")
    memory_text, memory_error = read_text(base / "memory.current")
    cpu_text, cpu_error = read_text(base / "cpu.stat")
    io_text, io_error = read_text(base / "io.stat")
    cpu = parse_kv_lines(cpu_text or "")
    rbytes = wbytes = 0
    io_ok = io_text is not None
    for line in (io_text or "").splitlines():
        for field in line.split()[1:]:
            if field.startswith("rbytes="):
                try:
                    rbytes += int(field.split("=", 1)[1])
                except ValueError:
                    io_ok = False
            elif field.startswith("wbytes="):
                try:
                    wbytes += int(field.split("=", 1)[1])
                except ValueError:
                    io_ok = False
    try:
        memory_current = int((memory_text or "").strip())
        memory_metric = metric(AVAILABLE, memory_current)
    except ValueError:
        memory_metric = metric(error_status(memory_error), note=memory_error or "malformed memory.current")
    return {
        "path": metric(AVAILABLE, cgroup_path),
        "memory_current_bytes": memory_metric,
        "cpu_usage_usec": metric(AVAILABLE, cpu["usage_usec"]) if "usage_usec" in cpu else metric(error_status(cpu_error), note=cpu_error),
        "io_read_bytes": metric(AVAILABLE, rbytes) if io_ok else metric(error_status(io_error), note=io_error),
        "io_write_bytes": metric(AVAILABLE, wbytes) if io_ok else metric(error_status(io_error), note=io_error),
    }


def cgroup_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key in ("cpu_usage_usec", "io_read_bytes", "io_write_bytes"):
        a, b = before.get(key, {}), after.get(key, {})
        if a.get("status") == AVAILABLE and b.get("status") == AVAILABLE:
            value = _delta(a.get("value"), b.get("value"))
            result[key + "_delta"] = metric(AVAILABLE, value) if value is not None else metric(UNAVAILABLE)
        else:
            result[key + "_delta"] = metric(UNAVAILABLE)
    return result


def _sum_metric(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = []
    missing = []
    for row in rows:
        item = row.get(key, {})
        if item.get("status") == AVAILABLE and isinstance(item.get("value"), (int, float)):
            values.append(item["value"])
        else:
            missing.append(row["pid"])
    if not rows:
        return metric(AVAILABLE, 0)
    status = AVAILABLE if not missing else UNAVAILABLE
    note = None if not missing else f"incomplete: unavailable for PIDs {missing}"
    return metric(status, sum(values), note)


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pid_count": len(rows),
        "pids": [row["pid"] for row in rows],
        "pss_bytes": _sum_metric(rows, "pss_bytes"),
        "rss_bytes": _sum_metric(rows, "rss_bytes"),
        "private_clean_bytes": _sum_metric(rows, "private_clean_bytes"),
        "private_dirty_bytes": _sum_metric(rows, "private_dirty_bytes"),
        "swap_pss_bytes": _sum_metric(rows, "swap_pss_bytes"),
        "cpu_seconds_delta": _sum_metric(rows, "cpu_seconds_delta"),
        "minor_page_faults_delta": _sum_metric(rows, "minor_page_faults_delta"),
        "major_page_faults_delta": _sum_metric(rows, "major_page_faults_delta"),
        "io_read_bytes_delta": _sum_metric(rows, "io_read_bytes_delta"),
        "io_write_bytes_delta": _sum_metric(rows, "io_write_bytes_delta"),
        "voluntary_context_switches_delta": _sum_metric(rows, "voluntary_context_switches_delta"),
        "nonvoluntary_context_switches_delta": _sum_metric(rows, "nonvoluntary_context_switches_delta"),
    }


def descendants(rows: list[dict[str, Any]], roots: set[int]) -> set[int]:
    children: dict[int, list[int]] = {}
    for row in rows:
        children.setdefault(row.get("ppid", -1), []).append(row["pid"])
    found: set[int] = set()
    stack = list(roots)
    while stack:
        parent = stack.pop()
        for child in children.get(parent, []):
            if child in found or child in roots:
                continue
            found.add(child)
            stack.append(child)
    return found


def collector_ancestor_pids(rows: list[dict[str, Any]]) -> set[int]:
    by_pid = {row["pid"]: row for row in rows}
    current = os.getpid()
    result = {current}
    while current in by_pid:
        parent = by_pid[current].get("ppid")
        if not isinstance(parent, int) or parent <= 1 or parent in result:
            break
        result.add(parent)
        current = parent
    return result


def aggregate_scopes(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    excluded = collector_ancestor_pids(rows)
    uid = os.getuid()
    session_rows = [row for row in rows if row.get("uid") == uid and row["pid"] not in excluded]
    components = {}
    for name, matcher in COMPONENT_MATCHERS.items():
        selected = [row for row in rows if matcher(row)]
        components[name] = aggregate_rows(selected)
    qs_pids = set(components["quickshell"]["pids"])
    aux_pids = descendants(rows, qs_pids)
    aux_rows = [row for row in rows if row["pid"] in aux_pids]
    components["quickshell_auxiliary"] = aggregate_rows(aux_rows)
    components["quickshell_auxiliary"]["processes"] = [
        {"pid": row["pid"], "name": row["name"], "argv": row.get("argv", []), "cgroup": row.get("cgroup")}
        for row in aux_rows
    ]

    recognized = set()
    for item in components.values():
        recognized.update(item.get("pids", []))
    ignored_names = {"systemd", "(sd-pam)", "dbus-broker", "dbus-daemon", "gpg-agent"}
    unexpected = [
        {"pid": row["pid"], "name": row["name"], "argv": row.get("argv", []), "cgroup": row.get("cgroup")}
        for row in session_rows
        if row["pid"] not in recognized and row["name"] not in ignored_names
    ]
    session = aggregate_rows(session_rows)
    session["definition"] = (
        "Unique final-sample processes whose real UID equals the benchmark user, excluding the "
        "collector and its ancestor chain. Each PID contributes PSS exactly once. Component groups "
        "are views over these/all processes and are never added back into session PSS."
    )
    session["excluded_benchmark_pids"] = sorted(excluded)
    session["unexpected_session_processes"] = unexpected
    return session, components


def validate_profile(
    profile: str | None,
    timeline: list[dict[str, Any]],
    unit_before: dict[str, Any],
    unit_after: dict[str, Any],
) -> dict[str, Any]:
    if profile is None:
        return {"status": "NOT_REQUESTED", "evidence": {}}
    failures: list[str] = []
    for sample in timeline:
        niri = len(sample["niri_pids"])
        kwin = len(sample["kwin_wayland_pids"])
        plasma = len(sample["plasmashell_pids"])
        qs = len(sample["qs_pids"])
        managed = len(sample["managed_qs_pids"])
        polkit = len(sample.get("polkit_agent_pids", []))
        if profile == "plasma":
            if kwin < 1 or plasma < 1:
                failures.append("Plasma/KWin were not continuously present")
            if niri != 0:
                failures.append("Niri appeared during Plasma measurement")
            if qs != 0:
                failures.append("Quickshell appeared during Plasma measurement")
        elif profile == "niri-core":
            if niri < 1:
                failures.append("Niri was not continuously present")
            if kwin != 0 or plasma != 0:
                failures.append("Plasma/KWin appeared during niri-core measurement")
            if polkit < 1:
                failures.append("PolicyKit agent was not continuously present in niri-core")
            if qs != 0:
                failures.append("Quickshell appeared during niri-core measurement")
        elif profile == "niri-quickshell":
            if niri < 1:
                failures.append("Niri was not continuously present")
            if kwin != 0 or plasma != 0:
                failures.append("Plasma/KWin appeared during niri-quickshell measurement")
            if polkit < 1:
                failures.append("PolicyKit agent was not continuously present in niri-quickshell")
            if qs != 1 or managed != 1:
                failures.append("expected exactly one managed Quickshell process throughout the window")
        else:
            failures.append(f"unknown profile: {profile}")

    if profile == "niri-core":
        for point, unit in (("start", unit_before), ("end", unit_after)):
            if unit.get("EnableState") != "masked-runtime":
                failures.append(f"Quickshell unit was not runtime-masked at {point}")
            if unit.get("ActiveState") not in {"inactive", "failed"}:
                failures.append(f"Quickshell unit was not inactive at {point}")
    elif profile == "niri-quickshell":
        for point, unit in (("start", unit_before), ("end", unit_after)):
            if unit.get("ActiveState") != "active" or unit.get("SubState") != "running":
                failures.append(f"Quickshell unit was not active/running at {point}")
            if unit.get("Result") not in {"success", ""}:
                failures.append(f"Quickshell unit Result was not success at {point}")
            try:
                if int(unit.get("NRestarts", "0")) != 0:
                    failures.append(f"Quickshell unit reported restarts at {point}")
            except ValueError:
                failures.append(f"Quickshell restart counter unreadable at {point}")
        final_qs = timeline[-1]["managed_qs_pids"]
        try:
            exec_pid = int(unit_after.get("ExecMainPID", "0"))
        except ValueError:
            exec_pid = 0
        if len(final_qs) != 1 or exec_pid != final_qs[0]:
            failures.append("managed Quickshell PID did not match systemd ExecMainPID")

    unique_failures = list(dict.fromkeys(failures))
    return {
        "status": PROFILE_OK if not unique_failures else PROFILE_FAIL,
        "evidence": {
            "timeline": timeline,
            "quickshell_unit_start": unit_before,
            "quickshell_unit_end": unit_after,
            "failures": unique_failures,
        },
    }


def observe(
    *,
    root_prefix: pathlib.Path | None = None,
    duration: float = 10.0,
    sample_period: float = 0.5,
    profile: str | None = None,
) -> dict[str, Any]:
    root = root_prefix or pathlib.Path("/")
    try:
        ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError):
        ticks_per_second = 100

    before_rows, before_outcomes = process_snapshot(root, ticks_per_second)
    cpu_before = system_cpu_counters(root)
    psi_before = psi_snapshot(root)

    real_host = root_prefix is None
    unit_before = systemd_user_unit(QS_UNIT) if real_host else {"probe_status": NOT_APPLICABLE, "EnableState": NOT_APPLICABLE}
    cgroup_before = (
        cgroup_snapshot(root, unit_before.get("ControlGroup"))
        if real_host else cgroup_snapshot(root, None)
    )
    user_manager_path = systemd_user_manager_cgroup() if real_host else None
    user_manager_before = cgroup_snapshot(root, user_manager_path)

    started = time.monotonic()
    timeline = observe_timeline(root, max(0.0, duration), sample_period)
    elapsed = max(0.0, time.monotonic() - started)

    after_rows, after_outcomes = process_snapshot(root, ticks_per_second)
    cpu_after = system_cpu_counters(root)
    psi_after = psi_snapshot(root)
    attach_process_deltas(before_rows, after_rows, elapsed, ticks_per_second)

    unit_after = systemd_user_unit(QS_UNIT) if real_host else {"probe_status": NOT_APPLICABLE, "EnableState": NOT_APPLICABLE}
    cgroup_after = (
        cgroup_snapshot(root, unit_after.get("ControlGroup"))
        if real_host else cgroup_snapshot(root, None)
    )
    user_manager_after = cgroup_snapshot(root, user_manager_path)
    session, components = aggregate_scopes(after_rows)
    validation = validate_profile(profile, timeline, unit_before, unit_after)

    return {
        "observation_window_seconds": elapsed,
        "profile_validation": validation,
        "processes": after_rows,
        "process_read_outcomes": {
            "start": before_outcomes,
            "end": after_outcomes,
        },
        "memory_scopes": {
            "session": session,
            "aggregation_rule": (
                "System-wide memory comes from /proc/meminfo. Session PSS is the sum of unique "
                "final-sample user-session PIDs only. Per-process and component views are reported "
                "separately and are not added to session or system totals."
            ),
        },
        "components": components,
        "cpu_delta": system_cpu_delta(cpu_before, cpu_after, elapsed),
        "psi_start": psi_before,
        "psi_end": psi_after,
        "psi_delta": psi_delta(psi_before, psi_after),
        "systemd_user_cgroup": {
            "start": user_manager_before,
            "end": user_manager_after,
            "delta": cgroup_delta(user_manager_before, user_manager_after),
            "note": "systemd --user manager cgroup; reported separately from PSS aggregates.",
        },
        "quickshell_cgroup": {
            "start": cgroup_before,
            "end": cgroup_after,
            "delta": cgroup_delta(cgroup_before, cgroup_after),
            "note": (
                "The systemd Quickshell cgroup delta captures CPU and I/O from short-lived children "
                "that may exit before the final /proc process sample."
            ),
        },
    }
