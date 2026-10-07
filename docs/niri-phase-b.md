# Phase B: minimal functional Niri session

## Scope

B1 is a first, deliberately plain Niri session. Quickshell is deferred to B2 so its cost can be measured separately. This change prepares files and a reversible installer; it does not install packages or change the Mac. Plasma remains installed and selectable in the display manager as recovery.

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

### Explicit B1 package set

| Package | Why it is included |
|---|---|
| `niri` | Wayland compositor and packaged `niri-session`, `niri.service`, shutdown target, portal configuration and standard session entry. |
| `foot` | Small native Wayland terminal bound to Mod+Return. |
| `fuzzel` | Native Wayland application launcher bound to Mod+D. |
| `xdg-desktop-portal-gtk` | Basic portal backend for file chooser and common desktop requests. Screen capture portal support is not enabled in B1. |
| `lxqt-policykit` | Lightweight Qt PolicyKit authentication agent (`/usr/libexec/lxqt-policykit-agent`), started only for the Niri graphical session. |

Fedora's Niri RPM has a hard dependency on `xwayland-satellite >= 0.7`; DNF installs it, and Niri starts it on demand for X11 clients. Do not add a manual `exec-once` or `DISPLAY` setup. Package `pipewire`, `pipewire-pulseaudio`, `wireplumber`, and `NetworkManager` were found installed in the real baseline. The installer checks and reports their presence but does not install, enable, restart, or reconfigure them.

The installer sets `install_weak_deps=False` to avoid Fedora's optional Niri suggestions (Waybar, GNOME portal, keyring, swaylock and similar) in this first comparison. This means B1 does not promise GNOME screencasting, Secret portal, screen lock, notifications, screenshots, clipboard history, wallpaper, or Quickshell. These can be added as separately measured session features. RPM dependencies remain DNF-managed.

Package metadata is mutable. Before applying on the Mac, DNF's live Fedora Asahi repository resolution remains the authoritative availability check; if a package has disappeared or needs another repo, stop and investigate instead of adding a foreign RPM source.

## Session and lifecycle

Niri+ reuses Fedora's packaged `Niri` display-manager entry instead of adding a second session. The first M1 installation exposed why this matters: Fedora's RPM already registered Niri, while the initial Niri+ entry registered another session with the same display name, so SDDM rendered `Niri (1)` and `Niri (2)`.

The Niri+ defaults are installed at `/etc/niri/config.kdl` (plus its included KDL files), which is the system fallback supported by Niri. This keeps Fedora's packaged `niri-session` lifecycle intact and avoids a custom display-manager launcher.

The packaged session script already imports the login environment into `systemd --user`, updates D-Bus activation environment, starts and waits for `niri.service`, starts `niri-shutdown.target`, and unsets session variables. Fedora's Niri user service orders itself around `graphical-session-pre.target`, binds to `graphical-session.target`, and brings up `xdg-desktop-autostart.target`; the shutdown target conflicts with graphical session targets. B1 uses that upstream lifecycle rather than duplicating it.

The PolicyKit agent is one user service wanted by `graphical-session.target`, conditioned on `XDG_CURRENT_DESKTOP=niri`, and `PartOf` that target. It therefore starts only in this Niri session and stops when the session target stops; Plasma's session environment does not satisfy the condition. NetworkManager, PipeWire, WirePlumber, `speakersafetyd`, SDDM and other system services are left untouched.

No output name, resolution, refresh rate, scale or Asahi-specific environment variables are guessed before testing on the M1. Xwayland support is package-provided and on-demand. There is no manual Xwayland service, screenshot tool, clipboard utility, shell, or `exec-once` app group in B1.

## Safe installer and rollback

The public interface is the `niri+` CLI. The single repository bootstrap entrypoint is `scripts/bootstrap-niri-plus`; it installs only the CLI and its runtime data, defaults to dry-run, and does not install Niri. After bootstrap, `niri+ install --dry-run` displays the plan. Applying requires root (`sudo niri+ install`) and an exact host match: `ID=fedora-asahi-remix`, `VERSION_ID=44`, `aarch64`. It installs only the explicit package list with weak dependencies disabled, adds uniquely named session/config files, and backs up any files it must replace under `/var/lib/asahi-system/niri-performance/`. Repeated apply is idempotent. It does not edit SDDM configuration, Plasma, boot/kernel/driver files, global services, or existing audio/network configuration.

Rollback restores backed-up files and removes only files created by this installer. By default packages stay installed; `--remove-packages` removes only explicit packages recorded as absent before apply, using `dnf remove --noautoremove`, and retains shared dependencies. Post-install local edits are saved in the rollback state directory before files are restored.

The initial safe command sequence for later Mac use is:

```sh
sudo ./scripts/bootstrap-niri-plus --apply && sudo niri+ install
```

Bootstrap installs the CLI only; the second command installs the Niri session. `niri+ rollback` restores only Niri+ managed files. Uninstall also removes only explicitly tracked packages installed by Niri+, using DNF with `--noautoremove`. The updater remains unavailable until versioned releases, content verification, and a health-check rollback protocol exist. Status and doctor are read-only. No command was run on the Mac in this phase.

### Niri+ command surface

`VERSION` is the single tracked source for the Niri+ application version. The bootstrap installs the Python CLI under `/usr/local/lib/niri-plus`, its data/collector under `/usr/local/share/niri-plus`, and the public executable at `/usr/local/bin/niri+`. It defaults to dry-run and backs up a previously bootstrapped copy before replacing it. It refuses unmanaged destinations.

- `niri+ status`: read-only host/session/package/service summary; optional Quickshell, Gamescope and Steam can be absent without treating that as an installation failure.
- `niri+ install [--dry-run]`: uses the existing strict Fedora Asahi 44/aarch64 installer. Apply requires `sudo niri+ install`; privilege is not retained by a resident process.
- `niri+ rollback`: restores only files recorded by the installer; package removal is opt-in with `--remove-packages`.
- `niri+ uninstall`: removes tracked session files and only packages recorded as newly installed; it does not remove shared dependencies or protected system components.
- `niri+ doctor`: read-only diagnostics, including managed-file checksums and M1-required hardware checks.
- `niri+ benchmark [--runs N] [--output FILE]`: forwards arguments to the existing Phase A collector.
- `niri+ update`: safe unavailable stub. It performs no changes until signed/versioned releases, content validation, a known-good point, and health-check rollback are designed.

## Cloud checks and M1-required validation

Cloud tests cover KDL syntax parsing, desktop-entry shape, shell syntax, package manifest, target refusal logic, idempotency, backup and rollback in a temporary directory. They do not prove Niri option semantics, session-manager discovery, sound, networking, input, suspend/resume, Honeykrisp rendering, or power behavior.

**M1_REQUIRED:** confirm DNF resolves the documented package set from configured Fedora Asahi repositories; confirm the new session appears beside Plasma in SDDM/Plasma Login; boot the Niri session and check internal keyboard/trackpad/touch input, audio output and volume/mute keys, Wi-Fi reconnect, lock/logout, suspend/resume, external display behavior if used, and returning to Plasma recovery. Capture the first Niri baseline only after a fresh login and 2–3 minutes idle with no terminal/browser/application open; use `niri+ benchmark --runs 5`. Quickshell comparison follows separately.
