"""Public install command backed by the reversible Phase B installer."""

from __future__ import annotations

import os
import platform
import subprocess
from . import install, source_update


def run_install(dry_run: bool = False, *, include_quickshell: bool = False) -> int:
    if dry_run:
        install.render_plan(include_quickshell=include_quickshell)
        errors = install.target_mismatches(install.parse_os_release(), platform.machine())
        if errors:
            print("Install would refuse this host: " + "; ".join(errors))
        return 0
    if os.geteuid() != 0:
        print("Install requires root; use: sudo niri+ install")
        return 2
    try:
        errors = install.target_mismatches(install.parse_os_release(), platform.machine())
        if errors:
            print("Install refused before updating Niri+: incompatible host: " + "; ".join(errors))
            return 2
        source_root = source_update.discover_source_root()
        with source_update.resolved_snapshot(source_root, include_quickshell=include_quickshell) as snapshot:
            if snapshot.include_quickshell:
                print(f"Resolved Niri+ {snapshot.system_commit[:12]} and Quickshell {snapshot.quickshell_commit[:12]}.")
            else:
                print(f"Resolved Niri+ {snapshot.system_commit[:12]} (Niri-only; Quickshell was not fetched).")
            source_update.install_from_snapshot(snapshot)
    except (install.InstallError, source_update.SourceUpdateError, subprocess.CalledProcessError, OSError) as exc:
        print(f"Install failed: {exc}")
        return 2
    return 0
