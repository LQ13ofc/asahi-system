"""Public Niri+ command line."""

from __future__ import annotations

import argparse
import pathlib
import subprocess

from . import benchmark, doctor, install_command, rollback, status, uninstall, update


def version() -> str:
    explicit = pathlib.Path(__file__).resolve().parents[1] / "VERSION"
    data_dir = pathlib.Path(__import__("os").environ.get("NIRI_PLUS_DATA_DIR", explicit.parent))
    candidate = data_dir / "VERSION"
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8").strip()
    return "0.0.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="niri+", description="Manage the Niri+ Fedora Asahi session.")
    parser.add_argument("--version", action="version", version=f"Niri+ {version()}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("status", help="show read-only installation status")
    install_parser = commands.add_parser("install", help="install the Niri+ session")
    install_parser.add_argument("--dry-run", action="store_true", help="show the plan without changing the host")
    commands.add_parser("update", help="check/apply a trusted Niri+ release (not yet available)")
    rollback_parser = commands.add_parser("rollback", help="restore Niri+ managed files")
    rollback_parser.add_argument("--remove-packages", action="store_true", help="also remove only explicitly tracked packages")
    commands.add_parser("doctor", help="run read-only diagnostics")
    benchmark_parser = commands.add_parser("benchmark", help="run the Phase A read-only collector")
    benchmark_parser.add_argument("--runs", type=int, default=1)
    benchmark_parser.add_argument("--output")
    commands.add_parser("uninstall", help="remove Niri+ managed files and tracked packages")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "status":
        print(status.render_status(version()))
    elif args.command == "install":
        return install_command.run_install(args.dry_run)
    elif args.command == "update":
        return update.run_update()
    elif args.command == "rollback":
        return rollback.run_rollback(args.remove_packages)
    elif args.command == "doctor":
        print(doctor.render_doctor())
    elif args.command == "benchmark":
        if not 1 <= args.runs <= 20:
            parser.error("--runs must be between 1 and 20")
        return benchmark.run_benchmark(args.runs, args.output)
    elif args.command == "uninstall":
        return uninstall.run_uninstall()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
