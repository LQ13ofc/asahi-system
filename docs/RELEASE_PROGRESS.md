# Niri+ Release Progress

Updated: 2026-10-10

## Release state

`CODE_COMPLETE / CLOUD_VALIDATED / M1_RELEASE_GATE_PENDING` for the cumulative
Cloud candidate on asahi-system PR #22 and quickshell- PR #15. This means the
Cloud-implementable settings, administration, and Gamescope-only launch
contract are implemented and covered by repository tests. It does not claim
that Gamescope, Honeykrisp, input devices, or runtime performance work on the
M1. No PR is merged, no hardware test was run, and production pins are intact.

## Repository state

| Repository | Work branch / PR | Current candidate | Preserved pin/checkpoint |
|---|---|---|---|
| `LQ13ofc/asahi-system` | `feature/quickshell-pr15-candidate` / #22 draft | code head `984b8c97c38853b5fcf0a8a621b32912138e2e55`; Gamescope-only Niri Gaming Mode | production pin `55e92880`; PR #19 gate `002131b1`; PR #21 candidate `b440538d` |
| `LQ13ofc/quickshell-` | `feature/accessible-control-center-sliders` / #15 draft | HEAD `0dc55d26f4dd2d281044ce6d4a043f5c751298df` | PR #8 physical-gate checkpoint `79b093e60f72a2e31f29f819ca2e13d3a1f296e5` |

The asahi-system PR #22 gitlink and lock both pin QuickShell PR #15 at
`0dc55d26f4dd2d281044ce6d4a043f5c751298df`. PR #19 / QuickShell PR #8 and
PR #21 / QuickShell PR #14 remain distinct historical gate references. The
production `main` pin is still `55e92880d0aff75d235f283c839ec0990eaa9e17`.

## Current candidate and evidence

- Settings and Control Center behavior stays in Quickshell; system setup,
  lifecycle, Niri KDL, diagnostics, benchmark, and Gamescope orchestration stay
  in asahi-system. Either repository remains usable without the other.
- PR #15 adds accessible Control Center sliders and makes Efficiency/reduced
  motion pause continuous activity indicators. Its Cloud checks passed: 43
  Python tests, 59 QML files load, full offscreen render, and qmllint exit 0
  with 192 classified warnings (run `38055105197`).
- PR #22 now exposes `niri+ gaming status|steam|run`. Gaming Mode launches only
  through Gamescope inside the current user's active Niri session; it uses no
  experimental flags and never falls back to direct Niri. Gamescope/Honeykrisp
  compatibility remains `M1_REQUIRED`. Status/doctor include read-only
  readiness; stale and symlinked IPC sockets are rejected, and explicit local
  executable paths work.
- Current asahi-system Cloud checks: compileall, baseline validator, and 232
  unittest cases pass. The 11 Gaming Mode cases cover live/stale IPC sockets,
  symlink rejection, read-only status, exact argv, executable paths, root/non-
  Niri rejection, Gamescope absence or start failure, and CLI dispatch. This
  Cloud host reported Niri inactive and Gamescope/Steam unavailable; no game
  was launched.
- PR #22's prior code head `556603abd14bfd758c5957257457bbc859d7d3fc` passed
  Asahi Actions run `38055455933`; the earlier Gaming CLI head
  `1e841e99b626ff27afd0e8b324a60d2f6b9c43f2` passed run `38056017599`. Actions
  run `38056128364` passed on code head
  `984b8c97c38853b5fcf0a8a621b32912138e2e55`; run `38056189794` passed on
  progress-document head `e267722e7dafc9dcf82d7e9f3c8d082c784798cc`.

### Remaining release gate

Cloud-implementable settings, UI behavior, profiles, lifecycle, persistence,
diagnostics, benchmark tooling, independent-repository behavior, candidate
install/rollback, and the no-fallback Gamescope contract are implemented and
covered by the cumulative test suites. Hardware output/backlight discovery,
Niri runtime application of generated KDL, Wayland services/devices, suspend,
Honeykrisp/Gamescope behavior, and measured CPU/RAM/frametime remain
`M1_REQUIRED`. Cloud validation does not establish those claims.

Next release action: the Cloud candidate is ready for review; the remaining
gate is the documented Fedora Asahi M1 validation after the candidate is
accepted for that gate. Until those measurements exist, Gamescope/Honeykrisp
compatibility and hardware performance remain unverified. Any new CI or review
finding that can be reproduced in Cloud returns to implementation before the
hardware gate.

Historical PR #19 first-gate checkpoint: production `main` used the known-good
Quickshell pin `55e92880d0aff75d235f283c839ec0990eaa9e17`, while that separate
candidate pinned `79b093e60f72a2e31f29f819ca2e13d3a1f296e5`. The current
cumulative pin is recorded at the top of this document.

