#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# The per-branch pre-merge CPU check on the FM-1: the 4 worst cases x 3 takes, every effect (the heaviest drive TYPE,
# TAPE, the heaviest reverb TYPE: tools/final/hil_cpu_fx.py), 10 voices, USB 48 kHz, each judged on the MAX of its
# takes against the margin rule (the user's): the whole ISR <= 4200 us. ~10 min. Exit 1 on a breach (or a take's own
# hil_cpu FAIL: 4500 us, a shed voice, a USB fault), 0 when all 4 pass.
#
#   tools/cpu_check.sh [PACKAGE.fwsc]     run it from the branch's tree; PACKAGE: installed first (fm1_install --yes),
#                                         else the build already on the FM-1 is measured (say which in the report)
#
#   case                    takes  rule
#   cpu_rhodes_spread_bend  3      SPREAD 127 + --bend-sweep; in CPU_HURT: 3 good takes IN A ROW (one over = FAIL)
#   cpu_vibes               3      --engine vibes; in CPU_HURT: 3 in a row
#   cpu_rhodes_bend         3      --bend-sweep; the max of the takes <= the limit
#   cpu_marimba             3      --engine marimba; the max of the takes <= the limit
# Optional cases, run only when ONLY names them (not in the default 4):
#   cpu_vibes_size_inf      3      cpu_vibes on INFINITE with --cc-sweep 73:1:127:200: the reverb SIZE turned every
#                                  200 ms, so its fades (0.9.6, 186 ms) run back to back; the max <= the limit
#   cpu_vibes_size_plate    3      the same on PLATE
#   (ONLY=vibes_size: both)
# (each take ~25-50 s; the same steps, names and rule as tools/final/run_final_rev.sh's cpu_* steps)
#
# Environment (all optional):
#   CPU_TAKES=3  CPU_LIMIT_US=4200  CPU_HURT=cpu_rhodes_spread_bend,cpu_vibes   as in run_final_rev.sh (CPU_HURT's
#                           cases: at least 3 takes in a row whatever CPU_TAKES)
#   REV_TYPE=NAME           the reverb TYPE for every effect (default: the heaviest by the compiled estimate)
#   ONLY=case,case          only these cases (the names above, or without cpu_: rhodes_spread_bend); an unknown
#                           name stops the check at once (exit 2, the valid names listed); 0 cases judged is
#                           never a PASS (exit 2)
#   PY=<python>             default <main checkout>/.local/venv/bin/python (mido, sounddevice, numpy)
#   TREE=<tree>             default: the git top level of the current directory
#   OUT=<dir>               default <tree>/build/cpu_check (one log per case, SUMMARY.txt)
#   LOCK_WAIT=3600          seconds to wait for the DEVICE lock (tools/lock.py run DEVICE: its holder's children
#                           inherit it, so it runs inside a session or a run that holds the FM-1)
#   DRY_RUN=1               hil_cpu_fx --dry-run per case: no lock, no MIDI, no audio, no install
# Needs the macOS sandbox off (CoreAudio, MIDI).
set -u
# the whole script is one function, called on the last line: bash parses all of it before running a line, so a
# checkout or an edit that rewrites this file while it runs (a 10 min check) cannot make it read half a new file
cpu_check_main() {
case "${1:-}" in -h|--help) sed -n '2,/^set -u/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;; esac
TREE="${TREE:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
TREE="$(cd "$TREE" && pwd)"
COMMON="$(git -C "$TREE" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo "$TREE/.git")"
MAIN="$(dirname "$COMMON")"
PY="${PY:-$MAIN/.local/venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
OUT="${OUT:-$TREE/build/cpu_check}"
DRY="${DRY_RUN:-0}"
CPU_TAKES="${CPU_TAKES:-3}"
CPU_LIMIT_US="${CPU_LIMIT_US:-4200}"
CPU_HURT="${CPU_HURT:-cpu_rhodes_spread_bend,cpu_vibes}"
ONLY="${ONLY:-}"
case "$CPU_TAKES$CPU_LIMIT_US" in *[!0-9]*|"") echo "CPU_TAKES / CPU_LIMIT_US: integers" >&2; exit 2 ;; esac
[ "$CPU_TAKES" -ge 1 ] || { echo "CPU_TAKES >= 1" >&2; exit 2; }
CASES="cpu_rhodes_spread_bend cpu_vibes cpu_rhodes_bend cpu_marimba"
OPTIONAL="cpu_vibes_size_inf cpu_vibes_size_plate"     # (only when ONLY names them)
# norm LIST: a comma list with each name given its cpu_ prefix (rhodes_spread_bend = cpu_rhodes_spread_bend);
# vibes_size = both size cases
norm() { local out="" x; local IFS=,; for x in $1; do x="${x// /}"; [ -z "$x" ] && continue
    case "$x" in cpu_*) ;; *) x="cpu_$x" ;; esac
    [ "$x" = "cpu_vibes_size" ] && x="cpu_vibes_size_inf,cpu_vibes_size_plate"
    out="$out${out:+,}$x"; done; echo "$out"; }
