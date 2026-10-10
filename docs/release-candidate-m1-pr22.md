# Integrated Niri+ Release Candidate test on Fedora Asahi Remix 44

This separate procedure tests the integrated Cloud candidate from PR #22. The original first-gate procedure remains at `release-candidate-m1.md` and its PR #19 / Quickshell 79b093e pin are unchanged. Use this candidate only after reviewing the current PR #22 head and only when the known-good record describes the production installation (restore the first gate first if it was ever applied).
The production command remains pinned to `main`; the RC installer is a separate,
explicit helper. It does not merge either PR or update the production Quickshell
pin (`55e92880d0aff75d235f283c839ec0990eaa9e17`). The candidate Quickshell pin is
`fadf99f1e95dd8763082b7553fb55216e42121a3`.

Do not run these commands from a mutable project worktree. Use a fresh,
user-owned `main` checkout as the Git credential/configuration source. Its
working files are never executed as root. A root-side verifier reads the
bootstrap bytes once, checks them against the SHA-256 of the blob inside the
exact PR #22 commit, then executes those already-verified bytes from memory.
The bootstrap independently checks its own Git blob and imports the installer
resolver only from that immutable commit. The resolver verifies the exact PR
head, both repository origins, the Quickshell gitlink and lock, and each Git
blob/mode in the root-owned snapshot.

Git fetches are non-interactive. They may add objects and update `FETCH_HEAD` in
the preparation checkout; they do not switch branches, modify tracked files,
or use local source code as privileged input. If the private Quickshell commit
cannot be fetched with the normal user's configured Git credentials, the
operation stops before creating the known-good record or changing the install.

## 1. Check the currently installed production version

Run as the normal user:

```sh
niri+ status
niri+ doctor
sudo niri+ install --dry-run --with-quickshell
```

The last command should show the current production lock at
`55e92880d0aff75d235f283c839ec0990eaa9e17`. It is still the ordinary production
flow and does not apply changes.

## 2. Prepare a clean source and pin the current PR #22 head

```sh
export RC_SOURCE="$HOME/Projects/niri-plus-rc-pr22-source"
export RC_DIR="$HOME/.cache/niri-plus-rc-pr22"
mkdir -p "$HOME/Projects" "$RC_DIR"
GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never git clone --branch main --single-branch https://github.com/LQ13ofc/asahi-system.git "$RC_SOURCE"
```

If that checkout already exists, do not overwrite it. Confirm it is the clean
`main` checkout before proceeding:

```sh
test "$(git -C "$RC_SOURCE" branch --show-current)" = main
test -z "$(git -C "$RC_SOURCE" status --porcelain --untracked-files=all)"
test "$(git -C "$RC_SOURCE" remote get-url origin)" = https://github.com/LQ13ofc/asahi-system.git
```

Fetch the PR ref without checking it out. Pin its exact SHA for this test, then
verify the candidate gitlink and lock agree on the requested Quickshell commit:

```sh
GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never git -C "$RC_SOURCE" fetch --no-tags origin refs/pull/22/head
export RC_SYSTEM_COMMIT="$(git -C "$RC_SOURCE" rev-parse --verify 'FETCH_HEAD^{commit}')"
test "$(GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never git -C "$RC_SOURCE" ls-remote --exit-code --refs origin refs/pull/22/head | awk '{print $1}')" = "$RC_SYSTEM_COMMIT"
test "$(git -C "$RC_SOURCE" rev-parse "$RC_SYSTEM_COMMIT:external/quickshell")" = fadf99f1e95dd8763082b7553fb55216e42121a3
test "$(git -C "$RC_SOURCE" show "$RC_SYSTEM_COMMIT:integration/quickshell.lock.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])')" = fadf99f1e95dd8763082b7553fb55216e42121a3
```

Derive the bootstrap checksum from the immutable Git blob, then fetch the same
file by commit URL and compare it. Save only these non-secret recovery pins:

```sh
RC_BOOTSTRAP_BLOB="$(git -C "$RC_SOURCE" rev-parse "$RC_SYSTEM_COMMIT:scripts/niri-plus-rc-bootstrap")"
export RC_BOOTSTRAP_SHA256="$(git -C "$RC_SOURCE" cat-file blob "$RC_BOOTSTRAP_BLOB" | sha256sum | awk '{print $1}')"
curl --fail --location --proto '=https' --tlsv1.2 "https://raw.githubusercontent.com/LQ13ofc/asahi-system/$RC_SYSTEM_COMMIT/scripts/niri-plus-rc-bootstrap" --output "$RC_DIR/niri-plus-rc-bootstrap"
printf '%s  %s\n' "$RC_BOOTSTRAP_SHA256" "$RC_DIR/niri-plus-rc-bootstrap" | sha256sum --check
printf '%s %s\n' "$RC_SYSTEM_COMMIT" "$RC_BOOTSTRAP_SHA256" > "$RC_DIR/pins"
chmod 600 "$RC_DIR/pins"
```

## 3. Define the root-side in-memory verifier

This function does not execute a file from the working tree. It reads the
download once, validates those exact bytes against the Git-derived SHA-256, and
executes that in-memory buffer. The helper then independently verifies its Git
blob against `RC_SYSTEM_COMMIT`.

```sh
niri_plus_rc() {
    sudo python3 -I -c '
import hashlib, pathlib, sys
args = sys.argv[1:]
path = pathlib.Path(args[0])
expected = args[1]
payload = path.read_bytes()
if hashlib.sha256(payload).hexdigest() != expected:
    raise SystemExit("RC bootstrap SHA-256 mismatch; nothing was executed")
sys.argv = [str(path), *args[2:]]
exec(compile(payload, str(path), "exec"), {
    "__name__": "__main__",
    "__file__": str(path),
    "__verified_script_bytes__": payload,
})
' "$RC_DIR/niri-plus-rc-bootstrap" "$RC_BOOTSTRAP_SHA256" \
      --source-root "$RC_SOURCE" --bootstrap-commit "$RC_SYSTEM_COMMIT" "$@"
}
```