### Repository independence contract

The repositories are peers, not runtime dependencies. The default
`sudo niri+ install` installs Niri/system management without fetching or
installing Quickshell. Users opt into the pinned visual integration with
`sudo niri+ install --with-quickshell`. Quickshell owns its QML and can launch
through `qs` without the Niri+ CLI; Niri-specific actions use the CLI as an
optional backend and report its absence. The integration branch's gitlink and
lock remain candidate-only; production pin `55e92880d0aff75d235f283c839ec0990eaa9e17`
is unchanged.

Historical checkpoint below: the former integration branch recorded an
earlier 205-test result at HEAD `7028f85`; current cumulative evidence appears
in the 2026-10-10 validation section below.
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
## Historical continuation record (prior checkpoint)

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

## Requirement matrix (prior checkpoint; current candidate evidence above)

| Requirement | Implementation | State | Evidence / blocker |
|---|---|---|---|
| Persistent visual preferences and schema migration | QuickShell `config/Preferences.qml`, `SettingsPanel.qml` | Implemented; revalidate integrated branch | QuickShell unit/harness suite |
| Appearance controls and preview | QuickShell `SettingsPanel.qml`, `config/Theme.qml` | Implemented in candidate | Offscreen render pending completion |
| Bar modules, order, position, per-monitor overrides | QuickShell `config/Preferences.qml`, `Bar.qml`, Settings Center | Implemented | Existing multi-screen and settings tests |
| Panel behavior, notification privacy/history/DND | QuickShell panel/services and Settings Center | Implemented | Unit and offscreen checks |
| Audio, Wi-Fi, Bluetooth, brightness | Existing PipeWire/NetworkManager/BlueZ services and Control Center | Implemented via existing backends | Cloud stubs only; hardware behavior is `M1_REQUIRED` |
| Niri gaps, border, column layout, launcher, terminal, close-window, focus/move columns, focus windows, floating toggle, keyboard repeat, and per-app rules | `niri_plus/niri_settings.py`, Niri Settings bridge | Implemented with allowlisted user-owned KDL; old state receives defaults for new fields | KDL parsing, allowlist/collision, persistence/rollback, migration, and bridge tests; compositor validation/reload is `M1_REQUIRED` |
| Trackpad/input configuration | `niri_settings.py`, Settings Center Input page | Implemented with explicit opt-in and collision checks | KDL/parser and preservation tests pass; real M1 device behavior remains `M1_REQUIRED` |
| Monitor mode/scale/brightness device discovery | No hardware-specific setting is claimed | Incomplete / `M1_REQUIRED` for output names and device validation | Cloud lacks Apple display/backlight hardware |
| Settings search, keyboard access, import/export/reset, doctor/status | QuickShell Settings Center and existing Niri+ CLI | Implemented in candidate | Unit/harness coverage and offscreen rendering |
| UI efficiency profiles | Quickshell profile state and lazy visual components | Implemented as interface behavior, not system power tuning | Cloud profile/lifecycle tests; no M1 performance claim |
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

## Historical open work / next executable action (prior checkpoint)

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
commits `672c70e` e `415dbfb`; PR #20 está aberto como draft sobre
`integration/niri-plus-rc`. A integração visual está no branch
`feature/settings-close-window-shortcut`, commits `7eba119` e `a94ce4e`, com PR
#9 draft sobre `integration/settings-center-rc`.
O backend `niri-settings` gera atalhos para launcher, terminal, fechar janela,
foco/movimento de colunas e janelas, e alternar janela flutuante. Os valores têm
allowlist fechada, colisão entre as dez ações é rejeitada, KDL é validado antes
da gravação e estados anteriores recebem defaults sem perda das opções
existentes. O Settings Center expõe seletores para essas ações, detecta suporte
a partir do status do backend e desativa apenas controles novos quando o
`niri+` for antigo.
Quickshell continua independente: os demais painéis funcionam sem a CLI, e
aplicações de configurações antigas omitem os campos que o backend antigo não
conhece.

Validação final do incremento: asahi `compileall`, 220 testes `unittest` e
`scripts/validate_baseline.py` passaram. Quickshell `compileall`, 40 testes
`unittest`, load de 59 QML (zero erros/avisos tardios), render completo de todas
as cenas (99 imagens, exit 0) e render específico da página de atalhos passaram.
A captura foi inspecionada; `SettingRow` recebeu espaçamento e composição em
pilha para evitar colisão visual dos grupos maiores. O qmllint segue exit 0 com
192 avisos classificados no relatório existente; nenhuma correção cega foi
feita. A sintaxe KDL passa no parser Cloud; validar as ações com `niri validate`
Fedora e no runtime continua `M1_REQUIRED`.

