# Quickshell integration (Phase B2)

## Repository ownership

`LQ13ofc/quickshell-` owns the complete visual layer: QML, bar, panels, menus,
notifications, themes, animation, visual audio/network/Bluetooth/workspace,
battery, media, calendar and weather behavior. `LQ13ofc/asahi-system` owns
Niri base configuration, install, lifecycle, system package declarations,
health checks, benchmark, rollback and later update behavior. No QML or helper
implementation is copied into this repository.

Both repositories remain independently usable. `asahi-system` installs and
manages Niri without fetching Quickshell. The visual fork runs through its own
`qs` engine without requiring the Niri+ CLI; controls that request system
changes report the optional backend as unavailable when `niri+` is absent.

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
`integration/quickshell.lock.json` both pin one exact commit on each branch.
Production `main` remains at `55e92880d0aff75d235f283c839ec0990eaa9e17`;
this integration candidate pins `ee0a9b8c7836b14485efaf7aca33849d0210570d`.
The fork is private, so cloning
`asahi-system` needs GitHub access that can read both repositories. The
submodule may be initialized for development; installation does not trust its
working tree. `sudo niri+ install` installs only the Niri system core and does
not require access to the private Quickshell repository. To opt into visual
integration, use `sudo niri+ plugin install quickshell`; after that,
`sudo niri+ install` refreshes the plugin together with the core. The old
`--with-quickshell` switch is not supported by this candidate. The plugin
command checks both Git URLs, resolves exact commit objects, and materializes a hash-verified
snapshot from those objects. It installs that visual snapshot at
`/usr/local/share/niri-plus/quickshell`, with a
manifest recording repository, commit and blob hashes. This is outside the
`asahi-system` source tree; no QML is copied into the system repository.
When Quickshell has no trusted installed-plugin manifest, a core install does
not fetch the private repository. A validated legacy 0.1.9 integration is
migrated automatically; an unverifiable one blocks installation.
`status` and `doctor` still report a pin mismatch if the preserved runtime does
not match the current system lock.
`niri+ status` and `doctor` compare the installed snapshot manifest, files and
commit with the lock. Missing, changed or divergent content is reported, not
silently repaired.

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

The UI was originally developed against Quickshell 0.2.1. The package metadata
proves availability and architecture, not that every QML component works with
0.3.1 or Fedora Asahi's Wayland stack. On the M1, the infrastructure gate now
confirms a single managed Quickshell process, zero restarts, successful crash
result, live Wayland readiness and matching snapshot/pin/runtime state. Audio,
network/Bluetooth panels and broader device integration remain separate
`M1_REQUIRED` functional checks where no dedicated evidence has been recorded.

## Dependency inventory

| Dependency / feature | Class | Owner / notes |
|---|---|---|
| Quickshell ARM64 engine | REQUIRED | Niri+ install, exact NEVRA and GPG-checked temporary COPR. |
| PipeWire | REQUIRED | System-owned; Quickshell audio integration. Keep existing services. |
| NetworkManager | REQUIRED for network controls and Wi-Fi indicator | System-owned; Quickshell 0.3.1 `Quickshell.Networking` reads indicator state directly; the open panel uses the existing D-Bus helper for controls. |
| D-Bus session/system buses | REQUIRED | Session/system integration for audio, Wi-Fi, Bluetooth, notification and tray features. |
| Python 3 | REQUIRED | `agenda.py` and `netctl.py`; spawned for a request/panel, not a permanent daemon. |
| `python3-dbus`, `python3-gobject` | REQUIRED for network controls | Used by the on-demand NetworkManager control helper while the panel is open. |
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
The Wi-Fi indicator reads NetworkManager through Quickshell's native
networking module; it does not start a monitor/helper process. The panel's
control helper uses NetworkManager and consults iwd only for auxiliary signal
metrics when available. The baseline has NetworkManager and `wpa_supplicant`,
but no iwd package, so the indicator no longer depends on iwd. Larger QML
performance changes remain for a separately measured A/B phase.

