"""Public install command backed by the reversible Phase B installer."""

from __future__ import annotations

import os
import platform
import subprocess
from . import install


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
        install.apply_install()
    except (install.InstallError, subprocess.CalledProcessError) as exc:
        print(f"Install failed: {exc}")
        return 2
    return 0
