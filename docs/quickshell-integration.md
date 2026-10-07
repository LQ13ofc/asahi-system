# Quickshell integration (Phase B2)

## Repository ownership

`LQ13ofc/quickshell-` owns the complete visual layer: QML, bar, panels, menus,
notifications, themes, animation, visual audio/network/Bluetooth/workspace,
battery, media, calendar and weather behavior. `LQ13ofc/asahi-system` owns
Niri base configuration, install, lifecycle, system package declarations,
health checks, benchmark, rollback and later update behavior. No QML or helper
implementation is copied into this repository.

The only Niri config snippet retained in Quickshell is visual or directly
coupled to its controls (layer blur and optional brightness IPC keybindings).
The generic `qs` autostart was removed; Niri+ owns process lifecycle.

## Pin mechanism choice

| Mechanism | Reproducibility and rollback | Separate development | Decision |
|---|---|---|---|
| Submodule plus lockfile | Git tree records the exact commit; a prior system commit naturally restores its prior visual commit. | Native; changes can land in either repository independently. | Selected. |
| Lockfile plus external fetch | Can pin a commit, but needs custom authenticated fetch, checkout, cleanup and rollback state. | Separate, with more updater logic. | More moving parts for the current private fork. |
| Quickshell RPM as integration | Useful for the engine, but does not version the user's QML fork. | Loses the independent config source. | Not a source integration mechanism. |

`.gitmodules` declares `external/quickshell`; the gitlink and
`integration/quickshell.lock.json` both pin
`e0731859af5dc559e115760de41db43f5dbe5bc8`. The fork is private, so cloning
`asahi-system` needs GitHub access that can read both repositories. Initialize
the submodule recursively before running the single bootstrap, for example:
`git clone --recurse-submodules https://github.com/LQ13ofc/asahi-system.git`.
Bootstrap
checks the origin and exact commit and creates a symlink from
`/usr/local/share/niri-plus/quickshell` to that external checkout. It copies
the lock metadata, never visual source. `niri+ status` and `doctor` compare the
installed git HEAD with the lock. A changed or absent checkout is reported,
not silently repaired.

The current lock keeps the expected visual commit separate from the Niri+
version and reserves `known_good_commit` in install state. `niri+ update` stays
a safe stub until content verification, health check and transactional
known-good handling exist for both version streams. Rollback of source changes
is therefore a Git commit rollback at this stage; updater-driven Quickshell
rollback is not yet implemented.

## Fedora 44 aarch64 engine package

On 2026-10-07, the public COPR project `errornointernet/quickshell` reported
the `fedora-44-aarch64` build of `quickshell-0:0.3.1-2.fc44` as succeeded, and
the repository metadata lists the package architecture as `aarch64`. The
installer asks DNF for this exact NEVRA using a temporary repo URL, with
`gpgcheck=1` and the COPR public key. It does not enable or write a persistent
COPR repo. No x86 package or emulation is involved.

