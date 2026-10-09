# Niri+ Release Progress

Updated: 2026-10-09

## Release state

`IMPLEMENTATION_IN_PROGRESS / CLOUD_VALIDATED_PARTIAL`. This is a development
checkpoint, not a release candidate and not a claim of M1 validation. Production
branches and the production Quickshell pin are unchanged.

## Repository state

| Repository | Work branch | Base | Preserved checkpoint | Current work |
|---|---|---|---|---|
| `LQ13ofc/asahi-system` | `integration/niri-plus-rc` | `main` at `5339e348` | `da85d0e` | HEAD `ce45489`; Niri core is independently installable, QuickShell integration is opt-in; guarded touchpad settings |
| `LQ13ofc/quickshell-` | `integration/settings-center-rc` | `master` at `4191e9f` | `435ca33` | HEAD `1ffdcd6`; optional Niri+ bridge, touchpad controls, and deferred Control Center action-row layout |

Production `main` still uses the known-good Quickshell pin
`55e92880d0aff75d235f283c839ec0990eaa9e17`. The integration branch now pins
its RC candidate separately to `ee0a9b8c7836b14485efaf7aca33849d0210570d`; this
does not change the production pin.

### Repository independence contract

The repositories are peers, not runtime dependencies. The default
`sudo niri+ install` installs Niri/system management without fetching or
installing Quickshell. Users opt into the pinned visual integration with
`sudo niri+ install --with-quickshell`. Quickshell owns its QML and can launch
through `qs` without the Niri+ CLI; Niri-specific actions use the CLI as an
optional backend and report its absence. The integration branch's gitlink and
lock remain candidate-only; production pin `55e92880d0aff75d235f283c839ec0990eaa9e17`
is unchanged.

Current Cloud validation: asahi-system compileall, baseline validation, and all
201 unit tests pass; Actions run 37999249999 passed at HEAD 3204742 (subsequent change ce45489 only updates this progress record).
QuickShell has 38 passing Python tests, 59 loadable QML files, all reference
scenes rendering locally, and qmllint exit 0 with 191 classified warnings.
Hosted run 37999480027 reproduced the ControlCenter creation crash. Its GDB
stack points to Qt Quick row positioning while a property binding updates. The
first geometry change at 9e2adc4 did not resolve hosted SIGSEGV in run
37999805430. HEAD 1ffdcd6 now defers the responsive action-row anchor update
until after Row completion and uses Qt-managed implicit geometry; the targeted
Control Center scene passes locally. Hosted runs for 1ffdcd6 are pending; lint
runs after render failures and the GDB diagnostic remains conditional.

## Completed in this continuation

- Preserved both pre-existing worktrees with checkpoint branches and WIP commits
  before synchronization.
- Merged current Quickshell `master` into `integration/settings-center-rc`;
  no production branch was changed.
- Hardened `niri_settings.py`: no chmod of existing XDG directories, path and
  ownership checks, no-follow reads, atomic writes, bounded JSON input, and
  recovery of managed files when apply fails.
- Added the unprivileged `niri+ niri-settings status|apply|rollback` path and
  connected Settings Center Niri, launcher, and keyboard controls over a
  validated stdin protocol. Touchpad controls now use the official Niri KDL
  options only when the included config tree proves there is no separate
  touchpad block; unverifiable absolute includes are refused safely.
- Added the settings KDL include and regression tests for backend safety,
  rollback, session syntax, CLI dispatch, and the QML-to-CLI bridge.
- Added allowlisted, bounded per-app window rules for exact `app-id`, workspace,
  and floating behavior; old settings states without the new list remain
  readable, and the rules participate in atomic apply/rollback.
- Added the installed split-library import test for `niri_settings.py` so the
  installed `/usr/local/lib/niri-plus` layout is exercised without host writes.
- Added the Settings Center per-app rule editor and validated rule payloads;
  candidate integration pin and gitlink now point to the exact QuickShell RC SHA.
- Added Python compilation to the asahi-system GitHub Actions validation job.
- Made the Quickshell package, checkout, service and pin an explicit optional
  integration: core install works without private repo access, and core-only
  upgrades preserve any installed visual runtime without refreshing it.
- Added regression coverage for default/explicit install modes, preserved
  visual runtime and pin state, and independent host readiness/status/doctor.
- In QuickShell, wide Settings Center choice groups now wrap below their
  labels, and Control Center uses a shared themed slider rather than Qt's
  platform-default control. Removed its now-unused Controls import.

## Requirement matrix

