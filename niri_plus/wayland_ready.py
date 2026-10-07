"""Short-lived Niri session gate that confirms a live Wayland display socket."""

from __future__ import annotations

import argparse
import os
import pathlib
import select
import socket
import struct
import subprocess
import sys
import time


class WaylandNotReady(RuntimeError):
    pass


def _candidate_sockets(runtime_dir: pathlib.Path, display: str | None) -> list[tuple[str, pathlib.Path]]:
    if display:
        supplied = pathlib.Path(display)
        path = supplied if supplied.is_absolute() else runtime_dir / supplied
        candidates = [(display if supplied.is_absolute() else supplied.name, path)]
        candidates.extend((entry.name, entry) for entry in sorted(runtime_dir.glob("wayland-*")) if entry != path)
        return candidates
    return [(entry.name, entry) for entry in sorted(runtime_dir.glob("wayland-*"))]


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise WaylandNotReady("Wayland socket closed during readiness handshake")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _handshake(path: pathlib.Path, timeout: float = 0.75) -> bool:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(str(path))
        # wl_display.sync(new_id=2), object 1, opcode 0. The callback response
        # proves the compositor event loop is accepting Wayland requests.
        connection.sendall(struct.pack("=III", 1, 12 << 16, 2))
        readable, _, _ = select.select([connection], [], [], timeout)
        if not readable:
            return False
        object_id, size_opcode = struct.unpack("=II", _recv_exact(connection, 8))
        size, opcode = size_opcode >> 16, size_opcode & 0xFFFF
        if object_id != 2 or opcode != 0 or size < 12 or size > 4096:
            return False
        _recv_exact(connection, size - 8)
        return True
    except (OSError, WaylandNotReady):
        return False
    finally:
        connection.close()


def wait_for_wayland(runtime_dir: pathlib.Path, display: str | None = None,
                     timeout: float = 20.0, interval: float = 0.05) -> tuple[str, pathlib.Path]:
    if not runtime_dir.is_dir() or runtime_dir.is_symlink():
        raise WaylandNotReady(f"XDG_RUNTIME_DIR is unavailable or unsafe: {runtime_dir}")
    deadline = time.monotonic() + timeout
    while True:
        candidates = _candidate_sockets(runtime_dir, display)
        ready = []
        for name, path in candidates:
            if not path.is_socket():
                continue
            # Bound the whole wait even when the runtime directory contains
            # stale sockets which accept a connection but never answer sync.
            remaining = max(0.0, deadline - time.monotonic())
            if remaining and _handshake(path, timeout=min(0.75, remaining)):
                ready.append((name, path))
        if display:
            requested = pathlib.Path(display)
            expected = requested if requested.is_absolute() else runtime_dir / requested
            selected = next((item for item in ready if item[1] == expected), None)
            if selected:
                return selected
            # Niri may select wayland-1 when an older, unresponsive
            # wayland-0 socket occupies the imported name. Use a fallback
            # only when exactly one other compositor endpoint answers the
            # protocol handshake; ambiguity fails closed.
            fallback = [item for item in ready if item[1] != expected]
            if len(fallback) == 1:
                return fallback[0]
        elif len(ready) == 1:
            return ready[0]
        elif len(ready) > 1:
            raise WaylandNotReady("multiple Wayland displays are ready and systemd has no selected WAYLAND_DISPLAY")
        if time.monotonic() >= deadline:
            raise WaylandNotReady(f"no responsive Wayland display appeared in {runtime_dir} before timeout")
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


def publish_display(display: str) -> None:
    subprocess.run(["systemctl", "--user", "set-environment", f"WAYLAND_DISPLAY={display}"],
                   check=True, stdin=subprocess.DEVNULL)
    updater = shutil_which("dbus-update-activation-environment")
    if updater:
        subprocess.run([updater, "--systemd", "WAYLAND_DISPLAY"], check=True,
                       stdin=subprocess.DEVNULL)


def shutil_which(command: str) -> str | None:
    from shutil import which
    return which(command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", type=float, default=20.0)
    args = parser.parse_args(argv)
    if os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")[0].lower() != "niri":
        print("Wayland readiness helper is only valid in the Niri session", file=sys.stderr)
        return 2
    runtime_dir = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", ""))
    try:
        display, _ = wait_for_wayland(runtime_dir, os.environ.get("WAYLAND_DISPLAY"), args.wait)
        publish_display(display)
    except (OSError, subprocess.SubprocessError, WaylandNotReady) as exc:
        print(f"Niri Wayland readiness failed: {exc}", file=sys.stderr)
        return 1
    print(f"Niri Wayland display is ready: {display}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
