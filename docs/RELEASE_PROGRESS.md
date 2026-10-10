# Niri+ Release Progress

Updated: 2026-10-09

## Release state

`IMPLEMENTATION_IN_PROGRESS / CLOUD_VALIDATED_PARTIAL`. This is a development
checkpoint, not a release candidate and not a claim of M1 validation. Production
branches and the production Quickshell pin are unchanged.

## Repository state

| Repository | Work branch | Base | Preserved checkpoint | Current work |
|---|---|---|---|---|
| `LQ13ofc/asahi-system` | `integration/niri-plus-rc` | `main` at `5339e348` | `da85d0e` | HEAD `7028f85`; Niri core is independently installable, optional QuickShell candidate install/rollback is transaction-tested |
| `LQ13ofc/quickshell-` | `integration/settings-center-rc` | `master` at `4191e9f` | `435ca33` | HEAD `79b093e`; optional Niri+ bridge, independent brightness failure state, and deferred Control Center action-row layout |

Production `main` still uses the known-good Quickshell pin
`55e92880d0aff75d235f283c839ec0990eaa9e17`. The integration branch now pins
its RC candidate separately to `79b093e60f72a2e31f29f819ca2e13d3a1f296e5`; this
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
205 unit tests pass at HEAD `7028f85`; Actions run `38002886855` passed for
the new transaction tests and progress documentation.
QuickShell
has 39 passing Python tests, 59 loadable QML files, all reference scenes
rendering locally, and qmllint exit 0 with 192 classified warnings. Hosted run
38000517181 passed for QuickShell code HEAD b444860: Python suite, QML load,
full reference render, and lint. It includes a regression proving the UI reports
an unavailable optional Niri+ brightness backend without remaining busy. HEAD
79b093e updates only the documented warning counts; Actions runs 38000692795
and 38000697379 passed at that exact head.
The hosted ControlCenter SIGSEGV was resolved by deferring responsive Row anchor
changes until component construction finishes and cancelling a pending relayout
with the component's one-shot timer. The GDB stack had identified Qt Quick
positioner/binding re-entry; no rendering scene is skipped or downgraded.
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
  candidate integration pin and gitlink now point to the exact CI-tested
  QuickShell RC SHA `79b093e60f72a2e31f29f819ca2e13d3a1f296e5`. Production's
  known-good pin remains unchanged.
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
| Niri gaps, border, column layout, launcher, terminal, close-window, focus navigation, keyboard repeat, and per-app window rules | `niri_plus/niri_settings.py`, Niri Settings bridge | Implemented with allowlisted user-owned KDL; old state receives defaults for new fields | KDL parsing, allowlist/collision, persistence/rollback, migration, and bridge tests; actual compositor validation/reload is `M1_REQUIRED` |
| Trackpad/input configuration | `niri_settings.py`, Settings Center Input page | Implemented with explicit opt-in and collision checks | KDL/parser and preservation tests pass; real M1 device behavior remains `M1_REQUIRED` |
| Monitor mode/scale/brightness device discovery | No hardware-specific setting is claimed | Incomplete / `M1_REQUIRED` for output names and device validation | Cloud lacks Apple display/backlight hardware |
| Settings search, keyboard access, import/export/reset, doctor/status | QuickShell Settings Center and existing Niri+ CLI | Implemented in candidate | Unit/harness coverage and offscreen rendering |
| UI efficiency profiles | Shared stats service and lazy panel content | Implemented as interface-only profiles | Regression tests and Cloud parser experiment; no M1 performance claim |
| Independent repositories with optional integration | `niri+ install [--with-quickshell]`, QuickShell CLI fallback | Implemented in candidate | Default system install and shell operation do not require the other repository; 201 system tests and 39 visual tests; M1 session behavior remains required |
| Full product RC, installer/update/rollback integration | Candidate branches and existing asahi-system installer | Cloud integration scenarios pass; RC remains in progress | Reversible snapshot install is tested for bootstrap/apply/verification failures; real candidate 79 was staged in a temporary root and all 188 manifest files and pin metadata matched |
| Cause of observed ~4 GiB RAM | Memory collector/diagnostic tooling | Unknown | Requires longitudinal M1 captures; no cause inferred |

## Validation so far