PR #20: `https://github.com/LQ13ofc/asahi-system/pull/20`, draft na branch
`feature/niri-close-window-shortcut`; CI manual `38051991004` passou no código
atual. O workflow pull_request do repo só observa base `main`, então o PR
encadeado ao RC não recebe check automático. PR #9 do quickshell-:
`https://github.com/LQ13ofc/quickshell-/pull/9`, draft na branch
`feature/settings-close-window-shortcut`; CI push `38051977771` e pull_request
`38051981008` passaram no commit `a94ce4e`.
Os heads dos pais continuam PR #19 `002131b1e86c4da58eeca226d817b68305ca5c5b`
e PR #8 `79b093e60f72a2e31f29f819ca2e13d3a1f296e5`.

Quickshell PR #10 draft: branch `feature/interface-profile-visualizer`, head
`87ee934f44dca926afe59339868f2b92b04987ca`, base
`integration/settings-center-rc`. Além dos perfis Efficiency/Balanced/Visual,
o branch corrige uma corrida já presente no teste netctl: leitura de
`/proc/PID/stat` após `exists()` podia perder o processo entre chamadas; agora
lê um snapshot e compara starttime para não confundir PID reutilizado. O check
push passa no Cloud local; o CI GitHub foi relançado nesse head após a correção.

Quickshell PR #11 draft: branch `feature/music-visualizer-preference`, head
`dc32bc4dfcf6b0729a4609e4a80ddb4bcd345fab`, base PR #10. Adiciona o toggle
persistente de Cava em Settings > Painéis e schema v4; arquivos v1/v2/v3
continuam válidos com o visualizador habilitado por default, gravando v4 na
próxima edição. Desligar a opção ou usar Efficiency descarrega o visualizador;
ligá-la em Balanced/Visual cria uma instância com os valores do perfil. Testes
validam persistência após reinício, reset, import/export, migração v3,
rejeição de tipos inválidos e lifecycle. Não requer nem invoca Niri+.

Validação combinada no Cloud: compileall, 40 testes `unittest`, load de 59 QML
sem erros/avisos tardios, render completo de todas as cenas e qmllint exit 0
com 192 avisos classificados. A captura de Settings > Painéis foi inspecionada
e não há colisão de layout. CI dos PRs #10/#11 passou nos heads acima. PR #20 teve validação manual em `7fb1c1c` aprovada
(`38052764189`). O estado ainda não é CODE_COMPLETE; consumo real e lifecycle
de Cava continuam `M1_REQUIRED`.

## Lifecycle de clima opcional — Cloud

Quickshell PR #12, branch `feature/weather-module-lifecycle`, commit
`d8663f5`, base `integration/settings-center-rc`: o serviço de clima só busca
ou agenda refresh se o módulo estiver visível em pelo menos um monitor. Ao
ocultar em todos os monitores, cancela o processo em curso e timer; ao reabrir,
reusa cache fresco e só faz uma nova busca se o cache venceu. O estado inicial
oculto agora também reporta `off`. O harness verifica visibilidade por monitor,
cache fresco/vencido, quantidade de requisições e cancelamento. Isso reduz
trabalho da integração visual; não altera política de sistema nem prova ganho de
CPU/RAM no M1.

Cloud após a mudança: compileall e 40 testes `unittest` passaram; load de 59
QML passou sem erros ou avisos tardios; render de todas as cenas passou;
settings-check, multiscreen, interação por teclado e checks dos backends
auxiliares passaram. `pyside6-qmllint` retorna 0 com 192 warnings já
classificados em `docs/validacao-qml.md`. CI GitHub de PRs #10, #11 e #12
passou nos heads atuais. Os PRs e pin de produção permanecem sem
merge/alteração.

A agenda foi auditada no mesmo ciclo: `Agenda.qml` não tem timer/processo; a
busca começa ao carregar `CalendarPanel`, cujo conteúdo vive sob o `LazyLoader`
do painel, e o timer local é apenas watchdog da busca. Não havia polling
ocioso a remover. Um teste de contrato agora protege esse lifecycle.

Quickshell PR #13 draft: branch `feature/brightness-monitor-demand`, head
`2282a375a871fc718c94c5993e8c81729e2bfb9f`, base
`integration/settings-center-rc`. `udevadm monitor` e a busca inicial do
backlight agora só existem enquanto o indicador de brilho está visível em pelo
menos um monitor ou o Control Center está vivo. Os consumidores sobrepostos
compartilham um único watcher; fechar/ocultar o último para processos e retries.
O painel pede refresh do sysfs ao assumir consumo. Testes cobrem nenhum
consumidor, início em cada rota, ausência de duplicata, manter o watcher quando
um dos dois consumidores fecha e parar/reiniciar pelo último consumidor.

