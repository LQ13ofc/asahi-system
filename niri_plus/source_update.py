"""Trusted, owner-scoped source refresh used by ``sudo niri+ install``.

Git runs as the checkout owner. The source must be the canonical ``main``
checkout, clean before pull, and exactly equal to cached ``origin/main`` after
pull. Status/doctor use :func:`inspect_source` and never fetch or mutate.
"""

from __future__ import annotations

import json
import os
import pathlib
import pwd
import re
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
    value = value.strip().lower()
    value = re.sub(r"^git@github\.com:", "https://github.com/", value)
    value = re.sub(r"^ssh://git@github\.com/", "https://github.com/", value)
    return value.rstrip("/").removesuffix(".git")


def discover_source_root(state_path: pathlib.Path = BOOTSTRAP_STATE) -> pathlib.Path:
    explicit = os.environ.get("NIRI_PLUS_SOURCE_DIR")
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SourceUpdateError(
            "Niri+ does not know its source checkout yet; run the one-time bootstrap from the repository."
        ) from exc

    source_root = state.get("source_root")
    if source_root:
        return pathlib.Path(source_root).resolve()

    quickshell_source = state.get("quickshell_source")
    if quickshell_source:
        return pathlib.Path(quickshell_source).resolve().parent.parent

    raise SourceUpdateError(
        "Niri+ does not know its source checkout yet; run the one-time bootstrap from the repository."
    )


def _owner_identity(source_root: pathlib.Path) -> tuple[int, int, list[int], dict[str, str]]:
    try:
        st = source_root.stat()
        account = pwd.getpwuid(st.st_uid)
        groups = os.getgrouplist(account.pw_name, account.pw_gid)
    except (OSError, KeyError) as exc:
        raise SourceUpdateError(f"cannot determine owner of source checkout: {source_root}") from exc
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("GIT_"):
            env.pop(key, None)
    env.update({
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
    })
    return account.pw_uid, account.pw_gid, groups, env


def _run_as_owner(
    source_root: pathlib.Path,
    args: list[str],
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    uid, gid, groups, env = _owner_identity(source_root)
    try:
        options = {"user": uid, "group": gid, "extra_groups": groups} if os.geteuid() != uid else {}
        return runner(args, check=True, text=True, capture_output=True, env=env, **options)
    except (OSError, subprocess.SubprocessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or "").strip()
        if "Authentication failed" in detail or "terminal prompts disabled" in detail:
            detail = "GitHub authentication is unavailable to the checkout owner; configure Git access for that user and retry."
        suffix = f": {detail}" if detail else ""
        raise SourceUpdateError(f"command failed: {' '.join(args)}{suffix}") from exc


def refresh_source(
    source_root: pathlib.Path,
    runner: Runner = subprocess.run,
    expected_repository: str = EXPECTED_REPOSITORY,
    expected_branch: str = EXPECTED_BRANCH,
) -> str:
    source_root = source_root.resolve()
    git_marker = source_root / ".git"
    if not git_marker.exists():
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
    if not head or head != origin_head:
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
        raise SourceUpdateError("updated source was pulled, but Niri+ bootstrap failed; retry after reviewing its state") from exc


def refresh_and_bootstrap(runner: Runner = subprocess.run) -> str:
    source_root = discover_source_root()
    commit = refresh_source(source_root, runner=runner)
    bootstrap_from_source(source_root, runner=runner)
    return commit


def reexec_install() -> None:
    env = os.environ.copy()
    env["NIRI_PLUS_SELF_UPDATED"] = "1"
    try:
        os.execve("/usr/local/bin/niri+", ["niri+", "install"], env)
    except OSError as exc:
        raise SourceUpdateError(f"updated CLI could not be restarted at /usr/local/bin/niri+: {exc}") from exc
    raise SourceUpdateError("re-exec unexpectedly returned")


def inspect_source(
    root: pathlib.Path = pathlib.Path("/"),
    runner: Runner = subprocess.run,
    state_path: pathlib.Path | None = None,
    installed_version: str | None = None,
) -> dict[str, str]:
    """Read-only cached source/update health; this function never runs fetch."""
    if root != pathlib.Path("/"):
        return {"status": "UNAVAILABLE", "source_root": "simulated host", "branch": "UNAVAILABLE",
                "origin": "UNAVAILABLE", "head": "UNAVAILABLE", "upstream_head": "UNAVAILABLE",
                "dirty": "UNAVAILABLE", "version": "UNAVAILABLE"}
    path = state_path or BOOTSTRAP_STATE
    try:
        if path.is_symlink():
            raise ValueError("bootstrap state is a symlink")
        state = json.loads(path.read_text(encoding="utf-8"))
        source_root = pathlib.Path(state["source_root"]).resolve()
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return {"status": "NOT_CONFIGURED", "source_root": "unavailable", "branch": "unavailable",
                "origin": "unavailable", "head": "unavailable", "upstream_head": "unavailable",
                "dirty": "unavailable", "version": "unavailable"}

    if not (source_root / ".git").exists():
        return {"status": "WARNING", "source_root": str(source_root), "branch": "unavailable",
                "origin": "unavailable", "head": "unavailable", "upstream_head": "unavailable",
                "dirty": "unavailable", "version": "unavailable"}

    def git(*args: str) -> str | None:
        command = ["git", "-c", f"safe.directory={source_root}", "-C", str(source_root), *args]
        try:
            result = runner(command, check=False, text=True, capture_output=True, timeout=4)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode:
            return None
        return (result.stdout or "").strip()

    origin = git("remote", "get-url", "origin")
    branch = git("branch", "--show-current")
    dirty_output = git("status", "--porcelain", "--untracked-files=all")
    head = git("rev-parse", "HEAD")
    upstream_head = git("rev-parse", "refs/remotes/origin/main")
    try:
        source_version = (source_root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        source_version = "unavailable"

    checks = [
        bool(origin) and _normalize_repository(origin) == _normalize_repository(EXPECTED_REPOSITORY),
        branch == EXPECTED_BRANCH,
        dirty_output == "",
        bool(head),
        bool(upstream_head) and head == upstream_head,
        source_version != "unavailable",
        installed_version is None or source_version == installed_version,
    ]
    return {
        "status": "OK" if all(checks) else "WARNING",
        "source_root": str(source_root),
        "branch": branch or "detached/unavailable",
        "origin": origin or "unavailable",
        "head": head or "unavailable",
        "upstream_head": upstream_head or "unavailable (no cached origin/main)",
        "dirty": "clean" if dirty_output == "" else "WARNING" if dirty_output else "UNAVAILABLE",
        "version": source_version,
    }