Sources: [COPR project](https://copr.fedorainfracloud.org/coprs/errornointernet/quickshell/), [Fedora 44 aarch64 COPR metadata](https://download.copr.fedorainfracloud.org/results/errornointernet/quickshell/fedora-44-aarch64/repodata/repomd.xml), and [build 11085751](https://copr.fedorainfracloud.org/api_3/build/11085751). The runtime package version is locked independently of the Quickshell repository commit. If the COPR no longer serves the exact NEVRA, installation fails safely pending a reviewed package-lock update.

The QML harness and project notes are based on Quickshell 0.2.1. The package
metadata proves availability and architecture, not that every QML component
works with 0.3.1 or Fedora Asahi's Wayland stack. `niri+ doctor` and status
mark this as `M1_REQUIRED`; the first session login must verify the bar, D-Bus
features, restart behavior and logs before treating this commit as known-good.

## Dependency inventory

| Dependency / feature | Class | Owner / notes |
|---|---|---|
| Quickshell ARM64 engine | REQUIRED | Niri+ install, exact NEVRA and GPG-checked temporary COPR. |
| PipeWire | REQUIRED | System-owned; Quickshell audio integration. Keep existing services. |
| NetworkManager | REQUIRED for network controls | System-owned; Wi-Fi state and mutation API. |
| D-Bus session/system buses | REQUIRED | Session/system integration for audio, Wi-Fi, Bluetooth, notification and tray features. |
| Python 3 | REQUIRED for helpers | `agenda.py` and `netctl.py`; spawned for a request/panel, not a permanent daemon. |
| BlueZ | OPTIONAL | Bluetooth panel reports unavailable if no daemon/device. |
| UPower | OPTIONAL | Battery UI enhancement; status may be absent. |
| MPRIS | LAZY/ON_DEMAND | Quickshell service observes available players; no separate service is added. |
| Notification server | REQUIRED capability, built in | The bar provides `org.freedesktop.Notifications`; do not add a second daemon. |
| Weather via curl | OPTIONAL | One-hour weather refresh; location lookup on interaction. |
| Calendar and `op` | LAZY/ON_DEMAND | Calendar helper is requested when panel opens; `op` only if secret URLs use 1Password. |
| Tray | LAZY/ON_DEMAND | Quickshell StatusNotifierItem support, no additional package. |
| `udevadm` | REQUIRED system utility | Backlight change events, supplied by systemd/udev. |
| `brightnessctl` | OPTIONAL | Needed only by optional brightness keybinding snippet. |
| `cava` | LAZY/ON_DEMAND | Visualizer process exists only while the music visualizer is open. |
| JetBrains Mono fonts | OPTIONAL | Preferred visual style; system monospace fallback remains available. |
| PySide6, Pillow, `pyside6-qmllint`, test harness | DEVELOPMENT_ONLY | Cloud validation only; not installed on the M1 as a runtime dependency. |

This table classifies features; it is not a blanket package installation list.
`iwd`, `gdbus` and `busctl` are used only where present for auxiliary
read-only data or helper operations. Larger QML performance changes remain
for a separately measured A/B phase.

## Session lifecycle

The Fedora-packaged `niri-session`/`niri.service` remains the display-manager
entry and session owner. `asahi-quickshell.service` is wanted by
`graphical-session.target`, ordered after `graphical-session-pre.target`,
`PartOf=graphical-session.target`, and conditioned on
`XDG_CURRENT_DESKTOP=niri`. It executes the pinned external checkout through
`qs --path`; systemd provides one unit instance, bounded restart-on-failure,
stop on session exit, and journal logs (`journalctl --user -u
asahi-quickshell.service`). `niri+ doctor` checks unit state, restart count,
recent log availability and duplicate processes. No process starts from Plasma because
its desktop environment fails the Niri condition. No Niri `spawn-at-startup`
is added to either repository.

The service does not start during install. It becomes eligible with the next
Niri graphical-session lifecycle. A user unit reload is requested without
starting or stopping any service when install runs inside an active user
session; otherwise the manager loads the unit on its next login.

## KDE XWayland Video Bridge leak

The captured Plasma baseline contains
`app-org.kde.xwaylandvideobridge@autostart.service`, generated alongside
Discover, Kalendar and KDE Connect by `systemd-xdg-autostart-generator`. Fedora
44 package metadata/source shows that the package installs
`/etc/xdg/autostart/org.kde.xwaylandvideobridge.desktop`. The upstream 0.5.2
desktop entry includes `Exec=xwaylandvideobridge` and `NoDisplay=true`, but no
`OnlyShowIn` or `NotShowIn`. Current systemd generator source translates these
fields to an `ExecCondition` for `$XDG_CURRENT_DESKTOP`, so with no desktop
restriction it generates a unit eligible in Niri as well as Plasma. This is
why a KDE-purpose component appeared in the first Niri run.

Niri+ installs a drop-in only for that generated unit with
`ConditionEnvironment=XDG_CURRENT_DESKTOP=KDE`. It is skipped before execution
in Niri and remains eligible in Plasma. The package, desktop file, Plasma
services and autostart files are untouched. The process is not killed after
launch. The additional baseline KDE autostarts remain unmodified pending
Niri-specific evidence about whether each belongs there.

## Validation boundary

Cloud tests can verify lock consistency, source path, systemd unit and drop-in
text, idempotent managed-file backup/rollback, CLI output and the absence of
Niri `spawn-at-startup` duplication. The M1 is still required to validate
Quickshell 0.3.1 against this v0.2.1-oriented QML, Wayland surfaces, audio,
network, Bluetooth, notifications, crash restart, process uniqueness, panel
helpers and memory. First compare clean Niri-only idle against Niri+Quickshell
with five runs each under the same login/settle conditions.
