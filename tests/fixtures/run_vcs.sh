#!/usr/bin/env bash
# Run locally on a VCS host. All generated files stay in the new OUTPUT_DIR.
set -euo pipefail
usage() {
    printf 'Usage: %s {register_control|stream_pipeline} {0|1} OUTPUT_DIR CASE\n' "$0" >&2
    exit 2
}
[[ $# == 4 ]] || usage
fixture=$1
bug=$2
output=$3
case_name=$4
case "$fixture:$case_name" in
    register_control:directed|stream_pipeline:nobackpressure|stream_pipeline:backpressure|stream_pipeline:reset) ;;
    *) usage ;;
esac
[[ "$bug" == 0 || "$bug" == 1 ]] || usage
[[ -n "$output" && ! -e "$output" && ! -L "$output" ]] || {
    printf 'OUTPUT_DIR must be a new path: %s\n' "$output" >&2
    exit 2
}
fixture_root=$(builtin cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v vcs >/dev/null || { printf 'vcs is not on PATH\n' >&2; exit 127; }
mkdir -p -- "$(dirname -- "$output")"
mkdir -- "$output"
output=$(builtin cd -- "$output" && pwd)
builtin cd -- "$output"
{
    printf 'fixture=%s\nbug=%s\ncase=%s\nstarted_utc=%s\n' "$fixture" "$bug" "$case_name" "$(date -u +%FT%TZ)"
    printf 'compile_command='
    printf '%q ' vcs -full64 -sverilog -timescale=1ns/1ps +define+FIXTURE_BUG="$bug" -top tb "$fixture_root/$fixture/rtl.sv" "$fixture_root/$fixture/tb.sv" -o "$output/simv"
    printf '\nrun_command='
    printf '%q ' "$output/simv" "+CASE=$case_name"
    printf '\n'
} > command.txt
set +e
vcs -full64 -sverilog -timescale=1ns/1ps +define+FIXTURE_BUG="$bug" \
    -top tb "$fixture_root/$fixture/rtl.sv" "$fixture_root/$fixture/tb.sv" \
    -o "$output/simv" > compile.log 2>&1
compile_rc=$?
set -e
printf 'compile_exit=%s\n' "$compile_rc" > status.txt
if [[ $compile_rc != 0 ]]; then
    tail -n 35 compile.log >&2
    exit "$compile_rc"
fi
[[ -x "$output/simv" ]] || { printf 'Compiled executable missing\n' >&2; exit 1; }
set +e
"$output/simv" "+CASE=$case_name" > run.log 2>&1
run_rc=$?
set -e
printf 'run_exit=%s\nfinished_utc=%s\n' "$run_rc" "$(date -u +%FT%TZ)" >> status.txt
if [[ $run_rc != 0 ]]; then
    tail -n 35 run.log >&2
    exit "$run_rc"
fi
if ! grep -Eq "^FIXTURE_SUMMARY fixture=$fixture bug=$bug case=$case_name result=PASS checks=[1-9][0-9]* failures=0 cycles=[1-9][0-9]*$" run.log ||
   grep -Eq 'CHECK_FAIL|Fatal|Error-|reason=TIMEOUT' run.log; then
    printf 'validation=FAIL\n' >> status.txt
    tail -n 35 run.log >&2
    printf 'Missing valid PASS summary or failure signature found\n' >&2
    exit 1
fi
printf 'validation=PASS\n' >> status.txt
grep -E '^FIXTURE_(METRICS|SUMMARY)' run.log
