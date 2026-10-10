"""Remove files and packages explicitly tracked as Niri+ managed."""

from __future__ import annotations
import os
import subprocess
from . import install_transaction
from .install import InstallError, rollback


def run_uninstall() -> int:
    if os.geteuid() != 0:
        print("Uninstall requires root; use: sudo niri+ uninstall")
        return 2
    try:
        with install_transaction.operation_lock():
            rollback(remove_packages=True)
    except (InstallError, install_transaction.TransactionError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Uninstall failed: {exc}")
        return 2
    print("Niri+ managed session files removed. Plasma, SDDM, kernel, Mesa, Asahi drivers, audio, network, and speaker safety were left untouched.")
    return 0
