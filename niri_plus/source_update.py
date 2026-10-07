"""Trusted source refresh used by ``niri+ install``.

The CLI is bootstrapped once from a local checkout. Subsequent installs refresh
that checkout from the canonical ``main`` branch, update pinned submodules,
re-bootstrap the CLI atomically, then re-exec the freshly installed command.
"""

from __future__ import annotations

import json
import os
import pathlib
import pwd
import subprocess
import sys
from typing import Callable

BOOTSTRAP_STATE = pathlib.Path("/var/lib/niri-plus/bootstrap.json")
EXPECTED_REPOSITORY = "https://github.com/LQ13ofc/asahi-system.git"
EXPECTED_BRANCH = "main"

Runner = Callable[..., subprocess.CompletedProcess[str]]


class SourceUpdateError(RuntimeError):
    pass


def _normalize_repository(value: str) -> str:
    return value.strip().rstrip("/").removesuffix(".git").lower()


def discover_source_root(state_path: pathlib.Path = BOOTSTRAP_STATE) -> pathlib.Path:
    explicit = os.environ.get("NIRI_PLUS_SOURCE_DIR")
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SourceUpdateError(
            "Niri+ does not know its source checkout yet; refresh the bootstrap once from the repository."
        ) from exc

    source_root = state.get("source_root")
    if source_root:
        return pathlib.Path(source_root).resolve()

    quickshell_source = state.get("quickshell_source")
    if quickshell_source:
        return pathlib.Path(quickshell_source).resolve().parent.parent

    raise SourceUpdateError(
        "Niri+ does not know its source checkout yet; refresh the bootstrap once from the repository."
    )


def _owner_identity(source_root: pathlib.Path) -> tuple[int, int, dict[str, str]]:
    try:
        st = source_root.stat()
        account = pwd.getpwuid(st.st_uid)
    except (OSError, KeyError) as exc:
        raise SourceUpdateError(f"cannot determine owner of source checkout: {source_root}") from exc
    env = os.environ.copy()
    env.update({"HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name})
    return account.pw_uid, account.pw_gid, env


def _run_as_owner(
    source_root: pathlib.Path,
    args: list[str],
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    uid, gid, env = _owner_identity(source_root)
    try:
        return runner(
            args,
            check=True,
            text=True,
            capture_output=True,
            env=env,
            user=uid,
            group=gid,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise SourceUpdateError(f"command failed: {' '.join(args)}{suffix}") from exc


def refresh_source(
    source_root: pathlib.Path,
    runner: Runner = subprocess.run,
    expected_repository: str = EXPECTED_REPOSITORY,
    expected_branch: str = EXPECTED_BRANCH,
) -> str:
    source_root = source_root.resolve()
    if not (source_root / ".git").exists():
        raise SourceUpdateError(f"source checkout is not a Git repository: {source_root}")

    git = ["git", "-C", str(source_root)]

    remote = _run_as_owner(source_root, [*git, "remote", "get-url", "origin"], runner).stdout.strip()
    if _normalize_repository(remote) != _normalize_repository(expected_repository):
        raise SourceUpdateError(f"unexpected origin for Niri+ source checkout: {remote}")

    branch = _run_as_owner(source_root, [*git, "branch", "--show-current"], runner).stdout.strip()
    if branch != expected_branch:
        raise SourceUpdateError(
            f"Niri+ source checkout must be on {expected_branch}; current branch is {branch or 'detached'}"
        )

    dirty = _run_as_owner(
        source_root, [*git, "status", "--porcelain", "--untracked-files=all"], runner
    ).stdout
    if dirty.strip():
        raise SourceUpdateError("Niri+ source checkout has local changes; refusing automatic pull")

    print(f"Updating Niri+ source ({expected_branch})...")
    _run_as_owner(source_root, [*git, "pull", "--ff-only", "origin", expected_branch], runner)

    head = _run_as_owner(source_root, [*git, "rev-parse", "HEAD"], runner).stdout.strip()
    origin_head = _run_as_owner(
        source_root, [*git, "rev-parse", f"refs/remotes/origin/{expected_branch}"], runner
    ).stdout.strip()
    if head != origin_head:
        raise SourceUpdateError(
            "local branch is not exactly origin/main after pull; refusing to bootstrap unreviewed local commits"
        )

    _run_as_owner(source_root, [*git, "submodule", "sync", "--recursive"], runner)
    _run_as_owner(
        source_root,
        [*git, "submodule", "update", "--init", "--recursive", "--checkout"],
        runner,
    )
    return head


def bootstrap_from_source(source_root: pathlib.Path, runner: Runner = subprocess.run) -> None:
    script = source_root / "scripts/bootstrap-niri-plus"
    if not script.is_file():
        raise SourceUpdateError(f"bootstrap script is missing after update: {script}")
    try:
        runner([sys.executable, str(script), "--apply"], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SourceUpdateError("updated source was pulled, but Niri+ bootstrap failed") from exc


def refresh_and_bootstrap(runner: Runner = subprocess.run) -> str:
    source_root = discover_source_root()
    commit = refresh_source(source_root, runner=runner)
    bootstrap_from_source(source_root, runner=runner)
    return commit


def reexec_install() -> None:
    env = os.environ.copy()
    env["NIRI_PLUS_SELF_UPDATED"] = "1"
    os.execve("/usr/local/bin/niri+", ["niri+", "install"], env)
