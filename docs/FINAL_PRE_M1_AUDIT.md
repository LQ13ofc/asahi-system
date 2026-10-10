# Final pre-M1 audit

Date: 2026-10-10. Scope: Cloud candidate worktrees only. No M1 access or test was
performed, and neither `main` nor `master` was changed.

## Decision

**GO for the opt-in, reversible experimental M1 validation described in
[`release-candidate-m1-pr22.md`](release-candidate-m1-pr22.md), after CI passes
on the updated candidate heads.** This is not approval for a production
release. Actual Fedora Asahi session behavior, hardware services, and memory
measurements remain `M1_REQUIRED`.

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

Before running the documented experiment, require passing CI on the updated
PR #15 and PR #22 heads and retain the saved known-good production record. A
successful experiment would still require the M1 health, recovery, and
benchmark checks before any release claim.