- asahi-system: `compileall`, baseline validator, and full unittest discovery;
  **205 tests passed**. New cases exercise core-only install, preservation of
  production pin `55e92880d0aff75d235f283c839ec0990eaa9e17`, file modes, full
  candidate staging and rollback on bootstrap/apply/verification failure.
- asahi-system: an isolated local Git mirror resolved Quickshell commit
  `79b093e60f72a2e31f29f819ca2e13d3a1f296e5`; bootstrap installed the actual
  candidate into a temporary `/usr/local`-shaped tree. All 188 runtime files
  matched the snapshot manifest, and installed version/pin metadata matched.
  No host system paths were written.
- quickshell-: `compileall`, full unittest discovery; **39 tests passed**.
- quickshell-: QML load: **59 files, 0 errors, 0 warnings**.
- quickshell-: settings persistence, multi-screen, keyboard interaction,
  brightness, Control Center radios, touchpad preferences, and Niri bridge
  harnesses and isolated action-policy tests passed.
- quickshell-: full offscreen render passed for all reference scenes,
  including Settings Center categories and Control Center.
- quickshell-: `pyside6-qmllint` returned **0**, reporting 192 classified
  warnings (161 unqualified, 20 missing-property, 6 import, 3 unresolved-type,
  2 unused-imports). These are not silently treated as proof of a runtime bug;
  the classification is in `docs/validacao-qml.md`.

## Open work / next executable action

1. Continue the requirement matrix for remaining Cloud-implementable product
   behavior while keeping visual state in Quickshell and system lifecycle,
   installation and Niri-owned options in asahi-system. Monitor-specific output
   discovery and physical device behavior remain `M1_REQUIRED`.
2. Keep PRs #8/#19 separate and draft. Do not merge to `main`/`master` or change
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
- Current candidate-only checkpoints: asahi `7028f85`; quickshell `79b093e`,
  followed by merge `5305bd2`. Candidate implementation commits: asahi
  `6073e5e`, `4b2caf0`; quickshell `1ecac16`, `ee0a9b8`. Draft integration PRs: asahi
  #19 and quickshell #8. None has been merged into production.

## Release Candidate installer gate — 2026-10-10

State: `IMPLEMENTATION_IN_PROGRESS / CLOUD_VALIDATED_PARTIAL`. Work remains on
the existing asahi-system PR #19 branch; `main` and the production Quickshell
pin were not changed. At the start of this step PR #19 was open at
`83bd12dbf9256bae9a8b6a57c7349e06830fc625`, mergeable, with CI successful.
`origin/main` still pins Quickshell `55e92880d0aff75d235f283c839ec0990eaa9e17`;
the candidate lock and gitlink both pin `79b093e60f72a2e31f29f819ca2e13d3a1f296e5`.

Implemented in the Cloud candidate branch (code commit
`95af802ba07501555675d30badf46d82a3157ea2`; M1 procedure/progress
documentation is pending a separate commit):

- The ordinary `niri+ install` path remains unchanged: it validates a clean
  source checkout on `main`, fetches only `refs/heads/main`, and consumes that
  source commit's lock. It does not accept an RC ref or candidate SHA.
- The explicit `scripts/niri-plus-rc-bootstrap` path requires the full PR #19
  SHA and confirms the current pull-request head is exactly that SHA. Its
  root-side Python trampoline reads the helper once, verifies the expected
  SHA-256, and executes those same in-memory bytes. The helper then confirms
  those bytes and `source_update.py` are the Git blobs in the exact commit.
- Candidate snapshots are materialized only from verified Git objects into a
  root-owned, write-protected temporary tree. The resolver validates canonical
  origins, the PR ref/SHA, the gitlink, lock, Quickshell origin/SHA, manifest
  digest, every blob hash, file mode, and snapshot path. A dirty or different
  local worktree cannot supply privileged code.
- Before applying, the helper validates and atomically saves the currently
  installed production system/Quickshell commits in a root-owned `0600` record.
  It requires a coherent production bootstrap state, lock, and runtime marker.
  `--restore-known-good` resolves those exact commits, not a future `main` or
  mutable branch. Existing install transactions restore managed files/state
  when bootstrap, apply, verification, or package finalization fails.
