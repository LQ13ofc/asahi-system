# Final pre-M1 audit

Date: 2026-10-10. Scope: Cloud candidate worktrees only. No M1 access or test was
performed, and neither `main` nor `master` was changed.

## Installer and offline-recovery addendum

The original PR #22 GO decision below is historical and applies only to that
earlier explicit experiment. This addendum evaluates the new install/plugin/
recovery candidate based on PR #22 head
`0f2774d750dad31ac7ad9e841754a0b96c1376dc`, with Quickshell PR #15 commit
`d2fe6d57dc2b86e5f433a0f6282732b8d03ca7f3`. The gitlink and lockfile agree.
The production Quickshell pin remains
`55e92880d0aff75d235f283c839ec0990eaa9e17`.

**Current decision: NO-GO until the new stacked PR #24 is pushed and its hosted
CI passes.** After that gate, the intended decision is GO only for the
explicit, reversible experimental M1 procedure in
[`M1_FIRST_TEST_FINAL.md`](M1_FIRST_TEST_FINAL.md); this remains NO-GO for
production. No hardware result is inferred from Cloud.

Cloud regressions found and corrected in this continuation:

- An RPM signature check could have accepted a digest-only or `NOKEY` message
  when a tool returned zero. Recovery now requires explicit trusted signature
  evidence; both summary and verbose rpmkeys output are covered.
- If offline DNF could not preflight removal of newly installed optional RPMs,
  recovery stopped before restoring managed files. It now restores the exact
  managed snapshot and reports packages whose optional cleanup was deferred.
- The RC dry-run previously did not run the Fedora Asahi 44/aarch64 target
  check. It now refuses before Git/network resolution on an incompatible host.
- The current M1 procedure previously invoked `preflight`/`recovery` commands
  before they existed on 0.1.9. It now uses only old-version diagnostics first;
  RC apply itself prepares and verifies the offline bundle before mutations.

Validation for these changes: 287 asahi-system unittests passed, baseline and
static checks passed; Quickshell PR #15 at its unchanged exact head passed 45
tests, 59-file QML load, full offscreen scene render, and qmllint (exit 0, 193
classified warnings). The latest PR #22 and PR #15 hosted Actions runs are
successful. PR #24 CI has not yet run. See `RELEASE_PROGRESS.md` for the
complete result and remaining gate. Hardware items below remain
`M1_REQUIRED`.

## Previous candidate decision (PR #22; superseded by the addendum above)

**GO for the opt-in, reversible experimental M1 validation described in
[`release-candidate-m1-pr22.md`](release-candidate-m1-pr22.md).** The updated
PR #15 and PR #22 candidate heads passed CI. This is not approval for a
production release. Actual Fedora Asahi session behavior, hardware services,
and memory measurements remain `M1_REQUIRED`.

## Candidate identity and preservation

| Repository | Candidate source | Audited Quickshell pin |
|---|---|---|
| `asahi-system` | PR #22 candidate branch, audit branch based on `2a6954623eeb657ffe3b8271f4004e22c65842ae` | `d2fe6d57dc2b86e5f433a0f6282732b8d03ca7f3` |
| `quickshell-` | PR #15 candidate branch | `d2fe6d57dc2b86e5f433a0f6282732b8d03ca7f3` |

The candidate gitlink, lockfile, RC verifier, and PR #22 test procedure agree
on that Quickshell commit. The production lock at `origin/main` remains
`55e92880d0aff75d235f283c839ec0990eaa9e17`. Quickshell `origin/master` remains
unchanged. Graphify PR #23 (`9bc3b2c8…`) and PR #16 (`79022a42…`) remain open and
unmerged; their commits were cherry-picked into the candidate worktrees.

## Findings fixed

| Severity | Finding and evidence | Fix and regression coverage |
|---|---|---|
| Low | Graphify's scan of the asahi-system worktree also entered the populated `external/quickshell` submodule, duplicating visual-source nodes in the system graph. | Added `external/quickshell/` to the asahi `.graphifyignore`; added a test. A fresh extraction contains zero `external/quickshell/` nodes. |
| Medium | Offscreen Control Center screenshots showed its intrinsic content extending beyond a short viewport; bottom controls were clipped and lacked a scroll affordance. | The shared panel calculates available height from its monitor; Control Center bounds content in a `Flickable` and shows an on-demand scrollbar. The new layout check verifies non-overlap and access to the last row. GitHub CI now runs this check. |

