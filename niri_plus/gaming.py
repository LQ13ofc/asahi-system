"""Explicit Gamescope-only launcher for the Niri Gaming Mode."""

from __future__ import annotations

import os
import pathlib
import shutil
import socket
import stat
import subprocess
from collections.abc import Mapping, Sequence


class GamingError(RuntimeError):
    pass


def _environment(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def niri_session_active(env: Mapping[str, str] | None = None, *, uid: int | None = None) -> bool:
    """Require a live same-user Niri IPC socket and explicit Wayland session markers."""
    values = _environment(env)
    desktops = set()
    for key in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP"):
        desktops.update(part.strip().casefold() for part in values.get(key, "").split(":"))
    socket_path = values.get("NIRI_SOCKET", "")
    if not socket_path or not values.get("WAYLAND_DISPLAY") or "niri" not in desktops:
        return False
    probe: socket.socket | None = None
    try:
        details = pathlib.Path(socket_path).lstat()
        if not stat.S_ISSOCK(details.st_mode) or details.st_uid != (os.getuid() if uid is None else uid):
            return False
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        probe.connect(socket_path)
    except OSError:
        return False
    finally:
        if probe is not None:
            probe.close()
    return True


def _resolve_command(command: str, path: str | None) -> str | None:
    candidate = pathlib.Path(command)
    if candidate.is_absolute() or os.sep in command or (os.altsep and os.altsep in command):
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return str(resolved)
        except OSError:
            return None
        return None
    return shutil.which(command, path=path)


def inspect(env: Mapping[str, str] | None = None) -> dict[str, str]:
    values = _environment(env)
    gamescope = shutil.which("gamescope", path=values.get("PATH"))
    steam = shutil.which("steam", path=values.get("PATH"))
    active = niri_session_active(values)
    if active and gamescope is not None:
        mode_state = "AVAILABLE_UNVERIFIED"
    elif gamescope is not None:
        mode_state = "NOT_ACTIVE"
    else:
        mode_state = "UNAVAILABLE"
    return {
        "niri_session": "OK" if active else "NOT_ACTIVE",
        "gamescope": gamescope or "UNAVAILABLE",
        "steam": steam or "UNAVAILABLE",
        "gaming_mode": mode_state,
    }


def render_status(env: Mapping[str, str] | None = None) -> str:
    state = inspect(env)
    return "\n".join((
        "Niri+ Gaming Mode (Gamescope only)",
        f"Niri session  {state['niri_session']}",
        f"Gamescope     {state['gamescope']}",
        f"Steam         {state['steam']}",
        f"Gaming Mode   {state['gaming_mode']}",
        "Runtime compatibility  M1_REQUIRED",
        "Fallback      disabled",
    ))


def normalize_command(command: Sequence[str]) -> list[str]:
    result = list(command)
    if result and result[0] == "--":
        result.pop(0)
    if not result or not result[0]:
        raise GamingError("provide a game command after `niri+ gaming run --`")
    return result


def launch(command: Sequence[str], *, env: Mapping[str, str] | None = None,
           runner=None) -> int:
    """Run one explicitly requested command under Gamescope, never directly on Niri."""
    values = _environment(env)
    argv = normalize_command(command)
    if os.geteuid() == 0:
        raise GamingError("Gaming Mode must run as the logged-in user, never as root")
    if not niri_session_active(values):
        raise GamingError("Gaming Mode requires an active Niri Wayland session; no command was started")

    gamescope = shutil.which("gamescope", path=values.get("PATH"))
    if gamescope is None:
        raise GamingError("Gamescope is unavailable; no fallback was started. Normal Niri use is unchanged")
    target = _resolve_command(argv[0], values.get("PATH"))
    if target is None:
        raise GamingError(f"game command not found: {argv[0]}")

    run_process = runner or subprocess.run
    try:
        result = run_process([gamescope, "--", target, *argv[1:]], env=dict(values), check=False)
    except OSError as error:
        raise GamingError(f"Gamescope could not start: {error}; no direct-Niri fallback was attempted") from error
    return int(result.returncode)
