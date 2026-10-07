"""Read-only host probes shared by status and doctor."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import stat
import subprocess
from typing import Callable

OK = "OK"
NOT_INSTALLED = "NOT_INSTALLED"
NOT_CONFIGURED = "NOT_CONFIGURED"
UNAVAILABLE = "UNAVAILABLE"
WARNING = "WARNING"
M1_REQUIRED = "M1_REQUIRED"

Runner = Callable[..., subprocess.CompletedProcess[str]]


def read_os_release(root: pathlib.Path = pathlib.Path("/")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in (root / "etc/os-release").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"').strip("'")
    except OSError:
        return values
    return values


def run(args: list[str], runner: Runner = subprocess.run) -> subprocess.CompletedProcess[str] | None:
    try:
        return runner(args, check=False, text=True, capture_output=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return None


def find_binary(root: pathlib.Path, name: str) -> str | None:
    if root == pathlib.Path("/"):
        return shutil.which(name)
    for directory in ("usr/bin", "usr/local/bin", "usr/sbin", "usr/local/sbin", "bin", "sbin"):
        candidate = root / directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def command_version(name: str, runner: Runner = subprocess.run,
                    root: pathlib.Path = pathlib.Path("/")) -> tuple[str, str | None]:
    path = find_binary(root, name)
    if not path:
        return NOT_INSTALLED, None
    result = run([path, "--version"], runner)
    if result and result.returncode == 0:
        lines = (result.stdout or result.stderr).strip().splitlines()
        return (OK, lines[0]) if lines else (WARNING, None)
    return WARNING, None


def package_status(package: str, runner: Runner = subprocess.run,
                   root: pathlib.Path = pathlib.Path("/")) -> str:
    if root != pathlib.Path("/"):
        return UNAVAILABLE
    if not shutil.which("rpm"):
        return UNAVAILABLE
    result = run(["rpm", "-q", package], runner)
    return OK if result and result.returncode == 0 else NOT_INSTALLED


def service_status(service: str, runner: Runner = subprocess.run, user: bool = False,
                   root: pathlib.Path = pathlib.Path("/")) -> str:
    if root != pathlib.Path("/"):
        return UNAVAILABLE
    args = ["systemctl"]
    if user:
        args.append("--user")
    args.extend(["is-active", service])
    result = run(args, runner)
    if result and result.returncode == 0 and result.stdout.strip() == "active":
        return OK
    if result and result.stdout.strip() in ("inactive", "failed", "unknown"):
        return WARNING
    return UNAVAILABLE


def installed_file(root: pathlib.Path, absolute: str) -> bool:
    return (root / absolute.lstrip("/")).is_file()


def session_status(root: pathlib.Path) -> str:
    packaged = root / "usr/share/wayland-sessions/niri.desktop"
    entries = []
    for directory in (root / "usr/share/wayland-sessions", root / "usr/local/share/wayland-sessions"):
        if not directory.is_dir():
            continue
        for candidate in directory.glob("*.desktop"):
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read(candidate, encoding="utf-8")
                if not parser.has_section("Desktop Entry"):
                    continue
                desktop = parser["Desktop Entry"]
                identity = " ".join((
                    candidate.stem,
                    desktop.get("Name", ""),
                    desktop.get("Exec", ""),
                    desktop.get("TryExec", ""),
                )).lower()
                if "niri" in identity:
                    entries.append(candidate)
            except (configparser.Error, OSError):
                continue
    if len(entries) != 1 or entries[0] != packaged:
        return WARNING if entries else NOT_INSTALLED
    if not packaged.is_file():
        return NOT_INSTALLED
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(packaged, encoding="utf-8")
        return OK if parser.get("Desktop Entry", "Name", fallback="") == "Niri" else WARNING
    except (configparser.Error, OSError):
        return WARNING


def configuration_status(root: pathlib.Path, environment: dict[str, str] | None = None) -> str:
    config = root / "etc/niri/config.kdl"
    try:
        source = config.read_text(encoding="utf-8")
    except OSError:
        return NOT_CONFIGURED
    includes = re.findall(r'^\s*include\s+"([^"\n]+)"', source, flags=re.MULTILINE)
    required = {"keybinds.kdl", "outputs.kdl", "rules.kdl", "autostart.kdl"}
    if len(includes) != len(required) or set(includes) != required:
        return WARNING
    for included in includes:
        path = pathlib.PurePosixPath(included)
        if path.is_absolute() or ".." in path.parts or not (config.parent / included).is_file():
            return WARNING
    if root == pathlib.Path("/") or environment is not None:
        environment = environment if environment is not None else os.environ
        configured = environment.get("NIRI_CONFIG")
        user_config_dir = pathlib.Path(environment.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config"))
        user_config = user_config_dir / "niri/config.kdl"
        if configured and pathlib.Path(configured).resolve() != config.resolve():
            return WARNING
        if user_config.is_file():
            return WARNING
    return OK


def configuration_validation(root: pathlib.Path, runner: Runner) -> str:
    if root != pathlib.Path("/"):
        return UNAVAILABLE
    niri = find_binary(root, "niri")
    if not niri:
        return NOT_INSTALLED
    result = run([niri, "validate", "--config", "/etc/niri/config.kdl"], runner)
    if result and result.returncode == 0:
        return OK
    return WARNING if result else UNAVAILABLE


def launcher_status(root: pathlib.Path) -> str:
    keybinds = root / "etc/niri/keybinds.kdl"
    try:
        source = keybinds.read_text(encoding="utf-8")
    except OSError:
        return NOT_CONFIGURED
    match = re.search(r'^\s*Mod\+Space\s*\{\s*spawn\s+"fuzzel"\s*;?\s*\}', source, re.MULTILINE)
    return OK if match else WARNING


def niri_session_active() -> bool:
    desktops = {item.strip().lower() for item in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")}
    return bool(os.environ.get("NIRI_SOCKET")) or "niri" in desktops


KDE_BACKGROUND_PROCESSES = (
    "plasmashell", "kwin_wayland", "kded6", "ksmserver", "kalendarac",
    "discovernotifier", "discovernotifie", "kdeconnectd", "akonadi_control", "akonadiserver",
    "akonadi_agent_server", "akonadi_maildispatcher_agent", "akonadi_indexing_agent",
    "baloo_file", "powerdevil", "org_kde_powerdevil", "polkit-kde-auth",
    "plasma-keyboard", "xwaylandvideobridge", "xwaylandvideobr", "kunifiedpush-distributor",
)


def kde_processes(proc_root: pathlib.Path = pathlib.Path("/proc")) -> list[str]:
    processes = []
    try:
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                argv = [part.decode(errors="replace").lower()
                        for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
                comm = (entry / "comm").read_text(encoding="utf-8").strip().lower()
            except OSError:
                continue
            try:
                executable = str((entry / "exe").resolve()).lower()
            except OSError:
                executable = ""
            argv0 = pathlib.Path(argv[0]).name if argv else ""
            process_names = {comm, pathlib.Path(executable).name, argv0}
            match = next((name for name in KDE_BACKGROUND_PROCESSES
                          if name in process_names), None)
            if match is None:
                if any(name.startswith("akonadi_") for name in process_names):
                    match = "akonadi_agent"
                elif any(name.startswith("kwin_wayland") for name in process_names):
                    match = "kwin_wayland"
                elif any(name.startswith("startplasma-") for name in process_names):
                    match = "startplasma"
            if match:
                processes.append(f"process:{match}")
    except OSError:
        pass
    return processes


def kde_isolation_status(root: pathlib.Path, runner: Runner,
                         proc_root: pathlib.Path = pathlib.Path("/proc")) -> tuple[str, list[str]]:
    # Import lazily to avoid the host/install module import cycle while making
    # the installed filter set the single source of truth for this check.
    from . import install

    if not niri_session_active():
        return "NOT_APPLICABLE", []
    result = run(["systemctl", "--user", "list-units", "--all", "--no-legend", "--no-pager"], runner)
    if not result or result.returncode != 0:
        return UNAVAILABLE, []
    active = []
    for line in (result.stdout or "").splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[2] != "active":
            continue
        unit = fields[0]
        if (unit.startswith("plasma-") or unit.endswith("@autostart.service") and unit.startswith("app-org.kde.")
                or unit in {"akonadi_control.service", "kde-baloo.service", "kunifiedpush-distributor.service"}
                or unit.startswith("akonadi_") and unit.endswith(".service")
                or unit in {"kdeconnectd.service", "kalendarac.service", "discovernotifier.service",
                            "xwaylandvideobridge.service"}):
            if unit.startswith("app-org.kde.konsole@"):
                continue  # An explicitly opened terminal is a user app, not a leaked autostart.
            active.append(unit)
    processes = kde_processes(proc_root)
    active.extend(processes)
    filter_units = (*install.KDE_SESSION_ONLY_UNITS, *install.KDE_AUTOSTART_UNITS)
    missing_filters = []
    for unit in filter_units:
        dropin = root / f"usr/lib/systemd/user/{unit}.d/10-niri-session.conf"
        try:
            body = dropin.read_text(encoding="utf-8")
            valid = ("ConditionEnvironment=XDG_CURRENT_DESKTOP=KDE" in body
                     and "PartOf=graphical-session.target" in body)
        except OSError:
            valid = False
        if not valid:
            missing_filters.append(unit)
    bridge = "app-org.kde.xwaylandvideobridge@autostart.service"
    if missing_filters or bridge in active or "process:xwaylandvideobridge" in processes:
        active.extend(f"missing KDE-only filter:{unit}" for unit in missing_filters)
        return WARNING, active
    return (WARNING if active else OK), active


def wayland_readiness_status(root: pathlib.Path) -> str:
    helper = root / "usr/lib/systemd/user/asahi-niri-wayland-ready.service"
    quickshell = root / "usr/lib/systemd/user/asahi-quickshell.service"
    polkit = root / "usr/lib/systemd/user/asahi-niri-polkit-agent.service"
    try:
        helper_text = helper.read_text(encoding="utf-8")
        quickshell_text = quickshell.read_text(encoding="utf-8")
        polkit_text = polkit.read_text(encoding="utf-8")
    except OSError:
        return NOT_INSTALLED
    requirements = [
        "ConditionEnvironment=XDG_CURRENT_DESKTOP=niri" in helper_text,
        "Before=graphical-session.target" in helper_text,
        "PartOf=graphical-session.target" in helper_text,
        "WantedBy=graphical-session.target" in helper_text,
        "wayland_ready.py" in helper_text,
    ]
    for client in (quickshell_text, polkit_text):
        requirements.extend((
            "ConditionEnvironment=XDG_CURRENT_DESKTOP=niri" in client,
            "Requires=asahi-niri-wayland-ready.service" in client,
            "After=asahi-niri-wayland-ready.service" in client,
            "PartOf=graphical-session.target" in client,
        ))
    return OK if all(requirements) else WARNING


def wayland_clients_runtime_status(runner: Runner) -> tuple[str, list[str]]:
    """Check the live display and first-start outcome without changing state."""
    if not niri_session_active():
        return "NOT_APPLICABLE", []
    from . import wayland_ready

    issues = []
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    display = os.environ.get("WAYLAND_DISPLAY")
    if not runtime_dir or not display:
        issues.append("Wayland environment is incomplete")
    else:
        display_path = pathlib.Path(display)
        socket_path = display_path if display_path.is_absolute() else pathlib.Path(runtime_dir) / display_path
        if not socket_path.is_socket() or not wayland_ready._handshake(socket_path, timeout=0.75):
            issues.append(f"Wayland display is not responsive: {display}")

    target = run(["systemctl", "--user", "show", "graphical-session.target", "--no-pager",
                  "--property=ActiveState"], runner)
    if not target or target.returncode != 0:
        return UNAVAILABLE, [*issues, "graphical-session.target state unavailable"]
    target_values = dict(line.split("=", 1) for line in (target.stdout or "").splitlines() if "=" in line)
    if target_values.get("ActiveState") != "active":
        issues.append("graphical-session.target is not active")

    for unit in ("asahi-niri-wayland-ready.service", "asahi-quickshell.service",
                 "asahi-niri-polkit-agent.service"):
        result = run(["systemctl", "--user", "show", unit, "--no-pager",
                      "--property=ActiveState", "--property=SubState", "--property=Result",
                      "--property=NRestarts"], runner)
        if not result or result.returncode != 0:
            return UNAVAILABLE, [*issues, f"{unit} state unavailable"]
        values = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
        if values.get("ActiveState") != "active" or values.get("Result") != "success":
            issues.append(f"{unit} is not active/successful")
        if values.get("NRestarts") != "0":
            issues.append(f"{unit} restarted {values.get('NRestarts', 'an unknown number of')} times")
    return (WARNING if issues else OK), issues


def installation_state(root: pathlib.Path) -> tuple[str, dict]:
    path = root / "var/lib/asahi-system/niri-performance/state.json"
    if path.is_symlink():
        return WARNING, {}
    if not path.exists():
        return NOT_CONFIGURED, {}
    try:
        if not path.is_file():
            return WARNING, {}
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o022 or mode & 0o044 != 0o044:
            return WARNING, {}
        if root == pathlib.Path("/") and path.stat().st_uid != 0:
            return WARNING, {}
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema_version") != 1 or not isinstance(state.get("entries"), dict):
            return WARNING, {}
        # Older installers called the applied version "known good" without
        # any runtime health check. Keep reads non-mutating and ask install to
        # migrate the field rather than repeating that false claim.
        if state.get("known_good_version") and not state.get("applied_version"):
            return WARNING, state
        return OK, state
    except (OSError, ValueError, json.JSONDecodeError):
        return WARNING, {}


def host_report(root: pathlib.Path = pathlib.Path("/"), machine: str | None = None,
                runner: Runner = subprocess.run) -> dict:
    from . import quickshell
    from . import install

    machine = machine or platform.machine()
    release = read_os_release(root)
    host = release.get("PRETTY_NAME") or "Unknown Linux"
    arch = machine
    state_status, state = installation_state(root)
    if state_status in (OK, WARNING) and state:
        try:
            state = install.load_state(root)
        except install.InstallError:
            state_status, state = WARNING, {}
    managed_files = {}
    entries = state.get("entries", {})
    installed_version = state.get("applied_version")
    for absolute in install.MANAGED_FILES if installed_version else entries:
        entry = entries.get(absolute, {})
        expected = entry.get("installed_sha256")
        if not expected:
            if installed_version:
                managed_files[absolute] = WARNING
            continue
        target = root / absolute.lstrip("/")
        if target.is_symlink():
            managed_files[absolute] = WARNING
            continue
        try:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            managed_files[absolute] = OK if actual == expected else WARNING
        except OSError:
            managed_files[absolute] = NOT_INSTALLED
    managed_links = {}
    for absolute, link_target in install.MANAGED_LINKS.items():
        if not installed_version:
            continue
        target = root / absolute.lstrip("/")
        try:
            managed_links[absolute] = OK if target.is_symlink() and os.readlink(target) == link_target else WARNING
        except OSError:
            managed_links[absolute] = NOT_INSTALLED
    niri_state, niri_version = command_version("niri", runner, root)
    from . import source_update
    cli_version_path = root / "usr/local/share/niri-plus/VERSION"
    try:
        cli_version = cli_version_path.read_text(encoding="utf-8").strip()
    except OSError:
        cli_version = None
    source_checkout = source_update.inspect_source(root, runner, installed_version=cli_version)
    quickshell_info = quickshell.report(root, runner)
    quickshell_state = state.get("quickshell", {})
    quickshell_info["known_good_commit"] = quickshell_state.get("known_good_commit") or NOT_CONFIGURED
    quickshell_info["state_expected_commit"] = quickshell_state.get("expected_commit") or NOT_CONFIGURED
    if installed_version:
        state_pin = quickshell_state.get("expected_commit")
        if state_pin != quickshell_info["expected_commit"]:
            quickshell_info["state_pin_status"] = WARNING
            quickshell_info["status"] = WARNING
            quickshell_info["warnings"].append(
                "Install state Quickshell pin differs from the repository lock; rerun sudo niri+ install to apply the reviewed pin."
            )
        else:
            quickshell_info["state_pin_status"] = OK
    else:
        quickshell_info["state_pin_status"] = NOT_CONFIGURED
    quickshell_info["processes"] = quickshell.process_diagnostics()
    gamescope = OK if find_binary(root, "gamescope") else UNAVAILABLE
    steam = OK if find_binary(root, "steam") else NOT_INSTALLED
    report = {
        "host": host,
        "architecture": arch,
        "fedora_asahi": OK if release.get("ID") == "fedora-asahi-remix" else WARNING,
        "fedora_version": release.get("VERSION_ID", "Unknown"),
        "niri": {"status": niri_state, "version": niri_version or "version unavailable"},
        "session": session_status(root),
        "session_package": packaged_session_status(root, runner),
        "configuration": configuration_status(root),
        "configuration_validation": configuration_validation(root, runner),
        "launcher": launcher_status(root),
        "launcher_binary": command_version("fuzzel", runner, root)[0],
        "rollback_state": rollback_state_status(root, state),
        "kde_isolation": kde_isolation_status(root, runner),
        "managed_file_checksums": managed_files,
        "managed_links": managed_links,
        "polkit_unit": OK if installed_file(root, "/usr/lib/systemd/user/asahi-niri-polkit-agent.service") else NOT_INSTALLED,
        "wayland_readiness": wayland_readiness_status(root),
        "wayland_clients_runtime": wayland_clients_runtime_status(runner),
        "portal_backend": OK if installed_file(root, "/usr/libexec/xdg-desktop-portal-gtk") else NOT_INSTALLED,
        "install_state": state_status,
        "known_good": state.get("verified_good_version", NOT_CONFIGURED),
        "applied_version": state.get("applied_version", "unknown"),
        "rollback": OK if state.get("entries") else NOT_CONFIGURED,
        "core": {
            "foot": package_status("foot", runner, root),
            "fuzzel": package_status("fuzzel", runner, root),
            "PolicyKit agent": package_status("lxqt-policykit", runner, root),
            "Python runtime": package_status("python3", runner, root),
            "Python D-Bus": package_status("python3-dbus", runner, root),
            "Python GObject": package_status("python3-gobject", runner, root),
        },
        "system": {
            "PipeWire": service_status("pipewire.service", runner, user=True, root=root),
            "WirePlumber": service_status("wireplumber.service", runner, user=True, root=root),
            "NetworkManager": service_status("NetworkManager.service", runner, root=root),
            "speakersafetyd": service_status("speakersafetyd.service", runner, root=root),
        },
        "optional": {
            "Gamescope": gamescope,
            "Steam": steam,
        },
        "quickshell": quickshell_info,
        "source_checkout": source_checkout,
        "m1_checks": M1_REQUIRED,
        "warnings": _warnings(release, machine, state_status, gamescope),
    }
    for group in ("core", "system"):
        for component, status in report[group].items():
            if status not in (OK, NOT_INSTALLED):
                report["warnings"].append(f"{component} state is {status}.")
    for absolute, status in managed_files.items():
        if status != OK:
            report["warnings"].append(f"Managed file check failed for {absolute}: {status}.")
    for absolute, status in managed_links.items():
        if status != OK:
            report["warnings"].append(f"Managed symlink check failed for {absolute}: {status}.")
    if "known_good_version" in state and not state.get("applied_version"):
        report["warnings"].append(
            "Install state uses the legacy known_good_version field without health verification; run sudo niri+ install to migrate it."
        )
    report["warnings"].append("Real input, audio, Wi-Fi, suspend/resume and graphics behavior require M1_REQUIRED validation.")
    report["warnings"].extend(quickshell_info["warnings"])
    if source_checkout["status"] not in (OK, "UNAVAILABLE"):
        report["warnings"].append(f"Niri+ source checkout health is {source_checkout['status']}.")
    if report["kde_isolation"][0] == WARNING:
        report["warnings"].append("Unexpected KDE/Plasma units or background processes are active in the Niri session.")
    if report["wayland_readiness"] != OK:
        report["warnings"].append("Niri graphical clients lack a verified Wayland readiness gate.")
    if report["wayland_clients_runtime"][0] == WARNING:
        report["warnings"].append("Wayland readiness, graphical-session target or Niri client startup needs attention.")
    if report["launcher"] != OK:
        report["warnings"].append("Command+Space does not resolve to the Fuzzel launcher in the installed Niri config.")
    if report["configuration_validation"] == WARNING:
        report["warnings"].append("The installed Niri rejected /etc/niri/config.kdl during read-only validation.")
    elif report["configuration_validation"] == UNAVAILABLE:
        report["warnings"].append("The Niri config validator could not run on this host.")
    if report["launcher_binary"] not in (OK,):
        report["warnings"].append("Fuzzel executable is missing or did not return a version successfully.")
    return report


def packaged_session_status(root: pathlib.Path, runner: Runner) -> str:
    if root != pathlib.Path("/"):
        return UNAVAILABLE
    if not shutil.which("rpm"):
        return UNAVAILABLE
    result = run(["rpm", "-qf", "--qf", "%{NAME}", "/usr/share/wayland-sessions/niri.desktop"], runner)
    if not result:
        return UNAVAILABLE
    return OK if result.returncode == 0 and (result.stdout or "").strip() == "niri" else WARNING


def rollback_state_status(root: pathlib.Path, state: dict) -> str:
    entries = state.get("entries", {})
    for relative, entry in entries.items():
        if entry.get("kind") != "file":
            continue
        backup_value = entry.get("backup")
        if not backup_value or backup_value != f"/var/lib/asahi-system/niri-performance/backups{relative}":
            return WARNING
        backup = root / backup_value.lstrip("/")
        if backup.is_symlink() or not backup.is_file():
            return WARNING
    return OK if entries else NOT_CONFIGURED


def _warnings(release: dict[str, str], machine: str, state_status: str, gamescope: str) -> list[str]:
    warnings = []
    if release.get("ID") != "fedora-asahi-remix":
        warnings.append("This host is not Fedora Asahi Remix; install is restricted to that target.")
    if release.get("VERSION_ID") != "44":
        warnings.append(f"Fedora {release.get('VERSION_ID', 'unknown')} is not the declared Fedora Asahi 44 target.")
    if machine != "aarch64":
        warnings.append(f"Architecture {machine} is not the target aarch64 hardware.")
    if state_status == WARNING:
        warnings.append("Niri+ install state is malformed or unreadable.")
    if gamescope == UNAVAILABLE:
        warnings.append("Gamescope compatibility on Asahi is M1_REQUIRED; normal Niri use remains available.")
    else:
        warnings.append("Installed Gamescope compatibility on Asahi remains M1_REQUIRED.")
    return warnings
