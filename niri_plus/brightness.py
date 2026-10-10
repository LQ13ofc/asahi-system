"""Set a backlight through systemd-logind's session-scoped D-Bus API.

The user process never writes sysfs or runs as root. logind applies its own
active-session and PolicyKit checks for SetBrightness.
"""

from __future__ import annotations

import os
import pathlib
import re
import shlex
import subprocess
from collections.abc import Callable

SERVICE = "org.freedesktop.login1"
MANAGER_PATH = "/org/freedesktop/login1"
MANAGER_INTERFACE = "org.freedesktop.login1.Manager"
SESSION_INTERFACE = "org.freedesktop.login1.Session"
SESSION_PATH_PREFIX = "/org/freedesktop/login1/session/"
DEVICE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z", re.ASCII)


class BrightnessError(RuntimeError):
    """A safe brightness request could not be completed."""


def _run_busctl(
    args: list[str], runner: Callable[..., subprocess.CompletedProcess[str]]
) -> str:
    try:
        result = runner(
            ["busctl", "--system", *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except FileNotFoundError as error:
        raise BrightnessError("busctl do systemd não está disponível") from error
    except subprocess.TimeoutExpired as error:
        raise BrightnessError("systemd-logind não respondeu a tempo") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip()
        raise BrightnessError(detail or "systemd-logind recusou a operação") from error
    return result.stdout.strip()


def _session_path(session_id: str, runner: Callable[..., subprocess.CompletedProcess[str]]) -> str:
    if not session_id or len(session_id) > 128 or any(ord(char) < 32 for char in session_id):
        raise BrightnessError("XDG_SESSION_ID não identifica uma sessão logind")

    output = _run_busctl(
        ["call", SERVICE, MANAGER_PATH, MANAGER_INTERFACE, "ListSessions"], runner
    )
    try:
        tokens = shlex.split(output)
        if len(tokens) < 2 or tokens[0] != "a(susso)":
            raise ValueError("assinatura inesperada")
        count = int(tokens[1])
        values = tokens[2:]
        if count < 0 or len(values) != count * 5:
            raise ValueError("quantidade inesperada")
        for offset in range(0, len(values), 5):
            found_id, _uid, _user, _seat, path = values[offset : offset + 5]
            if found_id == session_id:
                if not path.startswith(SESSION_PATH_PREFIX):
                    raise ValueError("caminho de sessão inválido")
                suffix = path[len(SESSION_PATH_PREFIX) :]
                if not suffix or any(not (char.isascii() and (char.isalnum() or char == "_")) for char in suffix):
                    raise ValueError("caminho de sessão inválido")
                return path
    except (ValueError, IndexError) as error:
        raise BrightnessError("resposta inválida ao listar sessões logind") from error
    raise BrightnessError("sessão gráfica atual não foi encontrada no systemd-logind")


def _backlight(root: pathlib.Path, device: str | None) -> tuple[str, int]:
    try:
        entries = sorted(path for path in root.iterdir() if path.is_dir())
    except OSError as error:
        raise BrightnessError("dispositivo de brilho indisponível") from error

    if device is not None:
        if not DEVICE_NAME.fullmatch(device):
            raise BrightnessError("nome de dispositivo inválido")
        entries = [path for path in entries if path.name == device]
    elif len(entries) != 1:
        message = "nenhum dispositivo de brilho disponível" if not entries else "selecione um dispositivo de brilho"
        raise BrightnessError(message)

    if len(entries) != 1:
        raise BrightnessError("dispositivo de brilho indisponível")
    selected = entries[0]
    if not DEVICE_NAME.fullmatch(selected.name):
        raise BrightnessError("nome de dispositivo inválido")
    try:
        maximum = int((selected / "max_brightness").read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError) as error:
        raise BrightnessError("limite de brilho indisponível") from error
    if maximum <= 0:
        raise BrightnessError("limite de brilho inválido")
    return selected.name, maximum


def set_percent(
    percent: int,
    device: str | None = None,
    *,
    session_id: str | None = None,
    sysfs_root: pathlib.Path = pathlib.Path("/sys/class/backlight"),
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[str, int]:
    """Set display brightness and return (device, absolute driver level)."""

    if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
        raise BrightnessError("brilho deve ser um inteiro entre 0 e 100")
    current_session = session_id if session_id is not None else os.environ.get("XDG_SESSION_ID", "")
    device_name, maximum = _backlight(sysfs_root, device)
    path = _session_path(current_session, runner)

    active = _run_busctl(
        ["get-property", SERVICE, path, SESSION_INTERFACE, "Active"], runner
    )
    if shlex.split(active) != ["b", "true"]:
        raise BrightnessError("brilho só pode ser alterado pela sessão gráfica ativa")

    level = round(maximum * percent / 100)
    _run_busctl(
        [
            "call",
            SERVICE,
            path,
            SESSION_INTERFACE,
            "SetBrightness",
            "ssu",
            "backlight",
            device_name,
            str(level),
        ],
        runner,
    )
    return device_name, level
