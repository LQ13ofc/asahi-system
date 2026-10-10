"""Filesystem transaction for one Niri+ CLI + session installation attempt."""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
import sys
import tempfile
import threading
import fcntl
from contextlib import contextmanager
from collections.abc import Iterable


class TransactionError(RuntimeError):
    pass


_OPERATION_GUARD = threading.RLock()
_OPERATION_LOCAL = threading.local()


@contextmanager
def operation_lock(root: pathlib.Path = pathlib.Path("/")):
    """Serialize host mutations and reject another process already installing."""
    with _OPERATION_GUARD:
        depth = getattr(_OPERATION_LOCAL, "depth", 0)
        if depth == 0:
            lock_path = pathlib.Path("/run/niri-plus-operation.lock")
            if root != pathlib.Path("/"):
                lock_path = root / lock_path.as_posix().lstrip("/")
                lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            elif not lock_path.parent.is_dir() or lock_path.parent.is_symlink():
                raise TransactionError("safe system lock directory /run/lock is unavailable")
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(lock_path, flags, 0o600)
                metadata = os.fstat(descriptor)
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                        or stat.S_IMODE(metadata.st_mode) != 0o600
                        or (root == pathlib.Path("/") and metadata.st_uid != 0)):
                    os.close(descriptor)
                    raise TransactionError("Niri+ operation lock is not a protected regular file")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    os.close(descriptor)
                    raise TransactionError("another Niri+ install, rollback or recovery is already running") from exc
            except OSError as exc:
                raise TransactionError(f"cannot acquire Niri+ operation lock: {exc}") from exc
            _OPERATION_LOCAL.descriptor = descriptor
        _OPERATION_LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _OPERATION_LOCAL.depth -= 1
            if _OPERATION_LOCAL.depth == 0:
                descriptor = _OPERATION_LOCAL.descriptor
                del _OPERATION_LOCAL.descriptor
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


def _remove(path: pathlib.Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _copy(source: pathlib.Path, destination: pathlib.Path) -> None:
    if source.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)


def _safe_parent(path: pathlib.Path) -> None:
    current = pathlib.Path("/")
    for component in path.absolute().parts[1:-1]:
        current /= component
        if current.is_symlink():
            raise TransactionError(f"managed destination has a symlinked parent: {current}")


class FilesystemTransaction:
    """Save selected root-managed paths and restore them unless commit succeeds.

    Paths are deliberately explicit; this transaction does not scan or replace
    broad system directories. It can be tested with a temporary root.
    """

    def __init__(self, paths: Iterable[pathlib.Path], *, root: pathlib.Path = pathlib.Path("/")):
        self.root = root
        normalized = sorted({pathlib.Path(p).as_posix() for p in paths})
        # A parent directory snapshot already covers its descendants. Reject
        # overlaps instead of relying on ambiguous restore ordering.
        for index, item in enumerate(normalized):
            if any(other.startswith(item.rstrip("/") + "/") for other in normalized[index + 1:]):
                raise TransactionError(f"overlapping transaction destinations: {item}")
        self.paths = [pathlib.Path(item) for item in normalized]
        self._temp: tempfile.TemporaryDirectory[str] | None = None
        self._snapshot: pathlib.Path | None = None
        self._existed: dict[str, bool] = {}
        self._committed = False
        self._lock_context = None

    def __enter__(self) -> "FilesystemTransaction":
        if self._temp is not None:
            raise TransactionError("transaction cannot be entered more than once")
        self._lock_context = operation_lock(self.root)
        self._lock_context.__enter__()
        try:
            self._temp = tempfile.TemporaryDirectory(prefix="niri-plus-install-transaction-")
            self._snapshot = pathlib.Path(self._temp.name)
            for index, absolute in enumerate(self.paths):
                target = absolute if self.root == pathlib.Path("/") else self.root / absolute.as_posix().lstrip("/")
                _safe_parent(target)
                saved = self._snapshot / str(index)
                exists = target.exists() or target.is_symlink()
                self._existed[absolute.as_posix()] = exists
                if exists:
                    _copy(target, saved)
            return self
        except Exception:
            self._lock_context.__exit__(*sys.exc_info())
            self._lock_context = None
            raise

    def commit(self) -> None:
        if self._snapshot is None:
            raise TransactionError("transaction has not started")
        self._committed = True

    def rollback(self) -> None:
        if self._snapshot is None:
            return
        errors = []
        for index, absolute in reversed(list(enumerate(self.paths))):
            target = absolute if self.root == pathlib.Path("/") else self.root / absolute.as_posix().lstrip("/")
            saved = self._snapshot / str(index)
            try:
                _safe_parent(target)
                _remove(target)
                if self._existed[absolute.as_posix()]:
                    _copy(saved, target)
            except (OSError, TransactionError) as exc:
                errors.append(f"{target}: {exc}")
        if errors:
            raise TransactionError("could not restore all managed paths: " + "; ".join(errors))

    def __exit__(self, exc_type, exc, tb) -> bool:
        restore_error = None
        if exc_type is not None or not self._committed:
            try:
                self.rollback()
            except Exception as rollback_exc:  # preserve the original error too
                restore_error = rollback_exc
        if self._temp is not None:
            self._temp.cleanup()
            self._temp = None
            self._snapshot = None
        if self._lock_context is not None:
            self._lock_context.__exit__(exc_type, exc, tb)
            self._lock_context = None
        if restore_error is not None:
            if exc is not None:
                raise restore_error from exc
            raise restore_error
        return False


def transaction_paths(managed_files: Iterable[str], managed_links: Iterable[str]) -> list[pathlib.Path]:
    paths = [pathlib.Path(path) for path in (*managed_files, *managed_links)]
    paths.extend(pathlib.Path(path) for path in (
        "/usr/local/lib/niri-plus",
        "/usr/local/share/niri-plus",
        "/usr/local/bin/niri+",
        "/usr/local/libexec/niri-plus-recover",
        "/var/lib/niri-plus/bootstrap.json",
        "/var/lib/niri-plus/plugins.json",
        "/var/lib/niri-plus/bootstrap-backups",
        "/var/lib/asahi-system/niri-performance/state.json",
        "/var/lib/asahi-system/niri-performance/backups",
        "/var/lib/asahi-system/niri-performance/rollback-edits",
    ))
    return paths