Fedora's package catalog lists `python3-dbus` 1.4.0-9.fc44 and
`python3-gobject` 3.56.3-1.fc44 for Fedora 44. These packages supply the D-Bus
and GLib bindings imported by the on-demand Wi-Fi control helper; they are now
declared in the explicit Niri+ package set. References:
[python3-dbus Fedora 44](https://packages.fedoraproject.org/pkgs/dbus-python/python3-dbus/fedora-44.html)
and [python3-gobject Fedora 44](https://packages.fedoraproject.org/pkgs/pygobject3/python3-gobject/fedora-44-updates.html).

## Session lifecycle

The Fedora-packaged `niri-session`/`niri.service` remains the display-manager
entry and session owner. M1 logs showed `graphical-session-pre.target` can be
reached while `niri.service` is still creating its Wayland display. Starting
clients at that point caused Quickshell and LXQt PolicyKit to abort with no
`wl_display`; both worked after manual restart. `asahi-niri-wayland-ready.service`
now gates the session clients: it waits for the real socket and verifies a
Wayland `wl_display.sync` response, imports the selected display name into the
user manager/D-Bus environment, and exits. It has no fixed sleep or persistent
poller. Quickshell and PolicyKit both `Requires=` and `After=` this readiness
unit, are conditioned on `XDG_CURRENT_DESKTOP=niri`, and are `PartOf=`
`graphical-session.target`. Their first start therefore waits for compositor
readiness and they stop with the session. Quickshell retains one systemd-owned
instance, bounded restart-on-failure, and journal logs (`journalctl --user -u
asahi-quickshell.service`). `niri+ status` and `doctor` check the live socket
with a Wayland sync handshake, `graphical-session.target`, the readiness,
Quickshell and PolicyKit unit results, restart counts, logs and duplicate
processes. Any first-start restart or unavailable display is reported as a
warning rather than healthy. No Niri `spawn-at-startup` is added to either
repository.

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

Follow-up M1 logs confirmed the Video Bridge filter worked, but also found
`kdeconnectd`, `kalendarac`, `akonadi_control` and multiple Akonadi agents
resident in Niri. The Plasma baseline also records `plasma-keyboard`,
`org_kde_powerdevil`, the KDE PolicyKit agent, the KWin Wayland wrapper and
`startplasma-wayland`; the read-only doctor detects these process names even if
their parent unit is no longer active. The parent causes were the baseline's
KDE user services and generated XDG autostart units being eligible in any
desktop: these generated entries had no KDE-only restriction, and direct user
units had no session-specific condition. Niri+ now applies
`ConditionEnvironment=XDG_CURRENT_DESKTOP=KDE` to the four baseline XDG units
(Discover notifier, Kalendar, KDE Connect and XWayland Video Bridge) and to
direct background units in the baseline: Akonadi Control, Baloo,
KUnifiedPush, and Plasma's menu proxy, accessibility, activity manager, kded,
session manager, KWin, shell, PolicyKit, Powerdevil, KDE portal and XEmbed proxy.
`PartOf=graphical-session.target` makes those background units stop at session
exit. Plasma satisfies the condition and keeps its normal services. Packages,
desktop files and global service configuration remain untouched; nothing is
globally disabled or masked. D-Bus-activated KDE Wallet is retained for
applications that need stored credentials, and an explicitly opened Konsole
is not counted as a session leak.

`niri+ status` and `doctor` inspect active KDE autostart/services and
characteristic background process names, including Akonadi agents. They warn
in Niri for unexpected KDE activity or a missing session filter, but report
`NOT_APPLICABLE` in Plasma. A normal Niri logout was observed in the same M1
logs (`quitting after confirming exit dialog`); subsequent Wayland loss was
therefore expected. DRM/EDID/HDR/gamma warnings did not prevent Niri startup.

## Validation boundary

Cloud tests verify lock consistency, snapshot object hashes, concurrent source
edits, non-interactive Git, transaction rollback/idempotence, Wayland socket
handshake and target ordering, KDE filters and diagnostics, CLI output and the
absence of Niri `spawn-at-startup` duplication. The M1 infrastructure gate has
validated live Wayland surfaces sufficiently for startup/lifecycle, process
uniqueness, restart state, KDE isolation and the installed snapshot/pin/runtime
contract. Audio, network, Bluetooth, notifications, panel helpers and memory
remain feature/performance checks rather than blockers for the closed
infrastructure gate. The next controlled measurement compares clean Niri-only
idle against Niri+Quickshell with five runs each under the same login/settle
conditions.

The audit found that the Wi-Fi bar indicator had depended on iwd even though
the captured target uses NetworkManager with `wpa_supplicant` and has no iwd.
It now reads device and SSID state through Quickshell 0.3.1's native
`Quickshell.Networking` API. The Wi-Fi control panel remains NetworkManager
based and only starts its Python helper while open. No permanent network helper
or second shell startup was added. The indicator and panels load/render in the
Cloud harness, but live NetworkManager behavior and the pinned engine's exact
API behavior remain M1_REQUIRED.