- `docs/release-candidate-m1.md` documents preparation, the hash-checked
  in-memory bootstrap, candidate dry-run/apply, SDDM/Plasma recovery, exact
  known-good restore, diagnostics, and M1-only checks. No physical test ran.

Cloud validation after implementation: `test_release_gate_security.py` — 34
tests passed, including full candidate-channel bootstrap/apply/verification
failure rollback and exact known-good resolution. Full `unittest discover`:
215 tests passed. `compileall`, `scripts/validate_baseline.py`, RC bootstrap
`--help`, and `git diff --check` passed. Expected refusal/failure messages in
the suite are test fixtures, not failing tests. PR #19 CI run `38009784374`
passed on head `232e18ee2bec810b793c40ca806bc456d2676e7e`.

The procedure is now ready for its first controlled M1 validation; this Cloud
work did not execute it. PR #19 remains open/draft and the known-good pin on
`main` remains unchanged. The next Cloud action is to retain the RC and M1
results in the release matrix, then continue any Cloud-implementable product
work without promoting or merging the candidate. The final PR head and helper
SHA-256 must be reported with the commands, not copied here in a self-referential
form. Keep PRs #19/#8 unmerged and retain `main`'s stable Quickshell pin.

## Incremento de atalhos do Settings Center — Cloud

Trabalho em branches separadas, baseadas nos candidatos PR #19 (`002131b1…`)
e PR #8 (`79b093e6…`); os heads candidatos e o pin estável não foram alterados.
O backend e seus testes estão no branch `feature/niri-close-window-shortcut`,
commit `672c70e` (implementação) e `c3bda92` (progresso); PR #20 está aberto
como draft sobre `integration/niri-plus-rc`. A integração visual está no branch
`feature/settings-close-window-shortcut`, commit `7eba119`, com PR #9 draft
sobre `integration/settings-center-rc`.
O backend `niri-settings` agora gera atalhos configuráveis para fechar a janela
focada e navegar foco horizontal/vertical. Os valores têm allowlist fechada,
colisão entre as sete ações é rejeitada, KDL é validado antes da gravação e
estados anteriores recebem defaults sem perda das opções existentes. O
Settings Center expõe seletores para essas ações, detecta suporte a partir do
status do backend e desativa apenas controles novos quando o `niri+` for antigo.
Quickshell continua independente: os demais painéis funcionam sem a CLI, e
aplicações de configurações antigas omitem os campos que o backend antigo não
conhece.

Validação final do incremento: asahi `compileall`, 219 testes `unittest` e
`scripts/validate_baseline.py` passaram. Quickshell `compileall`, 40 testes
`unittest`, load de 59 QML (zero erros/avisos tardios), render completo de todas
as cenas (99 imagens, exit 0) e render específico da página de atalhos passaram.
A captura foi inspecionada; `SettingRow` recebeu espaçamento e composição em
pilha para evitar colisão visual dos grupos maiores. O qmllint segue exit 0 com
192 avisos classificados no relatório existente; nenhuma correção cega foi
feita. A sintaxe KDL passa no parser Cloud; validar as ações com `niri validate`
Fedora e no runtime continua `M1_REQUIRED`.

PR #20: `https://github.com/LQ13ofc/asahi-system/pull/20`, draft na branch
`feature/niri-close-window-shortcut`; CI manual `38051488356` passou no código
da feature (workflow pull_request do repositório só observa base `main`, então
PR de feature encadeado ao RC não recebe check automático). PR #9 do quickshell-:
`https://github.com/LQ13ofc/quickshell-/pull/9`, draft, head
`7eba119f1dea6572c597246563f3794a68d986f0`; CI `38051456317` passou.
Os heads dos pais continuam PR #19 `002131b1e86c4da58eeca226d817b68305ca5c5b`
e PR #8 `79b093e60f72a2e31f29f819ca2e13d3a1f296e5`.

Próxima ação executável: continuar as opções funcionais pendentes do Settings
Center em novas branches baseadas no RC. A descoberta de nome/mode/scale de
outputs e validação de backlight permanecem `M1_REQUIRED`; nenhuma UI deverá
simular essas opções no Cloud. PRs #19/#8 permanecem exatamente nos commits RC,
sem merge ou mudança no pin de produção.
