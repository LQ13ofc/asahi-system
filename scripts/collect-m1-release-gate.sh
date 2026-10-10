#!/usr/bin/env bash
set -uo pipefail

usage() {
    cat <<'EOF'
Usage: collect-m1-release-gate.sh [plasma|niri-core|niri-quickshell] [new-output-directory]

Collects read-only diagnostics and five benchmark runs for the selected live
session. It writes only under the selected output directory with private modes.
The memory JSON includes process metadata; review/redact it before sharing.
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
    usage
    exit 0
fi
profile=${1:-niri-quickshell}
case "$profile" in
    plasma|niri-core|niri-quickshell) ;;
    *) usage >&2; exit 2 ;;
esac

if [[ $# -gt 2 ]]; then
    usage >&2
    exit 2
fi
umask 077
if [[ $# -eq 2 ]]; then
    output_dir=$2
    if ! mkdir -m 700 -- "$output_dir" 2>/dev/null; then
        echo "Refusing to reuse output directory: $output_dir" >&2
        exit 2
    fi
else
    output_dir=$(mktemp -d "${HOME:?}/niri-plus-release-gate.XXXXXXXX") || exit 2
fi

run_capture() {
    local name=$1
    shift
    "$@" >"$output_dir/$name.txt" 2>&1
    local result=$?
    printf '%s\n' "$result" >"$output_dir/$name.exit-code"
    return 0
}

date --iso-8601=seconds >"$output_dir/collected-at.txt" 2>&1 || true
uname -r -m >"$output_dir/kernel-architecture.txt" 2>&1 || true
grep -E '^(NAME|ID|VERSION_ID|PRETTY_NAME)=' /etc/os-release >"$output_dir/os-release.txt" 2>&1 || true
run_capture niri-plus-status niri+ status
run_capture niri-plus-doctor niri+ doctor
run_capture niri-plus-preflight niri+ preflight
run_capture quickshell-plugin niri+ plugin status quickshell
run_capture offline-recovery niri+ recovery status
run_capture gaming-status niri+ gaming status
run_capture user-session-target systemctl --user show graphical-session.target \
    --property=ActiveState,SubState,Result
run_capture niri-service systemctl --user show niri.service \
    --property=ActiveState,SubState,Result,NRestarts,ExecMainStatus
run_capture quickshell-service systemctl --user show asahi-quickshell.service \
    --property=ActiveState,SubState,Result,NRestarts,ExecMainStatus
run_capture compositor-journal journalctl --user -b --since='15 minutes ago' \
    -u niri.service -u asahi-quickshell.service -u asahi-niri-polkit-agent.service --no-pager

# This structured collector is read-only. Its JSON deliberately retains enough
# process detail for diagnosis, so the output directory remains mode 0700.
niri+ memory --json-output "$output_dir/memory.json" >"$output_dir/memory-summary.txt" 2>&1
printf '%s\n' "$?" >"$output_dir/memory.exit-code"
niri+ benchmark --profile "$profile" --runs 5 --window 10 \
    --output "$output_dir/benchmark-$profile.json" >"$output_dir/benchmark.txt" 2>&1
printf '%s\n' "$?" >"$output_dir/benchmark.exit-code"

printf 'Diagnostics saved privately to: %s\n' "$output_dir"
printf '%s\n' 'Review/redact all outputs before sharing; memory.json includes process metadata.'
