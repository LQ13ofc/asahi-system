"""Public Niri+ command line."""

from __future__ import annotations

import argparse
import json
import pathlib

from . import benchmark, brightness, doctor, install_command, niri_settings, rollback, status, uninstall, update


def version() -> str:
    explicit = pathlib.Path(__file__).resolve().parents[1] / "VERSION"
    data_dir = pathlib.Path(__import__("os").environ.get("NIRI_PLUS_DATA_DIR", explicit.parent))
    candidate = data_dir / "VERSION"
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8").strip()
    return "0.0.0"


def _brightness_percent(value: str) -> int:
    try:
        percent = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer from 0 to 100") from error
    if not 0 <= percent <= 100:
        raise argparse.ArgumentTypeError("must be an integer from 0 to 100")
    return percent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="niri+", description="Manage the Niri+ Fedora Asahi session.")
    parser.add_argument("--version", action="version", version=f"Niri+ {version()}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("status", help="show read-only installation status")
    install_parser = commands.add_parser("install", help="install the Niri+ session")
    install_parser.add_argument("--dry-run", action="store_true", help="show the plan without changing the host")
    install_parser.add_argument("--with-quickshell", action="store_true",
                                help="also install the optional Quickshell visual integration")
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
    candidate_compare_parser = benchmark_actions.add_parser(
        "compare-quickshell", help="compare KNOWN-GOOD C0 with an experimental Quickshell C1 capture"
    )
    candidate_compare_parser.add_argument("--known-good", required=True, help="validated C0 niri-quickshell JSON")
    candidate_compare_parser.add_argument("--candidate", required=True, help="validated C1 niri-quickshell JSON")
    candidate_compare_parser.add_argument("--output", required=True, help="Markdown report path")
    candidate_compare_parser.add_argument("--json-output", help="optional machine-readable report path")
    candidate_compare_parser.add_argument(
        "--allow-asahi-system-commit-change", action="store_true",
        help="permit a differing Niri+ source commit after manually confirming only Quickshell selection changed",
    )
    memory_parser = commands.add_parser("memory", help="inspect global, process, cgroup and DRM memory read-only")
    memory_parser.add_argument("--window", type=float, default=2.0,
                               help="seconds for process CPU deltas (default: 2)")
    memory_parser.add_argument("--sample-period", type=float, default=0.5,
                               help="seconds between light process-presence samples")
    memory_parser.add_argument("--json-output", help="write the full diagnostic as JSON")
    memory_parser.add_argument("--series", action="store_true",
                               help="collect at 0, 5, 15, and 30 minutes, then exit")
    memory_parser.add_argument("--include-60-minutes", action="store_true",
                               help="include an optional final +60 minute sample (requires --series)")
    brightness_parser = commands.add_parser("brightness", help="set screen brightness through systemd-logind")
    brightness_actions = brightness_parser.add_subparsers(dest="brightness_action", required=True)
    brightness_set = brightness_actions.add_parser("set", help="set the active session backlight from 0 to 100 percent")
    brightness_set.add_argument("percent", type=_brightness_percent)
    brightness_set.add_argument("--device", help="backlight name; required when more than one device exists")
    settings_parser = commands.add_parser("niri-settings", help="manage safe user-level Niri preferences")
    settings_actions = settings_parser.add_subparsers(dest="settings_action", required=True)
    settings_actions.add_parser("status", help="show current user-level Niri preferences")
    settings_actions.add_parser("apply", help="validate and apply one JSON request from stdin")
    settings_actions.add_parser("rollback", help="restore the previous Niri+ preference set")
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
        return install_command.run_install(args.dry_run, include_quickshell=args.with_quickshell)
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
        if args.benchmark_action == "compare-quickshell":
            return benchmark.compare_quickshell_candidate_benchmarks(
                args.known_good,
                args.candidate,
                args.output,
                args.json_output,
                allow_asahi_system_commit_change=args.allow_asahi_system_commit_change,
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
    elif args.command == "memory":
        if args.include_60_minutes and not args.series:
            parser.error("--include-60-minutes requires --series")
        if not 0.5 <= args.window <= 15:
            parser.error("--window must be between 0.5 and 15 seconds")
        if not 0.2 <= args.sample_period <= args.window:
            parser.error("--sample-period must be between 0.2 seconds and --window")
        return benchmark.run_memory_diagnostic(
            args.json_output, args.window, args.sample_period,
            args.series, args.include_60_minutes,
        )
    elif args.command == "brightness":
        try:
            device, _level = brightness.set_percent(args.percent, args.device)
        except brightness.BrightnessError as error:
            parser.error(str(error))
        print(f"Brilho alterado para {args.percent}% ({device}).")
    elif args.command == "niri-settings":
        manager = niri_settings.NiriSettingsManager()
        if args.settings_action == "status":
            print(json.dumps(manager.status(), ensure_ascii=False, sort_keys=True))
        elif args.settings_action == "apply":
            return niri_settings.apply_from_stdin(manager)
        elif args.settings_action == "rollback":
            try:
                print(json.dumps(manager.rollback(), ensure_ascii=False, sort_keys=True))
            except niri_settings.NiriSettingsError as error:
                parser.error(str(error))
    elif args.command == "uninstall":
        return uninstall.run_uninstall()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