Cloud no head do PR #13: compileall, 41 testes `unittest`, 59 QML sem erros ou
avisos tardios, render completo, lint com os mesmos 192 avisos classificados,
settings-check, multiscreen, interação por teclado, bridge Niri, brilho com e
sem CLI e Control Center power passaram. CI dos PRs #10–#13 passou. O watcher
permanece event-driven; isso valida ciclo de vida no harness, não PSS/CPU,
atualização por teclas físicas nem estado do sysfs no M1 (`M1_REQUIRED`).

Quickshell PR #14 draft: branch `integration/cloud-settings-rc-20261010`, head
`b440538d342822eac6cb046f0c0dd5e96212d6f2`, base `integration/settings-center-rc`.
É um candidato integrado sobre o PR #8 imutável (`79b093e6…`), reunindo os
heads/commits dos PRs #10–#13. Conflitos de harness e matriz de Settings foram
resolvidos preservando tanto perfis/visualizador quanto clima/brightness. A
suíte combinada passou: 43 testes `unittest`, compileall, 59 QML sem erros,
render de todas as cenas, todos os comandos harness e qmllint exit 0 (192 avisos
classificados em 126 arquivos). Ambos os checks CI do PR #14 passaram. Nenhum
branch principal, pin de produção ou primeiro gate foi alterado.

## Candidato Cloud seguinte — Quickshell PR #14 + Niri+ PR #21

Branch Asahi `feature/quickshell-pr14-candidate`, commit de implementação
`abe940e1e70c7491ffd4252baefc0cc12fa2a3f5`, PR #21 draft baseado no PR #20.
Esse branch deixa intactos o PR #19 (`002131b1…`), seu procedimento M1 e o pin
de produção. O gitlink `external/quickshell` e `integration/quickshell.lock.json`
fixam exatamente o candidato integrado do PR #14:
`b440538d342822eac6cb046f0c0dd5e96212d6f2`.

Para que o opt-in experimental continue coerente com essa nova combinação, o
resolver/bootstrap deste branch verifica PR #21 e o pin Quickshell b440; o
fluxo normal continua buscando `main`. O documento separado
`docs/release-candidate-m1-next.md` usa o SHA da cabeça atual de PR #21, valida
gitlink/lock e deriva o checksum do bootstrap do blob desse commit. O documento
original `release-candidate-m1.md` permanece intocado para o primeiro gate
PR #19 / Quickshell 79b.

Cloud neste branch: baseline validator e compileall passaram; 221 testes
`unittest` passaram, incluindo 34 verificações de snapshot/instalação/rollback
e um contrato que mantém distintos os pinos dos procedimentos PR #19 e #21.
O workflow manual `Validate baseline` passou no commit de implementação
`abe940e` (run `38054567953`) e novamente no head `31a3fd2`
(run `38054641614`). Nenhuma alteração no host ou teste M1.

Quickshell avançou no PR #15 draft, sobre o PR #14 sem tocar em master ou no
pin do candidato: head `0dc55d26f4dd2d281044ce6d4a043f5c751298df`. Sliders do
Control Center agora têm nome/role acessíveis, entram na ordem de Tab e aceitam
setas; Efficiency/reduced motion pausa os indicadores animados de atividade e
conexão sem esconder texto de estado. No Cloud passaram compileall, 43 testes,
load de 59 QML, render completo e qmllint código 0 com 192 avisos classificados.
O workflow do PR #15 passou (run `38055105197`).

O PR #22 é agora o candidato cumulativo draft sobre `main`, mantendo PRs #19–#21
abertos e sem merge. Ele fixa o commit Quickshell acima; o bootstrap experimental
confere PR #22 e o fluxo de produção continua em `main`. O procedimento
`docs/release-candidate-m1-pr22.md` é separado e usa paths próprios. No commit de
código `36d5aa0`, passaram compileall, 221 testes, baseline validator, 34 testes
de segurança do release gate e 22 testes da sessão Niri. Nenhum teste foi feito
no host. O PR foi retargetado para `main` para validar o diff cumulativo; o
workflow Asahi para o novo head ainda precisa ser confirmado.

Próxima ação: confirmar CI do PR #22 no head após este registro. Se passar,
continuar verificando a matriz de requisitos implementáveis no Cloud, mantendo
os pins anteriores imutáveis. Compositor, teclado físico, serviços e recursos
reais continuam `M1_REQUIRED`.