ONLY="$(norm "$ONLY")"
CPU_HURT="$(norm "$CPU_HURT")"
if [ -n "$ONLY" ]; then
    IFS=, read -r -a want <<< "$ONLY"
    for x in "${want[@]}"; do
        [[ " $CASES $OPTIONAL " == *" $x "* ]] || { echo "ONLY: unknown case '$x'; the cases: $CASES $OPTIONAL (or without cpu_; vibes_size: both size cases)" >&2; exit 2; }
    done
fi
NOPT=0; for x in $OPTIONAL; do [[ ",$ONLY," == *",$x,"* ]] && NOPT=$((NOPT + 1)); done   # (the optional cases named)
FX="$TREE/tools/final/hil_cpu_fx.py"
[ -f "$FX" ] && [ -f "$TREE/tools/hil_cpu.py" ] || { echo "no $FX / tools/hil_cpu.py: run it from a Frodhes tree" >&2; exit 2; }
FW="${1:-}"
[ -z "$FW" ] || [ -f "$FW" ] || { echo "no package $FW" >&2; exit 2; }

# the FM-1 under the DEVICE lock (re-run under tools/lock.py run; its children inherit the token)
if [ "$DRY" != "1" ] && [ -z "${CPU_CHECK_LOCKED:-}" ]; then
    CPU_CHECK_LOCKED=1 exec "$PY" "$TREE/tools/lock.py" run DEVICE --who "tools/cpu_check.sh" --why "pre-merge CPU check" \
        --wait "${LOCK_WAIT:-3600}" -- env CPU_CHECK_LOCKED=1 bash "$0" "$@"
fi

mkdir -p "$OUT"
: > "$OUT/SUMMARY.txt"
export TREE PYTHONUNBUFFERED=1 CPU_LIMIT_US
say() { echo "$*" | tee -a "$OUT/SUMMARY.txt"; }
say "cpu_check $(date '+%Y-%m-%d %H:%M:%S'): tree $(git -C "$TREE" log -1 --format='%h %s' 2>/dev/null | cut -c1-80)"
say "  rule: whole ISR <= $CPU_LIMIT_US us in every take (the max judged), $CPU_TAKES takes per case; 3 good in a row for $CPU_HURT"
if [ -n "$FW" ]; then
    if [ "$DRY" = "1" ]; then say "  would install $FW (dry run)"
    else
        say "  installing $FW (sha256 $(shasum -a 256 "$FW" | cut -c1-16)...)"
        (cd "$TREE" && "$PY" tools/fm1_install.py --yes "$FW") > "$OUT/install.log" 2>&1 \
            || { say "  the install FAILED: see $OUT/install.log"; exit 2; }
    fi
else
    say "  the build already on the FM-1 (no PACKAGE given)"
