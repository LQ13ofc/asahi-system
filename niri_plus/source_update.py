"""Fetch reviewed Git objects as the checkout owner and materialize a closed snapshot.

No code from the mutable checkout is executed as root. The only source inputs
used after resolution are Git objects whose hashes are checked against the
resolved commits; the snapshot itself is created in a root-owned temporary
directory and is removed by :func:`resolved_snapshot` on every exit path.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import pathlib
import pwd
import re
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator
from urllib.parse import urlparse

BOOTSTRAP_STATE = pathlib.Path("/var/lib/niri-plus/bootstrap.json")
EXPECTED_REPOSITORY = "https://github.com/LQ13ofc/asahi-system.git"
EXPECTED_QUICKSHELL_REPOSITORY = "https://github.com/LQ13ofc/quickshell-.git"
EXPECTED_BRANCH = "main"
RELEASE_CANDIDATE_PULL_REQUEST = 21
RELEASE_CANDIDATE_QUICKSHELL_COMMIT = "b440538d342822eac6cb046f0c0dd5e96212d6f2"
RELEASE_CANDIDATE_REF = f"refs/pull/{RELEASE_CANDIDATE_PULL_REQUEST}/head"
QUICKSHELL_PATH = "external/quickshell"
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024

Runner = Callable[..., subprocess.CompletedProcess]


class SourceUpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    root: pathlib.Path
    manifest: pathlib.Path
    system_commit: str
    quickshell_commit: str
    include_quickshell: bool = True
    expected_repository: str = EXPECTED_REPOSITORY
    expected_quickshell_repository: str = EXPECTED_QUICKSHELL_REPOSITORY
    allow_test_file_sources: bool = False


def _normalize_repository(value: str, *, allow_test_file: bool = False) -> str:
    """Normalize only GitHub HTTPS/SSH spellings for the two approved repos."""
    value = value.strip()
    if allow_test_file and value.startswith("file://"):
        return value.rstrip("/")
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.removeprefix("git@github.com:")
    elif value.startswith("ssh://git@github.com/"):
        value = "https://github.com/" + value.removeprefix("ssh://git@github.com/")
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != "github.com":
        return ""
    path = parsed.path.lstrip("/").rstrip("/").removesuffix(".git").lower()
    if parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
        return ""
    return "https://github.com/" + path


def _owner_identity(source_root: pathlib.Path) -> tuple[int, int, list[int], str, dict[str, str]]:
    try:
        st = source_root.stat()
        account = pwd.getpwuid(st.st_uid)
        groups = os.getgrouplist(account.pw_name, account.pw_gid)
    except (OSError, KeyError) as exc:
        raise SourceUpdateError(f"cannot determine owner of source checkout: {source_root}") from exc
    env = os.environ.copy()
    # Remove Git repository/control overrides inherited through sudo, but keep
    # owner-controlled global config (including its credential helper). Network
    # auth must be non-interactive even when SSH could use the controlling TTY.
    global_config = env.get("GIT_CONFIG_GLOBAL")
    # The command override itself can be an arbitrary wrapper that opens a
    # prompt. Preserve SSH config and agent state, but force OpenSSH batch mode.
    for key in tuple(env):
        if key.startswith("GIT_") and key not in {"GIT_CONFIG_GLOBAL", "GIT_SSH_COMMAND"}:
            env.pop(key, None)
    if global_config:
        try:
            if pathlib.Path(global_config).expanduser().stat().st_uid == st.st_uid:
                env["GIT_CONFIG_GLOBAL"] = str(pathlib.Path(global_config).expanduser())
            else:
                env.pop("GIT_CONFIG_GLOBAL", None)
        except OSError:
            env.pop("GIT_CONFIG_GLOBAL", None)
    env.update({
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GCM_INTERACTIVE": "never",
        "GCM_MODAL_PROMPT": "0",
    })
    env.pop("GIT_ASKPASS", None)
    env.pop("SSH_ASKPASS", None)
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return account.pw_uid, account.pw_gid, groups, account.pw_dir, env


def _run_as_owner(
    source_root: pathlib.Path,
    args: list[str],
    runner: Runner = subprocess.run,
    *,
    input_bytes: bytes | None = None,
    git_config: list[tuple[str, str]] | None = None,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    uid, gid, groups, _, env = _owner_identity(source_root)
    try:
        options = {"user": uid, "group": gid, "extra_groups": groups} if os.geteuid() != uid else {}
        command = list(args)
        if command and pathlib.Path(command[0]).name == "git":
            command[1:1] = ["--no-optional-locks"]
            configurations = [("credential.interactive", "false"), *(git_config or [])]
            env["GIT_CONFIG_COUNT"] = str(len(configurations))
            for index, (key, value) in enumerate(configurations):
                env[f"GIT_CONFIG_KEY_{index}"] = key
                env[f"GIT_CONFIG_VALUE_{index}"] = value
        kwargs = {
            "check": check,
            "capture_output": True,
            "env": env,
            "stdin": subprocess.DEVNULL if input_bytes is None else None,
            "input": input_bytes,
            **options,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        # Do not pass text=True: snapshot object data is binary.
        result = runner(command, **kwargs)
        return result
    except (OSError, subprocess.SubprocessError) as exc:
        detail = b""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or b"")
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        detail = str(detail).strip()
        if any(text in detail.lower() for text in (
            "authentication failed", "terminal prompts disabled", "could not read username",
            "could not read password", "repository not found", "permission denied (publickey)",
        )):
            detail = "GitHub authentication is unavailable to the checkout owner; Git was non-interactive and no changes were applied."
        suffix = f": {detail}" if detail else ""
        raise SourceUpdateError(f"Git command failed: {' '.join(command if 'command' in locals() else args)}{suffix}") from exc


def _git(source_root: pathlib.Path, *args: str, runner: Runner = subprocess.run,
         check: bool = True, timeout: float | None = 15) -> subprocess.CompletedProcess:
    return _run_as_owner(
        source_root,
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         "--no-pager", "-C", str(source_root), *args],
        runner,
        check=check,
        timeout=timeout,
    )


def _stdout(result: subprocess.CompletedProcess) -> str:
    value = result.stdout or b""
    return value.decode("utf-8", errors="replace").strip() if isinstance(value, bytes) else value.strip()


def _configured_repository(source_root: pathlib.Path, runner: Runner,
                           expected_repository: str = EXPECTED_REPOSITORY,
                           *, allow_test_file: bool = False) -> str:
    configured = _stdout(_git(source_root, "config", "--local", "--get-all", "remote.origin.url", runner=runner, check=False))
    values = [line for line in configured.splitlines() if line]
    if (len(values) != 1
            or _normalize_repository(values[0], allow_test_file=allow_test_file)
            != _normalize_repository(expected_repository, allow_test_file=allow_test_file)):
        raise SourceUpdateError(f"unexpected configured origin for Niri+ source checkout: {configured or 'missing'}")
    effective = _stdout(_git(source_root, "remote", "get-url", "--all", "origin", runner=runner))
    urls = [line for line in effective.splitlines() if line]
    if (len(urls) != 1
            or _normalize_repository(urls[0], allow_test_file=allow_test_file)
            != _normalize_repository(expected_repository, allow_test_file=allow_test_file)):
        raise SourceUpdateError(f"origin URL rewrite does not resolve to the approved Niri+ repository: {effective or 'missing'}")
    return urls[0]


def _local_credential_helpers(source_root: pathlib.Path, repository: str,
                              runner: Runner) -> list[str]:
    result = _git(source_root, "config", "--local", "--get-urlmatch", "credential.helper", repository,
                  runner=runner, check=False)
    output = result.stdout or b""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return [line for line in output.splitlines() if line]


def _resolve_effective_url(source_root: pathlib.Path, git_dir: pathlib.Path, value: str,
                           expected_repository: str, runner: Runner,
                           *, allow_test_file: bool = False) -> str:
    current = value
    for _ in range(5):
        expanded = _stdout(_run_as_owner(
            source_root,
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             f"--git-dir={git_dir}", "ls-remote", "--get-url", current],
            runner,
            timeout=15,
        ))
        if (_normalize_repository(expanded, allow_test_file=allow_test_file)
                != _normalize_repository(expected_repository, allow_test_file=allow_test_file)):
            raise SourceUpdateError(f"Git URL rewrite leaves the approved repository: {expanded}")
        if expanded == current:
            return current
        current = expanded
    raise SourceUpdateError("Git URL rewrite did not reach a stable repository URL")


def _check_source_checkout(source_root: pathlib.Path, runner: Runner,
                           expected_repository: str = EXPECTED_REPOSITORY,
                           *, allow_test_file: bool = False) -> str:
    if not (source_root / ".git").exists():
        raise SourceUpdateError(f"source checkout is not a Git repository: {source_root}")
    _configured_repository(source_root, runner, expected_repository, allow_test_file=allow_test_file)
    branch = _stdout(_git(source_root, "branch", "--show-current", runner=runner))
    if branch != EXPECTED_BRANCH:
        raise SourceUpdateError(f"Niri+ source checkout must be on main; current branch is {branch or 'detached'}")
    dirty = _stdout(_git(source_root, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=all", runner=runner))
    if dirty:
        raise SourceUpdateError("Niri+ source checkout has local changes; refusing install so local work is preserved")
    return _configured_repository(source_root, runner, expected_repository, allow_test_file=allow_test_file)


def _check_source_repository(source_root: pathlib.Path, runner: Runner,
                             expected_repository: str = EXPECTED_REPOSITORY,
                             *, allow_test_file: bool = False) -> str:
    """Validate the configured repository without trusting its worktree/ref."""
    if not (source_root / ".git").exists():
        raise SourceUpdateError(f"source checkout is not a Git repository: {source_root}")
    return _configured_repository(source_root, runner, expected_repository, allow_test_file=allow_test_file)


def _check_initialized_quickshell(source_root: pathlib.Path, runner: Runner,
                                 expected_repository: str, *, allow_test_file: bool = False) -> None:
    checkout = source_root / QUICKSHELL_PATH
    if not (checkout / ".git").exists():
        # An uninitialized submodule is valid: the exact commit is fetched into
        # a separate temporary object database below.
        return
    remote = _configured_repository(checkout, runner, expected_repository, allow_test_file=allow_test_file)
    dirty = _stdout(_git(checkout, "status", "--porcelain", "--untracked-files=all", runner=runner))
    if dirty:
        raise SourceUpdateError("Quickshell submodule has local changes; refusing install so visual work is preserved")
    head = _stdout(_git(checkout, "rev-parse", "HEAD", runner=runner))
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise SourceUpdateError("Quickshell submodule HEAD is not a full commit")


def _git_hash(kind: bytes, data: bytes) -> str:
    return hashlib.sha1(kind + b" " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _cat_objects(source_root: pathlib.Path, git_dir: pathlib.Path, oids: list[str],
                 runner: Runner) -> dict[str, tuple[str, bytes]]:
    if not oids:
        return {}
    request = b"".join(oid.encode("ascii") + b"\n" for oid in oids)
    result = _run_as_owner(
        source_root,
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         f"--git-dir={git_dir}", "cat-file", "--batch"],
        runner,
        input_bytes=request,
        timeout=30,
    )
    output = result.stdout or b""
    found: dict[str, tuple[str, bytes]] = {}
    offset = 0
    for wanted in oids:
        end = output.find(b"\n", offset)
        if end < 0:
            raise SourceUpdateError("Git returned a truncated object batch")
        header = output[offset:end].split()
        offset = end + 1
        if len(header) != 3 or header[1] == b"missing":
            raise SourceUpdateError(f"required Git object is unavailable: {wanted}")
        oid, kind = header[0].decode("ascii"), header[1].decode("ascii")
        size = int(header[2])
        body = output[offset:offset + size]
        if len(body) != size or output[offset + size:offset + size + 1] != b"\n":
            raise SourceUpdateError(f"Git returned a truncated object: {wanted}")
        offset += size + 1
        if oid != wanted or _git_hash(kind.encode("ascii"), body) != wanted:
            raise SourceUpdateError(f"Git object failed content hash verification: {wanted}")
        found[wanted] = (kind, body)
    return found


def _parse_tree(data: bytes) -> list[tuple[int, str, str]]:
    entries = []
    offset = 0
    while offset < len(data):
        mode_end = data.find(b" ", offset)
        name_end = data.find(b"\0", mode_end + 1)
        if mode_end < 0 or name_end < 0 or name_end + 21 > len(data):
            raise SourceUpdateError("invalid Git tree object")
        mode = int(data[offset:mode_end], 8)
        try:
            name = data[mode_end + 1:name_end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceUpdateError("Git tree contains a non-UTF-8 path; refusing privileged snapshot") from exc
        oid = data[name_end + 1:name_end + 21].hex()
        if name in {"", ".", ".."} or "/" in name:
            raise SourceUpdateError("Git tree contains an unsafe path component")
        entries.append((mode, name, oid))
        offset = name_end + 21
    return entries


def _read_git_tree(source_root: pathlib.Path, git_dir: pathlib.Path, commit: str,
                   runner: Runner) -> tuple[dict[str, tuple[int, str, bytes]], list[tuple[str, str]]]:
    commit_object = _cat_objects(source_root, git_dir, [commit], runner)[commit]
    if commit_object[0] != "commit":
        raise SourceUpdateError("resolved ref is not a Git commit")
    tree_line = next((line for line in commit_object[1].splitlines() if line.startswith(b"tree ")), None)
    if not tree_line:
        raise SourceUpdateError("resolved commit has no root tree")
    root_tree = tree_line.split()[1].decode("ascii")
    pending = [("", root_tree)]
    files: dict[str, tuple[int, str, bytes]] = {}
    gitlinks: list[tuple[str, str]] = []
    total = 0
    seen_oids: set[str] = set()
    while pending:
        ids = [oid for _, oid in pending]
        objects = _cat_objects(source_root, git_dir, ids, runner)
        current = pending
        pending = []
        for prefix, tree_oid in current:
            kind, data = objects[tree_oid]
            if kind != "tree":
                raise SourceUpdateError("Git tree entry points to a non-tree object")
            for mode, name, oid in _parse_tree(data):
                path = f"{prefix}/{name}" if prefix else name
                if path in files or path in {item[0] for item in gitlinks}:
                    raise SourceUpdateError(f"duplicate Git tree path: {path}")
                if mode == 0o040000:
                    pending.append((path, oid))
                elif mode == 0o160000:
                    gitlinks.append((path, oid))
                elif mode in {0o100644, 0o100755, 0o120000}:
                    seen_oids.add(oid)
                    files[path] = (mode, oid, b"")
                else:
                    raise SourceUpdateError(f"unsupported Git file mode {mode:o} at {path}")
        if len(files) > 20000 or sum(len(path) for path in files) > 4 * 1024 * 1024:
            raise SourceUpdateError("Git snapshot has an unreasonable number of paths")
    blobs = _cat_objects(source_root, git_dir, sorted(seen_oids), runner)
    for path, (mode, oid, _) in tuple(files.items()):
        kind, data = blobs[oid]
        if kind != "blob":
            raise SourceUpdateError(f"Git file does not reference a blob: {path}")
        total += len(data)
        if total > MAX_SNAPSHOT_BYTES:
            raise SourceUpdateError("Git snapshot exceeds the configured size limit")
        files[path] = (mode, oid, data)
    return files, gitlinks


def _materialize(root: pathlib.Path, files: dict[str, tuple[int, str, bytes]]) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for rel, (mode, oid, data) in sorted(files.items()):
        path = pathlib.PurePosixPath(rel)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise SourceUpdateError(f"unsafe snapshot path: {rel}")
        destination = root.joinpath(*path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if mode == 0o120000:
            target = data.decode("utf-8", errors="strict")
            if pathlib.PurePosixPath(target).is_absolute():
                raise SourceUpdateError(f"absolute symlink in Git snapshot: {rel}")
            resolved = (destination.parent / target).resolve()
            if resolved != root.resolve() and root.resolve() not in resolved.parents:
                raise SourceUpdateError(f"escaping symlink in Git snapshot: {rel}")
            destination.symlink_to(target)
        else:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(destination, 0o755 if mode == 0o100755 else 0o644)


def _lock_and_gitmodules(snapshot_root: pathlib.Path, gitlinks: list[tuple[str, str]],
                         qs_expected_repository: str, *, allow_test_file: bool = False) -> tuple[str, str]:
    lock_path = snapshot_root / "integration/quickshell.lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        parser = configparser.ConfigParser()
        parser.read(snapshot_root / ".gitmodules", encoding="utf-8")
        gitmodules_path = parser.get('submodule "external/quickshell"', "path")
        gitmodules_url = parser.get('submodule "external/quickshell"', "url")
    except (OSError, ValueError, configparser.Error) as exc:
        raise SourceUpdateError(f"invalid Quickshell gitlink/lock metadata in resolved asahi-system commit: {exc}") from exc
    if lock.get("schema_version") != 1 or lock.get("checkout") != QUICKSHELL_PATH:
        raise SourceUpdateError("Quickshell lock has an unknown schema or checkout path")
    if gitmodules_path != QUICKSHELL_PATH:
        raise SourceUpdateError(".gitmodules Quickshell path differs from the supported checkout path")
    expected_repo = _normalize_repository(qs_expected_repository, allow_test_file=allow_test_file)
    if (not expected_repo or _normalize_repository(str(lock.get("repository", "")), allow_test_file=allow_test_file)
            != expected_repo):
        raise SourceUpdateError("Quickshell lock repository is not the approved repository")
    if _normalize_repository(gitmodules_url, allow_test_file=allow_test_file) != expected_repo:
        raise SourceUpdateError("Quickshell .gitmodules URL differs from the lock file")
    sha = str(lock.get("commit", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SourceUpdateError("Quickshell lock commit is not a full SHA-1 commit")
    matching = [oid for path, oid in gitlinks if path == QUICKSHELL_PATH]
    if len(matching) != 1 or matching[0] != sha or len(gitlinks) != 1:
        raise SourceUpdateError("Quickshell gitlink and lockfile diverge in the resolved asahi-system commit")
    return sha, lock["repository"]


def _snapshot_entries(files: dict[str, tuple[int, str, bytes]]) -> list[dict[str, str | int]]:
    return [{"path": path, "mode": mode, "oid": oid} for path, (mode, oid, _) in sorted(files.items())]


def _write_snapshot_files(destination: pathlib.Path, files: dict[str, tuple[int, str, bytes]]) -> None:
    _materialize(destination, files)


def _fetch_commit(source_root: pathlib.Path, git_dir: pathlib.Path, repository: str,
                  requested_ref: str, runner: Runner, *, private_quickshell: bool = False,
                  credential_helpers: list[str] | None = None) -> str:
    # Resolve a branch or tag through the configured credential helper, then
    # fetch the exact returned object ID. No mutable worktree/ref is trusted.
    try:
        if re.fullmatch(r"[0-9a-f]{40}", requested_ref):
            commit = requested_ref
        else:
            result = _run_as_owner(
                source_root,
                ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                 f"--git-dir={git_dir}", "ls-remote", "--exit-code", "--refs", repository, requested_ref],
                runner,
                git_config=[("credential.helper", helper) for helper in (credential_helpers or [])],
                timeout=30,
            )
            lines = (result.stdout or b"").decode().splitlines()
            matches = [line.split()[0] for line in lines if len(line.split()) == 2 and line.split()[1] == requested_ref]
            if len(matches) != 1 or not re.fullmatch(r"[0-9a-f]{40}", matches[0]):
                raise SourceUpdateError(f"could not resolve exactly one commit for {requested_ref}")
            commit = matches[0]
        _run_as_owner(
            source_root,
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             f"--git-dir={git_dir}", "fetch", "--depth=1", "--no-tags", repository, commit],
            runner,
            git_config=[("credential.helper", helper) for helper in (credential_helpers or [])],
            timeout=90,
        )
        # Validate the immutable commit object before interpreting any tree.
        obj = _cat_objects(source_root, git_dir, [commit], runner)[commit]
        if obj[0] != "commit":
            raise SourceUpdateError(f"resolved object is not a commit: {commit}")
        return commit
    except SourceUpdateError as exc:
        if private_quickshell and any(token in str(exc).lower() for token in (
            "authentication", "repository not found", "could not resolve", "git command failed",
        )):
            raise SourceUpdateError(
                "Quickshell's pinned private commit could not be fetched with the checkout owner's saved credentials. "
                "Git ran without terminal or credential-manager prompts; the installation was not changed."
            ) from exc
        raise


def _create_git_dir(source_root: pathlib.Path, git_dir: pathlib.Path, runner: Runner) -> None:
    # The parent temporary directory is handed to the checkout owner by
    # resolved_snapshot(). Let Git create the bare repository itself as that
    # user. Pre-creating git_dir as root makes Git fail with "File exists" and
    # also gives the owner the wrong permissions on the object store.
    git_dir.parent.mkdir(parents=True, exist_ok=True)
    if git_dir.exists() or git_dir.is_symlink():
        raise SourceUpdateError(f"temporary Git object store already exists: {git_dir}")
    _run_as_owner(source_root, ["git", "init", "--bare", str(git_dir)], runner, timeout=15)


@contextmanager
def resolved_snapshot(source_root: pathlib.Path | None = None,
                      runner: Runner = subprocess.run,
                      expected_repository: str = EXPECTED_REPOSITORY,
                      expected_quickshell_repository: str = EXPECTED_QUICKSHELL_REPOSITORY,
                      *, allow_test_file_sources: bool = False,
                      include_quickshell: bool = True,
                      release_candidate: bool = False,
                      expected_system_commit: str | None = None,
                      known_good_restore: bool = False,
                      expected_quickshell_commit: str | None = None) -> Iterator[Snapshot]:
    """Resolve production main by default, or an explicitly pinned PR #21 RC.

    The ordinary path remains deliberately main-only. The RC mode is opt-in,
    requires a full commit SHA, verifies that PR #21 currently resolves to that
    exact commit, and accepts only the RC's fixed Quickshell gitlink/lock pin.
    """
    source_root = (source_root or discover_source_root()).resolve()
    if release_candidate and known_good_restore:
        raise SourceUpdateError("release-candidate install and known-good restore are mutually exclusive")
    if release_candidate:
        if not re.fullmatch(r"[0-9a-f]{40}", str(expected_system_commit or "")):
            raise SourceUpdateError("release-candidate install requires the full 40-character PR #21 commit SHA")
        if not include_quickshell:
            raise SourceUpdateError("release-candidate install requires the pinned Quickshell integration")
    elif known_good_restore:
        if (not re.fullmatch(r"[0-9a-f]{40}", str(expected_system_commit or ""))
                or not re.fullmatch(r"[0-9a-f]{40}", str(expected_quickshell_commit or ""))
                or not include_quickshell):
            raise SourceUpdateError("known-good restore requires full system and Quickshell commit IDs")
    elif expected_system_commit is not None or expected_quickshell_commit is not None:
        raise SourceUpdateError("a candidate commit is valid only with explicit release-candidate mode")
    if (not allow_test_file_sources
            and (_normalize_repository(expected_repository) != _normalize_repository(EXPECTED_REPOSITORY)
                 or _normalize_repository(expected_quickshell_repository)
                 != _normalize_repository(EXPECTED_QUICKSHELL_REPOSITORY))):
        raise SourceUpdateError("test Git source override is disabled")
    fetch_url = (_check_source_repository if release_candidate or known_good_restore else _check_source_checkout)(
        source_root, runner, expected_repository, allow_test_file=allow_test_file_sources,
    )
    try:
        metadata = source_root.stat()
        account = pwd.getpwuid(metadata.st_uid)
    except (OSError, KeyError) as exc:
        raise SourceUpdateError(f"cannot determine source owner: {source_root}") from exc

    with tempfile.TemporaryDirectory(prefix="niri-plus-install-snapshot-") as root_temp, \
            tempfile.TemporaryDirectory(prefix="niri-plus-owner-git-") as owner_temp:
        root = pathlib.Path(root_temp)
        scratch = pathlib.Path(owner_temp) / "asahi.git"
        # Git network/config/credential commands run as the checkout owner. The
        # temporary object store is not a worktree and is verified object by
        # object before root writes any executable snapshot files.
        os.chown(pathlib.Path(owner_temp), account.pw_uid, account.pw_gid)
        _create_git_dir(source_root, scratch, runner)
        system_commit = _fetch_commit(
            source_root, scratch, fetch_url,
            (RELEASE_CANDIDATE_REF if release_candidate else expected_system_commit
             if known_good_restore else "refs/heads/main"), runner,
            credential_helpers=_local_credential_helpers(source_root, fetch_url, runner),
        )
        if (release_candidate or known_good_restore) and system_commit != expected_system_commit:
            raise SourceUpdateError(
                (f"PR #{RELEASE_CANDIDATE_PULL_REQUEST} head changed: expected {expected_system_commit}, "
                 f"remote currently resolves to {system_commit}; no installation was changed")
                if release_candidate else "known-good system commit could not be fetched exactly"
            )
        system_files, gitlinks = _read_git_tree(source_root, scratch, system_commit, runner)
        # The commit tree is read from the exact object above. Validate its
        # embedded gitlink and lock before making any attempt to fetch QML.
        temp_system = root / "system-tree"
        _write_snapshot_files(temp_system, system_files)
        qs_commit, qs_repo = _lock_and_gitmodules(
            temp_system, gitlinks, expected_quickshell_repository,
            allow_test_file=allow_test_file_sources,
        )
        if release_candidate and qs_commit != RELEASE_CANDIDATE_QUICKSHELL_COMMIT:
            raise SourceUpdateError(
                "PR #21 does not pin the approved Release Candidate Quickshell commit "
                f"{RELEASE_CANDIDATE_QUICKSHELL_COMMIT}"
            )
        if known_good_restore and qs_commit != expected_quickshell_commit:
            raise SourceUpdateError("known-good Quickshell commit differs from the saved installation record")
        fetch_url = _resolve_effective_url(
            source_root, scratch, fetch_url, expected_repository, runner,
            allow_test_file=allow_test_file_sources,
        )
        qs_files = {}
        if include_quickshell:
            _check_initialized_quickshell(
                source_root, runner, expected_quickshell_repository, allow_test_file=allow_test_file_sources,
            )
            qs_fetch_url = _resolve_effective_url(
                source_root, scratch, qs_repo, expected_quickshell_repository, runner,
                allow_test_file=allow_test_file_sources,
            )
            quickshell_objects = pathlib.Path(owner_temp) / "quickshell.git"
            _create_git_dir(source_root, quickshell_objects, runner)
            qshell_helpers = _local_credential_helpers(source_root, qs_fetch_url, runner)
            qshell_checkout = source_root / QUICKSHELL_PATH
            if (qshell_checkout / ".git").exists():
                qshell_helpers.extend(_local_credential_helpers(qshell_checkout, qs_fetch_url, runner))
            quickshell_commit = _fetch_commit(
                source_root, quickshell_objects, qs_fetch_url, qs_commit, runner, private_quickshell=True,
                credential_helpers=list(dict.fromkeys(qshell_helpers)),
            )
            if quickshell_commit != qs_commit:
                raise SourceUpdateError("fetched Quickshell commit differs from the parent gitlink/lock pin")
            qs_files, nested_gitlinks = _read_git_tree(source_root, quickshell_objects, qs_commit, runner)
            if nested_gitlinks:
                raise SourceUpdateError("nested Quickshell submodules are not supported by the locked snapshot format")
        else:
            quickshell_commit = qs_commit

        snapshot_root = root / "source"
        _materialize(snapshot_root, system_files)
        qs_root = snapshot_root / QUICKSHELL_PATH
        if qs_root.exists():
            raise SourceUpdateError("Quickshell gitlink path unexpectedly materialized as a normal directory")
        if include_quickshell:
            _materialize(qs_root, qs_files)
        manifest_data = {
            "schema_version": 1,
            "source_root": str(source_root),
            "system": {"repository": expected_repository,
                       "branch": (RELEASE_CANDIDATE_REF if release_candidate else
                                  f"commit:{system_commit}" if known_good_restore else EXPECTED_BRANCH),
                       "commit": system_commit, "files": _snapshot_entries(system_files)},
            "quickshell": {"repository": qs_repo,
                           "commit": quickshell_commit, "included": include_quickshell,
                           "files": _snapshot_entries(qs_files)},
        }
        if release_candidate:
            manifest_data["channel"] = "release-candidate"
            manifest_data["pull_request"] = RELEASE_CANDIDATE_PULL_REQUEST
            manifest_data["expected_system_commit"] = expected_system_commit
        elif known_good_restore:
            manifest_data["channel"] = "known-good-restore"
            manifest_data["expected_system_commit"] = expected_system_commit
            manifest_data["expected_quickshell_commit"] = expected_quickshell_commit
        canonical = json.dumps(manifest_data, sort_keys=True, separators=(",", ":")).encode()
        manifest_data["snapshot_sha256"] = hashlib.sha256(canonical).hexdigest()
        manifest_path = root / "snapshot.json"
        manifest_path.write_text(json.dumps(manifest_data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.chmod(manifest_path, 0o444)
        validate_snapshot(
            snapshot_root, manifest_path,
            expected_repository=expected_repository,
            expected_quickshell_repository=expected_quickshell_repository,
            allow_test_file_sources=allow_test_file_sources,
        )
        # Switch ownership only after all inputs have been created and verified.
        if os.geteuid() == 0:
            for directory, dirs, files in os.walk(root, topdown=False, followlinks=False):
                for name in files:
                    path = pathlib.Path(directory) / name
                    if not path.is_symlink():
                        os.chown(path, 0, 0)
                        path.chmod(path.stat().st_mode & ~0o222)
                for name in dirs:
                    path = pathlib.Path(directory) / name
                    if not path.is_symlink():
                        os.chown(path, 0, 0)
                        path.chmod(0o555)
            os.chown(root, 0, 0)
            os.chown(snapshot_root, 0, 0)
            os.chown(manifest_path, 0, 0)
            os.chmod(root, 0o555)
        yield Snapshot(snapshot_root, manifest_path, system_commit, quickshell_commit, include_quickshell,
                       expected_repository, expected_quickshell_repository, allow_test_file_sources)


def _safe_snapshot_path(root: pathlib.Path, rel: str) -> pathlib.Path:
    path = pathlib.PurePosixPath(rel)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise SourceUpdateError(f"unsafe snapshot manifest path: {rel}")
    return root.joinpath(*path.parts)


def validate_snapshot(root: pathlib.Path, manifest_path: pathlib.Path, *,
                      expected_repository: str | None = None,
                      expected_quickshell_repository: str | None = None,
                      allow_test_file_sources: bool = False) -> dict:
    expected_repository = expected_repository or EXPECTED_REPOSITORY
    expected_quickshell_repository = expected_quickshell_repository or EXPECTED_QUICKSHELL_REPOSITORY
    # Tests substitute local bare Git remotes by patching these module-level
    # repository constants. Production builds contain only the canonical HTTPS
    # URLs, so a local-file origin cannot pass this identity check there.
    allow_test_file_sources = allow_test_file_sources or (
        expected_repository.startswith("file://")
        and expected_quickshell_repository.startswith("file://")
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceUpdateError("snapshot manifest is missing or invalid") from exc
    supplied_digest = manifest.pop("snapshot_sha256", None)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    if manifest.get("schema_version") != 1 or supplied_digest != hashlib.sha256(canonical).hexdigest():
        raise SourceUpdateError("snapshot manifest integrity check failed")
    system = manifest.get("system")
    quickshell = manifest.get("quickshell")
    if not isinstance(quickshell, dict) or not isinstance(quickshell.get("included", True), bool):
        raise SourceUpdateError("snapshot Quickshell inclusion state is invalid")
    channel = manifest.get("channel", "production")
    local_test_sources = allow_test_file_sources
    local_test_quickshell = allow_test_file_sources
    if (not isinstance(system, dict)
            or _normalize_repository(str(system.get("repository", "")), allow_test_file=local_test_sources)
            != _normalize_repository(expected_repository, allow_test_file=local_test_sources)):
        raise SourceUpdateError("snapshot system repository is not the approved asahi-system repository")
    if channel == "production":
        if system.get("branch") != EXPECTED_BRANCH or any(
            key in manifest for key in ("pull_request", "expected_system_commit")
        ):
            raise SourceUpdateError("production snapshot must originate from approved main")
    elif channel == "release-candidate":
        if (manifest.get("pull_request") != RELEASE_CANDIDATE_PULL_REQUEST
                or system.get("branch") != RELEASE_CANDIDATE_REF
                or manifest.get("expected_system_commit") != system.get("commit")):
            raise SourceUpdateError("release-candidate snapshot identity is incomplete or inconsistent")
    elif channel == "known-good-restore":
        if (system.get("branch") != f"commit:{system.get('commit')}"
                or manifest.get("expected_system_commit") != system.get("commit")
                or manifest.get("expected_quickshell_commit") != manifest.get("quickshell", {}).get("commit")
                or any(key in manifest for key in ("pull_request",))):
            raise SourceUpdateError("known-good restore snapshot identity is incomplete or inconsistent")
    else:
        raise SourceUpdateError("unknown Niri+ snapshot channel")
    expected_paths: set[str] = set()
    include_quickshell = quickshell.get("included", True)
    if not include_quickshell and quickshell.get("files") != []:
        raise SourceUpdateError("Niri-only snapshot unexpectedly contains Quickshell files")
    for section in ("system", "quickshell"):
        if section == "quickshell" and not include_quickshell:
            continue
        data = manifest.get(section)
        if not isinstance(data, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(data.get("commit", ""))):
            raise SourceUpdateError(f"snapshot {section} commit is invalid")
        for entry in data.get("files", []):
            rel = str(entry.get("path", ""))
            prefix = QUICKSHELL_PATH + "/" if section == "quickshell" else ""
            complete = prefix + rel
            path = _safe_snapshot_path(root, complete)
            if complete in expected_paths or path.is_symlink() and entry.get("mode") != 0o120000:
                raise SourceUpdateError(f"snapshot path is duplicated or has unexpected type: {complete}")
            expected_paths.add(complete)
            try:
                if entry["mode"] == 0o120000:
                    data_bytes = os.readlink(path).encode("utf-8")
                else:
                    data_bytes = path.read_bytes()
            except (OSError, KeyError) as exc:
                raise SourceUpdateError(f"snapshot file is unavailable: {complete}") from exc
            if _git_hash(b"blob", data_bytes) != entry.get("oid"):
                raise SourceUpdateError(f"snapshot file content does not match Git object: {complete}")
            mode = 0o120000 if path.is_symlink() else 0o100755 if path.stat().st_mode & 0o111 else 0o100644
            if mode != entry.get("mode"):
                raise SourceUpdateError(f"snapshot file mode does not match Git tree: {complete}")
    actual_paths = set()
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = pathlib.Path(base) / name
            if path.is_symlink() or path.is_file():
                actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != expected_paths:
        raise SourceUpdateError("snapshot contains missing or untracked files")
    lock = json.loads((root / "integration/quickshell.lock.json").read_text(encoding="utf-8"))
    if (lock.get("commit") != manifest["quickshell"]["commit"]
            or lock.get("repository") != manifest["quickshell"]["repository"]):
        raise SourceUpdateError("snapshot Quickshell commit does not match the system lock file")
    if channel == "release-candidate" and manifest["quickshell"]["commit"] != RELEASE_CANDIDATE_QUICKSHELL_COMMIT:
        raise SourceUpdateError("release-candidate snapshot has an unexpected Quickshell pin")
    if (_normalize_repository(str(manifest["quickshell"].get("repository", "")),
                              allow_test_file=local_test_quickshell)
            != _normalize_repository(expected_quickshell_repository,
                                     allow_test_file=local_test_quickshell)):
        raise SourceUpdateError("snapshot Quickshell repository is not the approved fork")
    return {**manifest, "snapshot_sha256": supplied_digest}


def install_from_snapshot(snapshot: Snapshot, runner: Runner = subprocess.run) -> None:
    validate_snapshot(
        snapshot.root, snapshot.manifest,
        expected_repository=snapshot.expected_repository,
        expected_quickshell_repository=snapshot.expected_quickshell_repository,
        allow_test_file_sources=snapshot.allow_test_file_sources,
    )
    # Do not let a caller-preserved PYTHONHOME, startup hook, user site, or
    # search path inject code into the privileged interpreter. The snapshot
    # entrypoint adds its own closed root to sys.path, so run Python isolated
    # and with bytecode writes disabled. Otherwise the first import creates
    # __pycache__ inside the verified snapshot and the child validation sees
    # those files as untracked content.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    env["NIRI_PLUS_DATA_DIR"] = str(snapshot.root)
    command = [sys_executable(), "-I", "-B", str(snapshot.root / "scripts/install-snapshot"),
               "--snapshot-root", str(snapshot.root), "--manifest", str(snapshot.manifest)]
    if not snapshot.include_quickshell:
        command.append("--without-quickshell")
    try:
        runner(command, check=True, env=env, stdin=subprocess.DEVNULL, cwd=str(snapshot.root))
    except (OSError, subprocess.SubprocessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or b"")
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            detail = str(detail).strip()
        raise SourceUpdateError(f"snapshot installation failed; the previous install was restored: {detail or exc}") from exc


def sys_executable() -> str:
    import sys
    return sys.executable


def discover_source_root(state_path: pathlib.Path = BOOTSTRAP_STATE) -> pathlib.Path:
    explicit = os.environ.get("NIRI_PLUS_SOURCE_DIR")
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source_root = state.get("source_root")
        if source_root:
            return pathlib.Path(source_root).resolve()
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    raise SourceUpdateError("Niri+ source checkout is not configured; run the bootstrap entrypoint once.")


def inspect_source(root: pathlib.Path = pathlib.Path("/"), runner: Runner = subprocess.run,
                   state_path: pathlib.Path | None = None,
                   installed_version: str | None = None) -> dict[str, str]:
    """Read only local checkout/cache information; never fetch or alter Git."""
    unavailable = {"status": "NOT_CONFIGURED", "source_root": "unavailable", "branch": "unavailable",
                  "origin": "unavailable", "head": "unavailable", "upstream_head": "unavailable",
                  "dirty": "unavailable", "version": "unavailable"}
    if root != pathlib.Path("/"):
        return {**unavailable, "status": "UNAVAILABLE", "source_root": "simulated host"}
    try:
        path = state_path or BOOTSTRAP_STATE
        if path.is_symlink():
            raise ValueError("bootstrap state symlink")
        state = json.loads(path.read_text(encoding="utf-8"))
        source_root = pathlib.Path(state["source_root"]).resolve()
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return unavailable
    if not (source_root / ".git").exists():
        return {**unavailable, "status": "WARNING", "source_root": str(source_root)}
    try:
        remote = _configured_repository(source_root, runner)
        branch = _stdout(_git(source_root, "branch", "--show-current", runner=runner))
        dirty_output = _stdout(_git(source_root, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=all", runner=runner))
        head = _stdout(_git(source_root, "rev-parse", "HEAD", runner=runner))
        upstream = _stdout(_git(source_root, "rev-parse", "refs/remotes/origin/main", runner=runner, check=False))
        version = (source_root / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, SourceUpdateError):
        return {**unavailable, "status": "WARNING", "source_root": str(source_root)}
    checks = [_normalize_repository(remote) == _normalize_repository(EXPECTED_REPOSITORY), branch == EXPECTED_BRANCH,
              not dirty_output, bool(head), bool(upstream) and head == upstream,
              bool(version), installed_version is None or version == installed_version]
    return {"status": "OK" if all(checks) else "WARNING", "source_root": str(source_root),
            "branch": branch or "detached", "origin": remote, "head": head or "unavailable",
            "upstream_head": upstream or "unavailable (no cached origin/main)",
            "dirty": "clean" if not dirty_output else "WARNING", "version": version or "unavailable"}
