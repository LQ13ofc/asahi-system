"""CLI-facing imports for the independently runnable offline recovery core."""

from __future__ import annotations

from . import recovery_core as _core

RECOVERY_DIR = _core.RECOVERY_DIR
RecoveryError = _core.RecoveryError
has_rpm_payload = _core.has_rpm_payload
latest_bundle = _core.latest_bundle
main = _core.main
managed_roots = _core.managed_roots
prepare_bundle = _core.prepare_bundle
restore = _core.restore
status = _core.status

# Kept visible for targeted safety tests and fault injection. These refer to
# the same stdlib modules used by the standalone recovery core.
_canonical_member_name = _core._canonical_member_name
_verify_bundle = _core._verify_bundle
shutil = _core.shutil
subprocess = _core.subprocess

__all__ = [
    "RECOVERY_DIR", "RecoveryError", "has_rpm_payload", "latest_bundle",
    "main", "managed_roots", "prepare_bundle", "restore", "status",
]