fi
BEND=(); (cd "$TREE" && "$PY" tools/hil_cpu.py --help 2>/dev/null | grep -q -- "--bend-sweep") && BEND=(--bend-sweep)
[ ${#BEND[@]} -eq 0 ] && say "  NOTE: this tree's hil_cpu has no --bend-sweep: the bend cases run without it"
CCSW=0; (cd "$TREE" && "$PY" tools/hil_cpu.py --help 2>/dev/null | grep -q -- "--cc-sweep") && CCSW=1

FAILS=0
RAN=0
# case NAME FX-OPTIONS... -- HIL_CPU-OPTIONS...
case_run() {
    local name="$1"; shift
    if [ -n "$ONLY" ] && [[ ",$ONLY," != *",$name,"* ]]; then say "  $name: skipped (not in ONLY)"; return 0; fi
    if [ -z "$ONLY" ] && [[ " $OPTIONAL " == *" $name "* ]]; then return 0; fi   # (optional: only when named)
    local rt=(); [ -n "${REV_TYPE:-}" ] && [[ " $* " != *" --rev-type "* ]] && rt=(--rev-type "$REV_TYPE")
    local n="$CPU_TAKES" h=()
    if [[ ",$CPU_HURT," == *",$name,"* ]]; then h=(--hurt); [ "$n" -lt 3 ] && n=3; fi
    local d=(); [ "$DRY" = "1" ] && d=(--dry-run)
    local t0=$SECONDS code
    RAN=$((RAN + 1))
    echo "$(date '+%H:%M:%S') $name: $n takes"
    (cd "$TREE" && "$PY" "$FX" "${d[@]+"${d[@]}"}" "${rt[@]+"${rt[@]}"}" --limit "$CPU_LIMIT_US" \
        --takes "$n" "${h[@]+"${h[@]}"}" "$@") > "$OUT/$name.log" 2>&1
    code=$?
    local line; line="$(grep -E '^hil_cpu_fx: takes ' "$OUT/$name.log" | tail -1 | sed 's/^hil_cpu_fx: //')"
    [ "$DRY" = "1" ] && line="$(grep -E '^hil_cpu_fx: dry run: ' "$OUT/$name.log" | tail -1 | sed 's/^hil_cpu_fx: //')"
    if [ "$code" = "0" ]; then say "  $name: PASS in $((SECONDS - t0)) s: ${line:-?}"
    else FAILS=$((FAILS + 1)); say "  $name: FAIL (exit $code) in $((SECONDS - t0)) s: ${line:-no take line: see $OUT/$name.log}"
        grep -E '^hil_cpu_fx: FAIL ' "$OUT/$name.log" | sed 's/^hil_cpu_fx: /    /' | tee -a "$OUT/SUMMARY.txt"
    fi
}
case_run cpu_rhodes_spread_bend --spread 127 -- --worst --usb 48000 "${BEND[@]+"${BEND[@]}"}"
case_run cpu_vibes -- --engine vibes --worst --usb 48000
case_run cpu_rhodes_bend -- --worst --usb 48000 "${BEND[@]+"${BEND[@]}"}"
case_run cpu_marimba -- --engine marimba --worst --usb 48000
if [ "$CCSW" = "1" ]; then
    case_run cpu_vibes_size_inf --rev-type INFINITE -- --engine vibes --worst --usb 48000 --cc-sweep 73:1:127:200
    case_run cpu_vibes_size_plate --rev-type PLATE -- --engine vibes --worst --usb 48000 --cc-sweep 73:1:127:200
elif [[ ",$ONLY," == *",cpu_vibes_size_"* ]]; then
    FAILS=$((FAILS + 1)); say "  the size cases: this tree's hil_cpu has no --cc-sweep: FAIL (not judged)"
fi
if [ "$RAN" = "0" ]; then say "cpu_check: NOTHING JUDGED: 0 cases ran (ONLY=$ONLY): not a PASS"; exit 2; fi
if [ "$FAILS" = "0" ]; then say "cpu_check: PASS ($RAN of $((4 + NOPT)) cases; whole ISR <= $CPU_LIMIT_US us; logs in $OUT)"; exit 0; fi
say "cpu_check: FAIL: $FAILS case(s) over the margin or failed (logs in $OUT)"
exit 1
}
cpu_check_main "$@"; exit $?