No confirmed install, rollback, source-authentication, pin-integrity, or session
lifecycle defect remained in the Cloud candidate suites.

## Cloud evidence

`asahi-system`:

- Baseline validator passed against the checked-in Fedora Asahi 44 baseline.
- `compileall` passed; all 237 `unittest` tests passed. Coverage includes exact
  commit snapshots, non-interactive owner-identity Git, private-source auth
  failure before apply, concurrent working-tree edits, lock/gitlink mismatch,
  install rollback at staged failure points, permissions, protected paths,
  idempotence, Wayland readiness, KDE-only filters, and candidate procedure
  pins.
- Graphify 0.9.84 emitted 890 nodes / 2,439 edges from 42 source files. The
  graph contains no Quickshell, QML, KDL, or systemd coverage.

`quickshell-`:

- `compileall` and all 45 Python unit tests passed.
- All 13 harness checks passed: 59 QML files load without warnings; every
  reference scene renders; settings persistence, multiscreen layout, keyboard
  interaction, brightness, audio/network/Bluetooth controls, Niri bridge,
  weather lifecycle, performance profiles, and Control Center constrained
  scrolling pass.
- `pyside6-qmllint`: 126 files, exit 0, 193 warnings: 162 `unqualified`, 20
  `missing-property`, 6 `import`, 3 `unresolved-type`, and 2 `unused-imports`.
  The categories are described in `validacao-qml.md`; load/render tests pass.
- Graphify emitted 372 nodes / 713 edges from 16 source files. It reported 21
  code files with no nodes and 140 unclassified files, including `.qml`.

GitHub CI:

- Quickshell PR #15 head `d2fe6d57dc2b86e5f433a0f6282732b8d03ca7f3`: both
  `qml-and-python` runs passed (`38060336073`, `38060333049`), including the
  bounded-scroll regression check.
- asahi-system PR #22 head `360d174dd95296f89820b9a2e54899e78f3bdcd3`:
  `static-validation` passed (`38060438358`).
- Graphify PR #23 remains at its original head and its static validation passed
  (`38058990232`). Graphify PR #16 has no CI checks configured.

## Risks and limits

| Risk | Severity / disposition | Evidence boundary |
|---|---|---|
| Niri, Quickshell ARM64 package, Wayland user units, audio, Wi-Fi, Bluetooth, brightness, suspend/resume, and Plasma recovery on the actual target | **High — `M1_REQUIRED`; release blocker, not blocker for the isolated experiment** | Cloud uses Qt stubs; Niri, `qs`, and the PolicyKit executable are not installed here. `systemd-analyze verify` was attempted and reported those missing executables; a Cloud user systemd manager is unavailable. Static unit/lifecycle and session tests pass. |
| Idle RAM, CPU wakeups, GPU behavior, multi-monitor behavior and 4 GiB attribution | **High — `M1_REQUIRED`** | No Cloud result is presented as MacBook evidence. No cause is assigned to the 4 GiB observation. |
| Remaining QML lint warnings | **Low / advisory** | Qt 6 lint exits 0; unresolved Quickshell metadata and unqualified accesses are classified in `validacao-qml.md`. Review warnings individually if runtime evidence identifies a defect. |
| Graphify coverage gaps | **Low / known tooling limit** | It is only a navigation aid for emitted AST nodes. QML/KDL/systemd and the 21 no-node files require the separate static checks and runtime harness. |

## Recovery and next gate

The candidate installer remains explicit and opt-in. Its simulated transaction
tests verify restoration of previously managed files, state, permissions, and
pins after failures; the production `sudo niri+ install` channel remains tied
to `main`. The experimental procedure is separate and records the exact
candidate Quickshell pin. Do not use old PR #19/PR #8 command material for this
updated PR #22 candidate.

Before running the documented experiment, retain the saved known-good
production record. A
successful experiment would still require the M1 health, recovery, and
benchmark checks before any release claim.
