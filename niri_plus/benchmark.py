"""Frontend for the existing collector and temporary niri-core runtime profile."""

from __future__ import annotations

import json
import fcntl
import os
import pathlib
import stat
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from typing import Iterator


QS_UNIT = "asahi-quickshell.service"
QS_EXPECTED = ["/usr/bin/qs", "--path", "/usr/local/share/niri-plus/quickshell/shell.qml"]


def data_dir() -> pathlib.Path:
    explicit = os.environ.get("NIRI_PLUS_DATA_DIR")
    if explicit:
        return pathlib.Path(explicit)
    return pathlib.Path(__file__).resolve().parents[1]


def runtime_marker() -> pathlib.Path:
    runtime = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "niri-plus/benchmark-niri-core.json"


def _systemctl(args: list[str], runner=subprocess.run) -> subprocess.CompletedProcess[str]:
    command = ["systemctl", "--user", *args]
    try:
        return runner(command, text=True, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(command, 124, stdout="", stderr=str(exc))


def _state(runner=subprocess.run) -> dict[str, str]:
    enabled = _systemctl(["is-enabled", QS_UNIT], runner)
    active = _systemctl(["is-active", QS_UNIT], runner)
    return {
        "enabled": (enabled.stdout or "").strip(),
        "active": (active.stdout or "").strip(),
    }


def _qs_processes(proc_root: pathlib.Path = pathlib.Path("/proc")) -> list[dict[str, object]]:
    found = []
    try:
        entries = [entry for entry in proc_root.iterdir() if entry.name.isdigit()]
    except OSError as exc:
        raise OSError(f"cannot enumerate {proc_root}: {exc}") from exc
    for entry in entries:
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        argv = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        if argv and pathlib.Path(argv[0]).name == "qs":
            found.append({"pid": int(entry.name), "argv": argv})
    return found


class BenchmarkStateError(RuntimeError):
    pass


def _validate_runtime_directory(marker: pathlib.Path) -> None:
    runtime = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    try:
        runtime_stat = runtime.lstat()
    except OSError as exc:
        raise BenchmarkStateError(f"XDG_RUNTIME_DIR is unavailable: {exc}") from exc
    if not stat.S_ISDIR(runtime_stat.st_mode) or runtime_stat.st_uid != os.getuid() or stat.S_IMODE(runtime_stat.st_mode) & 0o077:
        raise BenchmarkStateError("XDG_RUNTIME_DIR must be a user-owned private directory")
    try:
        marker.parent.mkdir(mode=0o700, parents=False, exist_ok=True)
        directory_stat = marker.parent.lstat()
    except OSError as exc:
        raise BenchmarkStateError(f"cannot prepare private benchmark runtime directory: {exc}") from exc
    if (not stat.S_ISDIR(directory_stat.st_mode) or directory_stat.st_uid != os.getuid()
            or stat.S_IMODE(directory_stat.st_mode) & 0o077):
        raise BenchmarkStateError("benchmark runtime directory must be user-owned, private, and not a symlink")


@contextmanager
def _runtime_state_lock(marker: pathlib.Path) -> Iterator[None]:
    """Serialize cooperating prepare/restore calls without leaving a lock file."""
    _validate_runtime_directory(marker)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(marker.parent, flags)
    except OSError as exc:
        raise BenchmarkStateError(f"cannot open benchmark runtime directory safely: {exc}") from exc
    acquired = False
    try:
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise BenchmarkStateError("another niri+ benchmark prepare/restore is already in progress") from exc
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(directory_fd, fcntl.LOCK_UN)
        finally:
            os.close(directory_fd)


def _marker_exists(marker: pathlib.Path) -> bool:
    try:
        marker.lstat()
    except FileNotFoundError:
        return False
    return True


def _read_marker(marker: pathlib.Path) -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(marker, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise BenchmarkStateError("runtime marker must be a regular user-owned mode-0600 file")
        with os.fdopen(fd, "r", encoding="utf-8") as marker_file:
            fd = -1
            payload = json.load(marker_file)
    except BenchmarkStateError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise BenchmarkStateError(f"runtime marker is missing or corrupt: {exc}") from exc
    finally:
        if "fd" in locals() and isinstance(fd, int) and fd >= 0:
            os.close(fd)
    if not isinstance(payload, dict):
        raise BenchmarkStateError("runtime marker must contain a JSON object")
    if (payload.get("schema_version") != 2 or payload.get("unit") != QS_UNIT
            or payload.get("phase") not in {"preparing", "prepared", "restoring", "restore-required"}
            or not isinstance(payload.get("operation_id"), str)):
        raise BenchmarkStateError("runtime marker is not a supported Niri+ benchmark operation")
    return payload


def _write_initial_marker(marker: pathlib.Path, payload: dict[str, object]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(marker, flags, 0o600)
    except OSError as exc:
        raise BenchmarkStateError(f"cannot create runtime marker without replacing existing state: {exc}") from exc
    try:
        data = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        written = os.write(fd, data)
        if written != len(data):
            raise OSError("short write while creating runtime marker")
        os.fsync(fd)
    except OSError:
        os.close(fd)
        marker.unlink(missing_ok=True)
        raise
    else:
        os.close(fd)


def _write_marker(marker: pathlib.Path, payload: dict[str, object]) -> None:
    current = _read_marker(marker)
    if current.get("operation_id") != payload.get("operation_id"):
        raise BenchmarkStateError("runtime marker ownership changed during operation")
    temp = marker.with_name(f".{marker.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, 0o600)
    try:
        data = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        written = os.write(fd, data)
        if written != len(data):
            raise OSError("short write while updating runtime marker")
        os.fsync(fd)
    except OSError:
        os.close(fd)
        temp.unlink(missing_ok=True)
        raise
    else:
        os.close(fd)
    try:
        os.replace(temp, marker)
        directory_fd = os.open(marker.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)


def _remove_marker(marker: pathlib.Path, payload: dict[str, object]) -> None:
    current = _read_marker(marker)
    if current.get("operation_id") != payload.get("operation_id"):
        raise BenchmarkStateError("refusing to remove a marker owned by another operation")
    marker.unlink()
    directory_fd = os.open(marker.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _is_niri_session() -> bool:
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("XDG_SESSION_DESKTOP") or "").lower()
    return "niri" in desktop


def _restore_owned_state(marker: pathlib.Path, payload: dict[str, object], runner) -> tuple[bool, str]:
    """Idempotently restore the exact pre-prepare enable/active state."""
    original_enabled = str(payload["previous_enable_state"])
    original_active = str(payload["previous_active_state"])
    state = _state(runner)
    try:
        processes = _qs_processes()
    except OSError as exc:
        return False, f"cannot verify Quickshell process ownership ({exc}); marker preserved"

    if state["enabled"] == "masked-runtime":
        if state["active"] == "active" or processes:
            return False, f"Quickshell was started or assumed while runtime-masked (active={state['active']}, qs={processes}); marker preserved"
        _write_marker(marker, {**payload, "phase": "restoring"})
        unmasked = _systemctl(["unmask", "--runtime", QS_UNIT], runner)
        if unmasked.returncode != 0:
            message = (unmasked.stderr or unmasked.stdout or "systemctl unmask failed").strip()
            _write_marker(marker, {**payload, "phase": "restore-required", "last_error": message})
            return False, f"runtime unmask failed: {message}"
        state = _state(runner)

    if state["enabled"] != original_enabled:
        return False, f"enable state changed outside this experiment ({state['enabled']}; expected {original_enabled}); marker preserved"

    if not _is_niri_session():
        if state["active"] == "active" or processes:
            return False, f"Quickshell is unexpectedly active outside Niri (active={state['active']}, qs={processes}); marker preserved"
        _remove_marker(marker, payload)
        return True, "runtime mask removed; Niri is no longer the active session, so Quickshell was not started"

    if state["active"] == "active" and len(processes) == 1 and processes[0].get("argv") == QS_EXPECTED:
        _remove_marker(marker, payload)
        return True, "Quickshell was already restored to one managed active process"
    if state["active"] != "inactive" or processes:
        return False, f"cannot safely restart Quickshell (active={state['active']}, qs={processes}); marker preserved"
    if original_active == "active":
        _write_marker(marker, {**payload, "phase": "restoring"})
        started = _systemctl(["start", QS_UNIT], runner)
        if started.returncode != 0:
            message = (started.stderr or started.stdout or "systemctl start failed").strip()
            _write_marker(marker, {**payload, "phase": "restore-required", "last_error": message})
            return False, f"Quickshell restart failed: {message}"
        state = _state(runner)
        try:
            processes = _qs_processes()
        except OSError as exc:
            _write_marker(marker, {**payload, "phase": "restore-required", "last_error": str(exc)})
            return False, f"cannot verify Quickshell after restart ({exc}); marker preserved"
        if state["enabled"] != original_enabled or state["active"] != "active" or len(processes) != 1 or processes[0].get("argv") != QS_EXPECTED:
            detail = f"active={state['active']}, enabled={state['enabled']}, qs={processes}"
            _write_marker(marker, {**payload, "phase": "restore-required", "last_error": detail})
            return False, f"Quickshell did not return to its prior single managed state ({detail})"
    _remove_marker(marker, payload)
    return True, "runtime mask removed and prior Quickshell state verified"


def prepare_niri_core(runner=subprocess.run) -> int:
    """Temporarily runtime-mask Quickshell for the current user manager."""
    if os.geteuid() == 0:
        print("REFUSED: niri-core preparation must run as the desktop user, not root.", file=sys.stderr)
        return 2
    marker = runtime_marker()
    if not _is_niri_session():
        print("REFUSED: prepare niri-core from the active Niri session, then wait 2-3 minutes before measuring.", file=sys.stderr)
        return 2
    try:
        with _runtime_state_lock(marker):
            if _marker_exists(marker):
                print(f"REFUSED: benchmark runtime marker already exists: {marker}", file=sys.stderr)
                return 2
            before = _state(runner)
            if before["enabled"] in {"masked", "masked-runtime"}:
                print("REFUSED: Quickshell was already masked; benchmark will not take ownership of that state.", file=sys.stderr)
                return 2
            before_qs = _qs_processes()
            managed_before = [item for item in before_qs if item.get("argv") == QS_EXPECTED]
            if before["active"] != "active" or len(before_qs) != 1 or len(managed_before) != 1:
                print(
                    "REFUSED: niri-core preparation requires the normal KNOWN-GOOD Quickshell state "
                    f"(active unit + exactly one managed qs); active={before['active']}, qs={before_qs}.",
                    file=sys.stderr,
                )
                return 2
            payload: dict[str, object] = {
                "schema_version": 2,
                "operation_id": uuid.uuid4().hex,
                "unit": QS_UNIT,
                "previous_enable_state": before["enabled"],
                "previous_active_state": before["active"],
                "previous_qs_pid": managed_before[0]["pid"],
                "prepared_at_unix": time.time(),
                "phase": "preparing",
            }
            _write_initial_marker(marker, payload)

            masked = _systemctl(["mask", "--runtime", "--now", QS_UNIT], runner)
            after = _state(runner)
            try:
                qs = _qs_processes()
            except OSError as exc:
                qs = [{"error": str(exc)}]
            if masked.returncode != 0 or after["enabled"] != "masked-runtime" or after["active"] == "active" or qs:
                detail = (masked.stderr or masked.stdout or "").strip()
                if not detail:
                    detail = f"mask verification failed (enabled={after['enabled']}, active={after['active']}, qs={qs})"
                payload = {**payload, "phase": "restore-required", "last_error": detail}
                _write_marker(marker, payload)
                restored, restore_detail = _restore_owned_state(marker, payload, runner)
                if not restored:
                    _write_marker(marker, {**payload, "phase": "restore-required", "last_error": restore_detail})
                    print(
                        f"FAILED: niri-core preparation failed ({detail}); recovery is incomplete: {restore_detail}. "
                        "Retry with niri+ benchmark --restore-niri-core; marker preserved.",
                        file=sys.stderr,
                    )
                else:
                    print(f"FAILED: niri-core preparation failed ({detail}); prior state restored.", file=sys.stderr)
                return 2

            payload = {**payload, "phase": "prepared"}
            _write_marker(marker, payload)
            print("niri-core prepared: Quickshell is runtime-masked and stopped for this user manager.")
            print("Wait 2-3 minutes at idle, then run niri+ benchmark --profile niri-core ...")
            return 0
    except BenchmarkStateError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"FAILED: niri-core preparation could not safely inspect/restore state: {exc}", file=sys.stderr)
        return 2


def restore_niri_core(runner=subprocess.run) -> int:
    """Remove only the runtime mask created by prepare_niri_core."""
    if os.geteuid() == 0:
        print("REFUSED: niri-core restore must run as the desktop user, not root.", file=sys.stderr)
        return 2
    marker = runtime_marker()
    try:
        with _runtime_state_lock(marker):
            payload = _read_marker(marker)
            restored, detail = _restore_owned_state(marker, payload, runner)
            if not restored:
                _write_marker(marker, {**payload, "phase": "restore-required", "last_error": detail})
                print(f"FAILED: {detail}. Retry with niri+ benchmark --restore-niri-core; marker preserved.", file=sys.stderr)
                return 2
            print(f"niri-core restored: {detail}.")
            return 0
    except BenchmarkStateError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"FAILED: niri-core restore could not safely inspect state: {exc}; marker preserved if present", file=sys.stderr)
        return 2


def run_benchmark(
    profile: str,
    runs: int = 1,
    window: float = 10.0,
    output: str | None = None,
    runner=subprocess.run,
) -> int:
    collector = data_dir() / "scripts/collect-performance-baseline"
    if not collector.is_file():
        print(f"UNAVAILABLE: benchmark collector not found at {collector}", file=sys.stderr)
        return 2
    command = [
        sys.executable,
        str(collector),
        "--profile",
        profile,
        "--runs",
        str(runs),
        "--window",
        str(window),
    ]
    if output:
        command.extend(["--output", output])
    return runner(command, check=False).returncode


def run_overhead_diagnostic(runs: int = 3, output: str | None = None, runner=subprocess.run) -> int:
    collector = data_dir() / "scripts/collect-performance-baseline"
    if not collector.is_file():
        print(f"UNAVAILABLE: benchmark collector not found at {collector}", file=sys.stderr)
        return 2
    command = [sys.executable, str(collector), "--diagnose-overhead", "--runs", str(runs)]
    if output:
        command.extend(["--output", output])
    return runner(command, check=False).returncode


def run_memory_diagnostic(
    json_output: str | None = None,
    window: float = 2.0,
    sample_period: float = 0.5,
    series: bool = False,
    include_60_minutes: bool = False,
    runner=subprocess.run,
) -> int:
    collector = data_dir() / "scripts/collect-performance-baseline"
    if not collector.is_file():
        print(f"UNAVAILABLE: benchmark collector not found at {collector}", file=sys.stderr)
        return 2
    command = [
        sys.executable,
        str(collector),
        "--memory-series" if series else "--memory-diagnostic",
        "--memory-window",
        str(window),
        "--memory-sample-period",
        str(sample_period),
    ]
    if include_60_minutes:
        command.append("--include-60-minutes")
    if json_output:
        command.extend(["--output", json_output])
    return runner(command, check=False).returncode


def compare_benchmarks(
    plasma: str,
    niri_core: str,
    niri_quickshell: str,
    output: str,
    json_output: str | None = None,
) -> int:
    from . import benchmark_compare

    try:
        result = benchmark_compare.compare_files(
            pathlib.Path(plasma),
            pathlib.Path(niri_core),
            pathlib.Path(niri_quickshell),
        )
        markdown = benchmark_compare.render_markdown(result)
        benchmark_compare.write_atomic(pathlib.Path(output), markdown)
        if json_output:
            benchmark_compare.write_atomic(
                pathlib.Path(json_output),
                json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            )
    except (benchmark_compare.ComparisonError, OSError) as exc:
        print(f"benchmark comparison failed: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote A/B/C comparison to {output}")
    if json_output:
        print(f"Wrote machine-readable comparison to {json_output}")
    return 0
