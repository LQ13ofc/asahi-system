"""Public Niri+ command line."""

from __future__ import annotations

import argparse
import pathlib

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
    benchmark_parser = commands.add_parser("benchmark", help="run the validated A/B/C performance collector")
    benchmark_mode = benchmark_parser.add_mutually_exclusive_group()
    benchmark_mode.add_argument("--profile", choices=("plasma", "niri-core", "niri-quickshell"),
                                help="collect only if the live session matches this profile")
    benchmark_mode.add_argument("--prepare-niri-core", action="store_true",
                                help="temporarily runtime-mask and stop Quickshell for profile B")
    benchmark_mode.add_argument("--restore-niri-core", action="store_true",
                                help="remove the benchmark-owned runtime mask and restore Quickshell")
    benchmark_mode.add_argument("--diagnose-overhead", action="store_true",
                                help="measure collector overhead separately; does not subtract estimates")
    benchmark_parser.add_argument("--runs", type=int, default=1)
    benchmark_parser.add_argument("--window", type=float, default=10.0,
                                  help="seconds in each CPU/fault/I/O observation window")
    benchmark_parser.add_argument("--output")
    benchmark_actions = benchmark_parser.add_subparsers(dest="benchmark_action")
    compare_parser = benchmark_actions.add_parser("compare", help="compare validated Plasma/Niri A/B/C captures offline")
    compare_parser.add_argument("--plasma", required=True, help="validated profile A JSON")
    compare_parser.add_argument("--niri-core", required=True, help="validated profile B JSON")
    compare_parser.add_argument("--niri-quickshell", required=True, help="validated profile C JSON")
    compare_parser.add_argument("--output", required=True, help="Markdown report path")
    compare_parser.add_argument("--json-output", help="optional machine-readable comparison JSON path")
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
        if args.benchmark_action == "compare":
            return benchmark.compare_benchmarks(
                args.plasma, args.niri_core, args.niri_quickshell, args.output, args.json_output
            )
        if not any((args.profile, args.prepare_niri_core, args.restore_niri_core, args.diagnose_overhead)):
            parser.error("benchmark requires --profile, --prepare-niri-core, --restore-niri-core, --diagnose-overhead, or compare")
        if args.prepare_niri_core:
            return benchmark.prepare_niri_core()
        if args.restore_niri_core:
            return benchmark.restore_niri_core()
        if not 1 <= args.runs <= 20:
            parser.error("--runs must be between 1 and 20")
        if args.diagnose_overhead:
            return benchmark.run_overhead_diagnostic(args.runs, args.output)
        if not 1 <= args.window <= 300:
            parser.error("--window must be between 1 and 300 seconds")
        return benchmark.run_benchmark(args.profile, args.runs, args.window, args.output)
    elif args.command == "uninstall":
        return uninstall.run_uninstall()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