| Requirement | Implementation | State | Evidence / blocker |
|---|---|---|---|
| Persistent visual preferences and schema migration | QuickShell `config/Preferences.qml`, `SettingsPanel.qml` | Implemented; revalidate integrated branch | QuickShell unit/harness suite |
| Appearance controls and preview | QuickShell `SettingsPanel.qml`, `config/Theme.qml` | Implemented in candidate | Offscreen render pending completion |
| Bar modules, order, position, per-monitor overrides | QuickShell `config/Preferences.qml`, `Bar.qml`, Settings Center | Implemented | Existing multi-screen and settings tests |
| Panel behavior, notification privacy/history/DND | QuickShell panel/services and Settings Center | Implemented | Unit and offscreen checks |
| Audio, Wi-Fi, Bluetooth, brightness | Existing PipeWire/NetworkManager/BlueZ services and Control Center | Implemented via existing backends | Cloud stubs only; hardware behavior is `M1_REQUIRED` |
| Niri gaps, border, column layout, launcher, keyboard repeat, and per-app window rules | `niri_plus/niri_settings.py`, Niri Settings bridge | Implemented with allowlisted user-owned KDL; legacy state without rules migrates in memory | KDL parsing, rule validation, persistence/rollback, migration, and bridge tests; actual compositor reload is `M1_REQUIRED` |
| Trackpad/input configuration | `niri_settings.py`, Settings Center Input page | Implemented with explicit opt-in and collision checks | KDL/parser and preservation tests pass; real M1 device behavior remains `M1_REQUIRED` |
| Monitor mode/scale/brightness device discovery | No hardware-specific setting is claimed | Incomplete / `M1_REQUIRED` for output names and device validation | Cloud lacks Apple display/backlight hardware |
| Settings search, keyboard access, import/export/reset, doctor/status | QuickShell Settings Center and existing Niri+ CLI | Implemented in candidate | Unit/harness coverage; integrated render still pending |
| UI efficiency profiles | Shared stats service and lazy panel content | Implemented as interface-only profiles | Regression tests and Cloud parser experiment; no M1 performance claim |
| Independent repositories with optional integration | `niri+ install [--with-quickshell]`, QuickShell CLI fallback | Implemented in candidate | Default system install and shell operation do not require the other repository; 201 system tests and 37 visual tests; M1 session behavior remains required |
| Full product RC, installer/update/rollback integration | Candidate branches and existing asahi-system installer | In progress | Combined integration, failure-path tests, screenshots, and CI still required |
| Cause of observed ~4 GiB RAM | Memory collector/diagnostic tooling | Unknown | Requires longitudinal M1 captures; no cause inferred |

## Validation so far

- asahi-system: `compileall`, baseline validator, and full unittest discovery;
  **201 tests passed**.
- quickshell-: `compileall`, full unittest discovery; **37 tests passed**.
- quickshell-: QML load: **59 files, 0 errors, 0 warnings**.
- quickshell-: settings persistence, multi-screen, keyboard interaction,
  brightness, Control Center radios, touchpad preferences, and Niri bridge
  harnesses and isolated action-policy tests passed.
- quickshell-: full offscreen render passed for all 71 reference scenes,
  including Settings Center categories and Control Center.
- quickshell-: `pyside6-qmllint` returned **0**, reporting 191 classified
  warnings (160 unqualified, 20 missing-property, 6 import, 3 unresolved-type,
  2 unused-imports). These are not silently treated as proof of a runtime bug;
  the classification is in `docs/validacao-qml.md`.

## Open work / next executable action

1. Review the hosted full-scene result for QuickShell HEAD 1ffdcd6. If deferred
   Row re-anchoring passes, update PR #8 evidence; if it fails, use the GDB
   stack to continue isolating which control row triggers Qt Quick completion.
2. Asahi-system core independence and optional QuickShell integration are covered
   at HEAD 3204742; run 37999249999 passed.
3. Keep PRs #8/#19 separate and draft. Do not merge to `main`/`master` or change
   the production pin.

## M1 release gate

Still required on real Fedora Asahi: Niri accepts the generated settings KDL;
Wayland/session lifecycle and PolicyKit/audio/radio controls work; output and
backlight discovery are correct; suspend/resume is safe; and performance,
resident memory, and multi-monitor rendering are measured. The Cloud evidence
above is not a substitute for these results.

## PR and commit references

- Existing asahi-system PR #17: `ace70af1` (Niri+ 0.1.10 candidate).
- Existing asahi-system PR #18: `c4454c7b` (C0/C1 comparison).
- Existing quickshell- PR #7: `9ef87cf` (initial Settings Center).
- Existing quickshell- PR #6: `f0fb3ec` (Cloud CI).
- Existing quickshell- PRs #4/#5: parser and residency work.
- Current candidate-only checkpoints: asahi `da85d0e`; quickshell `435ca33`,
  followed by merge `5305bd2`. Candidate implementation commits: asahi
  `6073e5e`, `4b2caf0`; quickshell `1ecac16`, `ee0a9b8`. Draft integration PRs: asahi
  #19 and quickshell #8. None has been merged into production.