The source tree itself may be dirty after preparation without affecting the
root snapshot: the RC resolver reads Git objects only. The configured and
effective `origin` must still resolve to the approved repository.

## 4. Dry-run, then install the RC

The dry-run fetches/verifies the candidate and shows both full commits plus the
currently installed known-good production commits. It does not write the
known-good record or apply the snapshot.

```sh
niri_plus_rc --candidate-commit "$RC_SYSTEM_COMMIT" --dry-run
```

Only continue if the output lists the intended PR #22 SHA and Quickshell
`fadf99f1e95dd8763082b7553fb55216e42121a3`, and identifies the previous
production commits as known-good. Then install:

```sh
niri_plus_rc --candidate-commit "$RC_SYSTEM_COMMIT" --apply
```

Apply is limited to Fedora Asahi Remix 44/aarch64. Before changing managed
files, it records the installed production system commit and Quickshell commit
in a root-owned, mode-0600 recovery record. The normal install transaction
backs up all managed files/state and restores them if bootstrap, apply,
verification, or package finalization fails. The record is kept after a
successful RC install so a later session failure can restore the exact prior
commits. Plasma, SDDM configuration, user files outside Niri+ ownership, the
kernel, Mesa/Asahi drivers, and global services are outside the transaction.

## 5. Enter the test session

At SDDM choose the existing **Niri** session. Plasma remains the recovery
session. Check the first-login lifecycle before broader testing:

```sh
niri+ status
niri+ doctor
```

Confirm the installed Quickshell commit is `fadf99f1e95dd8763082b7553fb55216e42121a3`,
the Wayland-ready user services are active once, audio/network remain available,
and the Niri session can log out normally. Do not change SDDM or disable global
services during this trial.

After a fresh login and 2–3 minutes idle, gather five comparable runs and a
longitudinal memory sample:

```sh
mkdir -p "$HOME/niri-plus-rc-results"
niri+ benchmark --profile niri-quickshell --runs 5 --window 10 --output "$HOME/niri-plus-rc-results/benchmark.json"
niri+ memory --series --json-output "$HOME/niri-plus-rc-results/memory-series.json"
```

The memory series records at +0/+5/+15/+30 minutes. The JSON may include
process arguments, user paths, device metadata, and service names; inspect and
redact it before sharing. Cloud results in this repository are not M1 results.

## 6. Inspect Gaming Mode readiness separately

This does not launch a game and is safe to run after the normal Niri session is
stable:

```sh
niri+ gaming status
```

`AVAILABLE_UNVERIFIED` means only that the Niri session marker and Gamescope
executable were found. It does not establish Honeykrisp compatibility. Gaming
Mode always requires Gamescope; if it cannot start, Niri+ does not run the game
directly. Actual launch/frametime validation remains a separate `M1_REQUIRED`
test after the normal-session gate is accepted.

## 7. Restore the exact known-good installation

Use this if the install transaction reports failure, or if the graphical Niri
session is unusable. If SDDM still works, select Plasma, open a terminal, and
run the commands below. If the display manager cannot be used, switch to a TTY
with `Ctrl`+`Alt`+`F3`, log in as the same normal user, and run them there.

Restore the saved pins in the shell, then preview the exact restoration:

```sh
read -r RC_SYSTEM_COMMIT RC_BOOTSTRAP_SHA256 < "$HOME/.cache/niri-plus-rc-pr22/pins"
RC_SOURCE="$HOME/Projects/niri-plus-rc-pr22-source"
RC_DIR="$HOME/.cache/niri-plus-rc-pr22"
niri_plus_rc() {
    sudo python3 -I -c '
import hashlib, pathlib, sys
args = sys.argv[1:]
path = pathlib.Path(args[0])
expected = args[1]
payload = path.read_bytes()
if hashlib.sha256(payload).hexdigest() != expected:
    raise SystemExit("RC bootstrap SHA-256 mismatch; nothing was executed")
sys.argv = [str(path), *args[2:]]
exec(compile(payload, str(path), "exec"), {
    "__name__": "__main__",
    "__file__": str(path),
    "__verified_script_bytes__": payload,
})
' "$RC_DIR/niri-plus-rc-bootstrap" "$RC_BOOTSTRAP_SHA256" \
      --source-root "$RC_SOURCE" --bootstrap-commit "$RC_SYSTEM_COMMIT" "$@"
}
niri_plus_rc --restore-known-good --dry-run
```

If it prints the saved production system and Quickshell commits, restore them:

```sh
niri_plus_rc --restore-known-good --apply
niri+ status
```

This restores the exact pre-RC Git commits recorded from the working
production install. It does not downgrade Fedora packages globally, kernel,
Mesa, firmware, or SDDM; package additions remain installed unless separately
reviewed. After successful restoration the one-time recovery record is removed.

## Cloud coverage and hardware boundary

Cloud tests exercise main-only production resolution, exact PR-head comparison,
dirty-worktree exclusion, source/Quickshell origin validation, gitlink/lock
matching, snapshot manifest/blob/mode verification, known-good commit recording,
installer transaction failures, helper hash/blob verification, and the
Gamescope-only launcher/no-fallback contract. CI validates the repository code
and the Release Candidate stays on PR #22.

Only the M1 can validate boot/session selection, Honeykrisp rendering, the live
Wayland race, audio, Wi-Fi, suspend/resume, and RAM/PSI/frametime behavior. No
hardware result is implied by this procedure or by Cloud tests.
