# Phase B: minimal functional Niri session

## Scope

B1 introduced the Fedora-packaged Niri session and minimal base tools; B2 integrates the pinned Quickshell checkout. M1 logs confirmed Niri rendered through Honeykrisp/Wayland, keyboard/trackpad operation and the terminal keybind worked, and the Quickshell bar appeared. Follow-up logs confirmed two blockers now addressed in the open release-gate PR: Quickshell/PolicyKit raced the live Wayland socket, and KDE/PIM background helpers entered Niri. Command+Space is bound to Fuzzel but still needs a real M1 smoke test; audio/network and suspend/resume also remain pending. Plasma remains installed and selectable as recovery.

Gaming Mode is out of scope. It means Gamescope active inside this same Niri session. Normal Niri may run Steam or a game without Gamescope; that is not Gaming Mode. There is no direct-Niri Gaming Mode fallback.

## Fedora 44 aarch64 package research

Package names and builds were checked against Fedora 44's official `Everything/aarch64` release and updates repository metadata on 2026-10-07. The updates repository currently lists `niri 26.04-1.fc44`, `foot 1.27.0-1.fc44`, and `xwayland-satellite 0.8.2-1.fc44`, all for aarch64. The release repository lists `fuzzel 1.14.0-1.fc44`, `xdg-desktop-portal-gtk 1.15.3-3.fc44`, and `lxqt-policykit 2.3.0-2.fc44`; Fedora 44 updates currently has `lxqt-policykit 2.4.0-1.fc44`. All queried records had `arch=aarch64` and an RPM path in Fedora's official repository metadata.

Sources:

- [Fedora 44 aarch64 updates repository metadata](https://dl.fedoraproject.org/pub/fedora/linux/updates/44/Everything/aarch64/repodata/repomd.xml)
- [Fedora 44 aarch64 release repository metadata](https://dl.fedoraproject.org/pub/fedora/linux/releases/44/Everything/aarch64/os/repodata/repomd.xml)
- [Fedora Niri spec, f44 branch](https://src.fedoraproject.org/rpms/niri/raw/f44/f/niri.spec)
- [Fedora LXQt PolicyKit spec, f44 branch](https://src.fedoraproject.org/rpms/lxqt-policykit/raw/f44/f/lxqt-policykit.spec)
- [Niri integration guidance](https://github.com/niri-wm/niri/wiki/Integrating-niri)
- [Niri required desktop software](https://github.com/niri-wm/niri/wiki/Important-Software)
- [Niri session launcher](https://github.com/niri-wm/niri/blob/main/resources/niri-session)
- [Niri user service](https://github.com/niri-wm/niri/blob/main/resources/niri.service)
- [Niri shutdown target](https://github.com/niri-wm/niri/blob/main/resources/niri-shutdown.target)

### Explicit Niri+ session package set

| Package | Why it is included |
|---|---|
| `niri` | Wayland compositor and packaged `niri-session`, `niri.service`, shutdown target, portal configuration and standard session entry. |
| `foot` | Small native Wayland terminal bound to Mod+Return. |
| `fuzzel` | Native Wayland application launcher; Command/Super+Space is primary and Mod+D remains an alias. |
| `xdg-desktop-portal-gtk` | Basic portal backend for file chooser and common desktop requests. Screen capture portal support is not enabled in B1. |
| `lxqt-policykit` | Lightweight Qt PolicyKit authentication agent (`/usr/libexec/lxqt-policykit-agent`), started only for the Niri graphical session. |
| `python3-dbus`, `python3-gobject` | Python 3 is part of the Fedora base; the on-demand NetworkManager control helper uses these D-Bus/GLib bindings. |

Fedora's Niri RPM has a hard dependency on `xwayland-satellite >= 0.7`; DNF installs it, and Niri starts it on demand for X11 clients. Do not add a manual `exec-once` or `DISPLAY` setup. Package `pipewire`, `pipewire-pulseaudio`, `wireplumber`, and `NetworkManager` were found installed in the real baseline. The installer checks and reports their presence but does not install, enable, restart, or reconfigure them.

The installer sets `install_weak_deps=False` to avoid Fedora's optional Niri suggestions (Waybar, GNOME portal, keyring, swaylock and similar). The base session does not promise GNOME screencasting, Secret portal, screen lock, screenshots, clipboard history or wallpaper. B2 adds the separate Quickshell engine and pinned visual checkout; the visual repository owns the UI. RPM dependencies remain DNF-managed.

Package metadata is mutable. Before applying on the Mac, DNF's live Fedora Asahi repository resolution remains the authoritative availability check; if a package has disappeared or needs another repo, stop and investigate instead of adding a foreign RPM source.

## Session and lifecycle

Niri+ reuses Fedora's packaged `Niri` display-manager entry instead of adding a second session. The first M1 installation exposed why this matters: Fedora's RPM already registered Niri, while the initial Niri+ entry registered another session with the same display name, so SDDM rendered `Niri (1)` and `Niri (2)`.

The Niri+ defaults are installed at `/etc/niri/config.kdl` (plus its included KDL files), which is the system fallback supported by Niri. This keeps Fedora's packaged `niri-session` lifecycle intact and avoids a custom display-manager launcher.

The packaged session script already imports the login environment into `systemd --user`, updates D-Bus activation environment, starts and waits for `niri.service`, starts `niri-shutdown.target`, and unsets session variables. Fedora's Niri user service orders itself around `graphical-session-pre.target`, binds to `graphical-session.target`, and brings up `xdg-desktop-autostart.target`; the shutdown target conflicts with graphical session targets. Niri+ uses that upstream lifecycle rather than duplicating it.

The PolicyKit agent and Quickshell are conditioned on `XDG_CURRENT_DESKTOP=niri` and are ordered after `asahi-niri-wayland-ready.service`. That short-lived user unit validates a Wayland `wl_display.sync` reply before the compositor target releases its clients; it retries only while startup is pending, with no fixed sleep or resident watcher. The helper and clients are `PartOf=graphical-session.target`, so they stop with the Niri session. NetworkManager, PipeWire, WirePlumber, `speakersafetyd`, SDDM and other system services are left untouched.

No output name, resolution, refresh rate, scale or Asahi-specific environment variables are guessed before testing on the M1. Xwayland support is package-provided and on-demand. There is no manual Xwayland service or `exec-once` app group; the user-facing Niri session comes from Fedora's package.

## Safe installer and rollback

The public interface is the `niri+` CLI. `scripts/bootstrap-niri-plus` remains the one executable bootstrap entrypoint and defaults to a read-only dry-run. The normal apply/update path is `sudo niri+ install`. It checks Fedora Asahi Remix 44/aarch64 before network access; Git runs as the source checkout owner with their HOME and credentials, non-interactive HTTP/GCM and SSH; it verifies the actual configured origin and `origin/main`, fetches exact commit objects, and validates the Quickshell gitlink against the lock. It then builds a root-owned, read-only snapshot from hash-checked Git objects. No privileged code or input is copied from the mutable working tree. The snapshot is removed after the transaction. `status`, `doctor` and `install --dry-run` remain read-only. The installer backs up managed files/state, stages the CLI and pinned Quickshell runtime, applies session migrations and verifies the filesystem state; the exact locked DNF transaction runs last, immediately before filesystem commit. Exceptions before commit restore the prior managed files and state. It reuses Fedora's session entry and leaves SDDM configuration, Plasma, boot/kernel/driver files, global services, and existing audio/network configuration alone.

The installed `/usr/local/bin/niri+` entrypoint uses Python isolated mode (`-I`) so caller `PYTHONPATH` or startup hooks cannot replace the trusted installed CLI. The child process that runs the verified snapshot receives a fresh Python environment with only that snapshot on `PYTHONPATH`.

Rollback restores backed-up files and removes only files created by this installer. It validates every backup before deleting managed files and preserves post-install edits in the rollback state directory. By default packages stay installed and their ownership record is retained; `--remove-packages` removes only explicit packages recorded as absent before apply, using `dnf remove --noautoremove`, and retains shared dependencies.

`niri+ rollback` restores only Niri+ managed files and retains package ownership for a later uninstall. `niri+ update` remains an unavailable stub; one-command refresh/apply belongs to `sudo niri+ install`. This filesystem transaction does not claim a runtime known-good health check. `status`, `doctor` and dry-run remain read-only. The initial Niri and Quickshell login test has run on the Mac; the readiness and KDE fixes still require an M1 retest.

### Niri+ command surface

`VERSION` is the single tracked source for the Niri+ application version. The bootstrap installs the Python CLI under `/usr/local/lib/niri-plus`, its data/collector under `/usr/local/share/niri-plus`, and the public executable at `/usr/local/bin/niri+`. It defaults to dry-run and backs up a previously bootstrapped copy before replacing it. It refuses unmanaged destinations.

- `niri+ status`: read-only host/session/package/service summary; Quickshell is part of the B2 setup. Gamescope and Steam remain optional/unavailable until separately validated.
- `niri+ install [--dry-run]`: dry-run is read-only; apply requires `sudo niri+ install` and uses the verified snapshot transaction described above.
- `niri+ rollback`: restores only files recorded by the installer; package removal is opt-in with `--remove-packages`.
- `niri+ uninstall`: removes tracked session files and only packages recorded as newly installed; it does not remove shared dependencies or protected system components.
- `niri+ doctor`: read-only diagnostics, including managed-file checksums and M1-required hardware checks.
- `niri+ benchmark [--runs N] [--output FILE]`: forwards arguments to the existing Phase A collector.
- `niri+ update`: safe unavailable stub; use `sudo niri+ install` for refresh and repair.

## Cloud checks and M1-required validation

Cloud tests cover KDL syntax parsing, desktop-entry shape, shell syntax, package manifest, target refusal logic, idempotency, backup and rollback in a temporary directory. They do not prove Niri option semantics, session-manager discovery, sound, networking, input, suspend/resume, Honeykrisp rendering, or power behavior.

**M1_REQUIRED:** after this PR is reviewed and merged, verify the single Fedora-provided Niri entry, Command+Space/Fuzzel, PipeWire/WirePlumber audio, Wi-Fi reconnect, suspend/resume, and return to Plasma recovery. Specifically confirm the first Quickshell and PolicyKit starts wait for the live Wayland socket without an initial crash/restart, and that `kdeconnectd`, Kalendar, Akonadi agents, Discover and other filtered helpers stay absent in Niri while Plasma still starts them. The observed Niri exit was normal (`quitting after confirming exit dialog`); DRM/EDID/HDR/gamma warnings did not block startup. Capture comparable idle baselines only after a fresh login and 2–3 minutes of stabilization; use five runs per profile.

## Release gate review

The main branch configuration had no `Mod+Space` binding; `Mod+D` was the only Fuzzel binding. The audit adds Command/Super+Space as the primary launcher and retains Mod+D as an alias. `Mod` maps to Super; the Mac Command key already operated a working `Mod+Return` terminal binding. The Fuzzel package is explicitly in the install set, but the launcher process and its application list still require the M1 smoke test.

The old custom `sessions/niri.desktop` and `sessions/launch/niri-session` source files are removed. Fedora's packaged `niri.desktop`/`niri-session` remains the only entry and keeps the upstream graphical-session lifecycle. The installer retains old managed paths only to safely migrate and roll back prior installations.

The release gate checks managed configuration hashes and links, Niri's read-only config validator, session duplicates, Fuzzel availability, Quickshell pin/state, any Quickshell process including unmanaged `qs -c` invocations, unit restart state and known KDE autostarts. Previous install state mislabeled an applied version as known-good; status flags that legacy state and does not silently claim runtime health. The source checkout may advance when an install fetches a newer commit, but privileged code is run only from that resolved snapshot; a failed transaction restores the prior managed installation. `niri+ update` remains stubbed and no runtime rollback health model is claimed.

The current PR closes the source-code TOCTOU: Git runs as the checkout owner, the configured origin and effective URL are checked separately, the remote `main` SHA is resolved once, and Git objects for the system commit and exact Quickshell gitlink/lock SHA are hash-verified before materialization. The snapshot is root-created, root-owned/read-only on the target, revalidated before use, and is the only `PYTHONPATH`/working directory for privileged installation. HTTP/GCM/SSH interaction is disabled. The managed CLI, QML runtime, pin, session files and state are covered by one exception-safe filesystem transaction. The locked RPM installation is deliberately the final mutation so a DNF error restores files/state without splitting the CLI/runtime/config version. A hard power loss is outside this in-process transaction guarantee; the installer does not claim a runtime known-good result.

The baseline contains four KDE-generated autostarts (Discover notifier, Kalendar, KDE Connect and XWayland Video Bridge), direct user units for Akonadi Control, Baloo, UnifiedPush and Plasma's menu proxy, accessibility, activity manager, kded, session manager, KWin, shell, PolicyKit, Powerdevil, KDE portal and XEmbed proxy, plus a D-Bus-activated KDE Wallet and user-opened Konsole. Niri+ adds KDE-only conditions and session stop ownership to the background/autostart units; the shared wallet and explicitly opened terminal are not treated as leaks. Nothing is disabled or masked globally. Cloud tests cover unit gating and diagnostics; the M1 must confirm those generated units are skipped in Niri and work normally in Plasma.
