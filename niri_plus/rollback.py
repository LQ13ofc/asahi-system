"""Rollback only the state recorded by the Niri+ installer."""

from __future__ import annotations
import os
import pathlib
import subprocess
from . import install_transaction
from .install import InstallError, rollback as restore_managed


def run_rollback(remove_packages: bool = False, root: pathlib.Path = pathlib.Path("/"),
                 require_root: bool = True) -> int:
    if require_root and os.geteuid() != 0:
        print("Rollback requires root; use: sudo niri+ rollback")
        return 2
    try:
        with install_transaction.operation_lock(root):
            restore_managed(root=root, remove_packages=remove_packages, require_root=require_root)
    except (InstallError, install_transaction.TransactionError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Rollback failed: {exc}")
        return 2
    return 0
