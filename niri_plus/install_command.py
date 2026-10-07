"""Public install command backed by the reversible Phase B installer."""

from __future__ import annotations

import os
import platform
import subprocess
from . import install, source_update


def run_install(dry_run: bool = False) -> int:
    if dry_run:
        install.render_plan()
        errors = install.target_mismatches(install.parse_os_release(), platform.machine())
        if errors:
            print("Install would refuse this host: " + "; ".join(errors))
        return 0
    if os.geteuid() != 0:
        print("Install requires root; use: sudo niri+ install")
        return 2
    try:
        if os.environ.get("NIRI_PLUS_SELF_UPDATED") != "1":
            errors = install.target_mismatches(install.parse_os_release(), platform.machine())
            if errors:
                print("Install refused before updating Niri+: incompatible host: " + "; ".join(errors))
                return 2
            commit = source_update.refresh_and_bootstrap()
            print(f"Niri+ source updated to {commit[:12]}; continuing with refreshed CLI.")
            source_update.reexec_install()
            raise source_update.SourceUpdateError("re-exec unexpectedly returned")
        install.apply_install()
    except (install.InstallError, source_update.SourceUpdateError, subprocess.CalledProcessError) as exc:
        print(f"Install failed: {exc}")
        return 2
    return 0
