#!/usr/bin/env python3
"""Build an optional, local-only Graphify code graph for Niri+ repositories."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = PROJECT_ROOT / "tools" / "graphify.lock.json"
EXPECTED_REPOSITORIES = {
    "asahi": ("LQ13ofc/asahi-system", "https://github.com/LQ13ofc/asahi-system.git"),
    "quickshell": ("LQ13ofc/quickshell-", "https://github.com/LQ13ofc/quickshell-.git"),
}


def load_lock(path: Path = LOCK_PATH) -> dict[str, str]:
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Graphify lock must be a JSON object: {path}")
    repository = data.get("repository")
    commit = data.get("commit")
    version = data.get("version")
    if repository != "https://github.com/Graphify-Labs/graphify.git":
        raise ValueError("Graphify repository does not match the approved upstream")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Graphify lock must pin a full 40-character commit")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Graphify lock must contain a semantic release version")
    if data.get("mode") != "code-only" or data.get("license") != "Apache-2.0":
        raise ValueError("Graphify lock must preserve the reviewed offline mode and license")
    return {key: str(value) for key, value in data.items()}


def canonical_github_remote(value: str) -> str:
    remote = value.strip()
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote.removeprefix("git@github.com:")
    if remote.endswith(".git"):
        remote = remote[:-4]
    return remote.rstrip("/").lower()


def verify_repo(path: Path, expected_url: str, label: str) -> Path:
    root = path.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"{label} path is not a directory: {root}")
    top = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if Path(top).resolve() != root:
        raise ValueError(f"{label} path must be the repository root: {root}")
    remote = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if canonical_github_remote(remote) != canonical_github_remote(expected_url):
        raise ValueError(f"{label} origin is not the expected repository; check its local remote configuration")
    return root


def resolve_quickshell(explicit: str | None) -> Path:
    configured = explicit or os.environ.get("NIRI_PLUS_QUICKSHELL_DIR")
    candidate = Path(configured).expanduser() if configured else PROJECT_ROOT.parent / "quickshell-"
    return verify_repo(candidate, EXPECTED_REPOSITORIES["quickshell"][1], "quickshell-")


def graphify_command(graphify_bin: str, lock: dict[str, str], target: Path) -> list[str]:
    source = f"git+{lock['repository']}@{lock['commit']}"
    return [
        graphify_bin,
        "--from",
        source,
        "graphify",
        "extract",
        str(target),
        "--code-only",
        "--no-cluster",
        "--out",
        str(target),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an ignored, local-only Graphify code graph; no LLM/API is used."
    )
    parser.add_argument("target", choices=("asahi", "quickshell", "both"), nargs="?", default="asahi")
    parser.add_argument("--quickshell-dir", help="path to the independent quickshell- checkout")
    parser.add_argument("--dry-run", action="store_true", help="print pinned commands without running them")
    args = parser.parse_args(argv)

    try:
        lock = load_lock()
        graphify_bin = shutil.which("uvx")
        if not graphify_bin:
            raise ValueError("uvx is required; install uv, then rerun this optional developer tool")
        targets: list[tuple[str, Path]] = []
        if args.target in ("asahi", "both"):
            targets.append(("asahi", verify_repo(PROJECT_ROOT, EXPECTED_REPOSITORIES["asahi"][1], "asahi-system")))
        if args.target in ("quickshell", "both"):
            targets.append(("quickshell", resolve_quickshell(args.quickshell_dir)))

        for label, root in targets:
            command = graphify_command(graphify_bin, lock, root)
            if args.dry_run:
                print(f"[{label}] cwd={root}")
                print(shlex.join(command))
                continue
            print(f"[{label}] Graphify {lock['version']} @ {lock['commit'][:12]} (code-only)", flush=True)
            subprocess.run(command, cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
        print(f"graphify-project: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
