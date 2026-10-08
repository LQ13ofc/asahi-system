"""Frontend for the existing collector and temporary niri-core runtime profile."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time


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
    return runner(
        ["systemctl", "--user", *args],
        text=True,
        capture_output=True,
        check=False,
    )


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
    except OSError:
        return found
    for entry in entries:
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        argv = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        if argv and pathlib.Path(argv[0]).name == "qs":
            found.append({"pid": int(entry.name), "argv": argv})
    return found


def prepare_niri_core(runner=subprocess.run) -> int:
    """Temporarily runtime-mask Quickshell for the current user manager."""
    if os.geteuid() == 0:
        print("REFUSED: niri-core preparation must run as the desktop user, not root.", file=sys.stderr)
        return 2
    marker = runtime_marker()
    if marker.exists():
        print(f"REFUSED: benchmark runtime marker already exists: {marker}", file=sys.stderr)
        return 2
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("XDG_SESSION_DESKTOP") or "").lower()
    if "niri" not in desktop:
        print("REFUSED: prepare niri-core from the active Niri session, then wait 2-3 minutes before measuring.", file=sys.stderr)
        return 2

    before = _state(runner)
    if before["enabled"] in {"masked", "masked-runtime"}:
        print("REFUSED: Quickshell was already masked; benchmark will not take ownership of that state.", file=sys.stderr)
        return 2
    marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "schema_version": 1,
        "unit": QS_UNIT,
        "previous_enable_state": before["enabled"],
        "prepared_at_unix": time.time(),
        "phase": "preparing",
    }
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
    except OSError as exc:
        print(f"REFUSED: cannot create runtime marker: {exc}", file=sys.stderr)
        return 2

    masked = _systemctl(["mask", "--runtime", "--now", QS_UNIT], runner)
    if masked.returncode != 0:
        marker.unlink(missing_ok=True)
        print(f"FAILED: runtime mask failed: {(masked.stderr or masked.stdout).strip()}", file=sys.stderr)
        return 2

    after = _state(runner)
    qs = _qs_processes()
    if after["enabled"] != "masked-runtime" or after["active"] == "active" or qs:
        _systemctl(["unmask", "--runtime", QS_UNIT], runner)
        marker.unlink(missing_ok=True)
        print(
            "FAILED: niri-core preparation did not converge "
            f"(enabled={after['enabled']}, active={after['active']}, qs={qs}).",
            file=sys.stderr,
        )
        return 2

    payload["phase"] = "prepared"
    marker.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    print("niri-core prepared: Quickshell is runtime-masked and stopped for this user manager.")
    print("Wait 2-3 minutes at idle, then run niri+ benchmark --profile niri-core ...")
    return 0


def restore_niri_core(runner=subprocess.run) -> int:
    """Remove only the runtime mask created by prepare_niri_core."""
    if os.geteuid() == 0:
        print("REFUSED: niri-core restore must run as the desktop user, not root.", file=sys.stderr)
        return 2
    marker = runtime_marker()
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSED: no valid benchmark runtime marker: {exc}", file=sys.stderr)
        return 2
    if payload.get("schema_version") != 1 or payload.get("unit") != QS_UNIT:
        print("REFUSED: runtime marker is not owned by this benchmark implementation.", file=sys.stderr)
        return 2

    state = _state(runner)
    if state["enabled"] != "masked-runtime":
        print(
            f"REFUSED: Quickshell is no longer runtime-masked by the expected state ({state['enabled']}).",
            file=sys.stderr,
        )
        return 2

    unmasked = _systemctl(["unmask", "--runtime", QS_UNIT], runner)
    if unmasked.returncode != 0:
        print(f"FAILED: could not remove runtime mask: {(unmasked.stderr or unmasked.stdout).strip()}", file=sys.stderr)
        return 2

    final = _state(runner)
    if final["enabled"] in {"masked", "masked-runtime"}:
        print("FAILED: Quickshell remains masked after restore.", file=sys.stderr)
        return 2

    # Ownership of the temporary mask ends as soon as the runtime unmask succeeds.
    # Do not leave a stale marker if restarting Quickshell subsequently fails.
    marker.unlink(missing_ok=True)
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("XDG_SESSION_DESKTOP") or "").lower()
    if "niri" in desktop:
        started = _systemctl(["start", QS_UNIT], runner)
        if started.returncode != 0:
            print(
                "FAILED: the temporary mask was removed, but Quickshell could not be restarted; "
                f"run systemctl --user start {QS_UNIT}.",
                file=sys.stderr,
            )
            return 2

    print("niri-core restored: temporary runtime mask removed.")
    return 0


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
