#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# The whole final FM-1 pass on the release binary with the reverb TYPEs, unattended: run_final.sh's steps and options
# (private/plans/final/FINISH-PLAN-max-agents.md; private/TEST-PLAN.md section 3, T1) + the reverb's FM-1 wave 3
# (private/reverb/PLAN.md sections 6-8, INTEGRATION.md section 5): one run for both.
#
#   run_final_rev.sh PACKAGE.fwsc         run it from the release tree (its tools/ are used)
#   run_final_rev.sh --help               the steps with their durations
#   ONLY=rev_early REV_TYPE=PLATE run_final_rev.sh BRANCH.fwsc    the early mode (a kernel branch's build): see --help
#
# "every effect" in every CPU step = the heaviest drive TYPE, the TAPE delay and the heaviest reverb TYPE (hil_cpu_fx.py:
# CC 84 / 88 / the reverb TYPE CC rewritten, from the tree's compiled cost estimates; REV_TYPE=NAME overrides). Each step
# logs to $OUT/<step>.log; $OUT/SUMMARY.md at the end (PASS / FAIL per step with its key numbers); $OUT/device-done.json
# for release.py --no-device --device-done when everything passed.
#
# Environment (all optional):
#   PREV=<0.9.4 .fwsc>      default <main checkout>/build/release-0.9.4/frodhes-0.9.4.fwsc
#   PY=<python>             default <main checkout>/.local/venv/bin/python (mido, sounddevice, numpy)
#   TREE=<release tree>     default: the git top level of the current directory
#   OUT=<dir>               default <main checkout>/build/final-rev (archived at the start: never the .fwsc's folder)
#   TAKES=<dir>             the reverb's jack takes, default <main checkout>/private/reverb/takes
#   JACK_DEV=M6 JACK_CH=5   the interface recording the headphone jack
#   QUICK=1                 stress_max 48k / 44.1k with --quick (20 s worst + 24 s recipe per engine): ~30 min less
#   REV_TYPE=NAME           the reverb TYPE for "every effect" (default: the heaviest by the compiled estimate);
#                           the early mode: the TYPE it tests (default: every TYPE in the tree but HALL)
#   RESUME=1                the same OUT, package sha256 and tree HEAD as the last attempt: the steps that passed
#                           there are DONE (their logs kept), the run restarts at the first one that did not; the
#                           install always runs again. A different package or tree: a fresh run (printed why)
#   CROSSCHECK=1            (default) hil_jack on the Rhodes beside the folded jack check (the plan's P3 side-by-side
#                           until the fold is proven); CROSSCHECK=0 leaves it to the fold
#   TIMEOUT_SCALE=1         every step's timeout (its estimate x 2 + 2 min, see --help) x this; a timed-out step is
#                           FAIL "timeout", the FM-1 is looked for again (fm1_install --info) and the run goes on
#   SKIP_COVERED=0          (default) every step runs; SKIP_COVERED=1: s5_install and keyadc_gate are not run,
#                           SUMMARY.md names the PRIOR run that covers each (a FAIL there is a FAIL here)
#   PYTEST_GREEN=<sha>      make pytest is green on this sha (>= 7 hex): when it is the tree's HEAD, the stress
#                           steps skip hil_crackle's host self-test (test_hil_crackle runs the same)
#   FW_SHA256=<sha256>      the package's expected sha256 (else PACKAGE.sha256 when there): a mismatch stops the run
#   PRIOR=<dir>             that earlier run's OUT, default <main checkout>/build/final2 (its steps.tsv, run.txt)
#   ONLY=step,step          run only these steps (the others are listed as skipped)
#   LOCK_WAIT=600           seconds to wait for build/hil/DEVICE.lockdir
#   AUTOSAVE_OLD=1          autosave_stream with --old-rule (the save within 70 s: a firmware with the 60 s rule)
#   CPU_TAKES=3             the takes per worst-case CPU step (cpu_rhodes_bend / _spread / _spread_bend, cpu_vibes,
#                           cpu_marimba, cpu_<engine>_<type>, the early mode's): hil_cpu_fx --takes, the MAX judged
#   CPU_LIMIT_US=4200       the margin rule (the user's): every CPU step FAILs when a take's whole ISR is over it
#                           (4500 us stays the design target, hil_cpu's own gate)
#   CPU_HURT=cpu_rhodes_spread_bend,cpu_vibes   the worst cases that have been over the margin: 3 good takes IN A
#                           ROW (at least 3 whatever CPU_TAKES; one take over = FAIL, every take in the log)
#   FRODHES_TRS_PORT=NAME   step midi_clock also sends the clock over the TRS jack: NAME = the computer MIDI
#                           interface's output wired to the FM-1's TRS IN (a substring; tools/hil_clock.py --list);
#                           unset: the clock over the FM-1's own USB-MIDI only
#   DRY_RUN=1               every step's --dry-run / --help instead: no lock, no MIDI, no audio, no install;
#                           output to $OUT/dry-run/ (ONLY=rev_early: $OUT/dry-run/ too)
# Needs the macOS sandbox off (CoreAudio, MIDI). Never touches MASTER or the MOTU gain.
set -u

steps_help() {
    cat <<'EOF'
Steps, in order: what fails fastest first (private/plans/test-speed/PLAN.md 3.3, 3.4, 3.10; P2). Minutes measured
on the FM-1 where marked m, else estimated. ~79 min in all (the CPU steps' 3 takes: +13): ~53 for the release's own steps (the plan's 48 + the
SPREAD steps, the fold's takes, the jack_rhodes cross-check) + ~12 for the reverb's own (other TYPEs' CPU, its stress,
its jack takes); -4 when INFINITE is the heaviest TYPE; CROSSCHECK=0 -2; QUICK=1 ~40. Each step's timeout: its estimate x 2 + 2 min (TIMEOUT_SCALE).
  step                     min    what
  install, fm1_info        1.5    fm1_install.py --yes PACKAGE, --info (also on RESUME); a failed install stops the run
  stress_48000             19     stress_max at 48 kHz FIRST (the user's DAW rate), FULL: 3 worst x 60 s + 2 recipes +
                                  the jack-alone take per engine, every engine, every effect with the heaviest drive,
                                  TAPE, the heaviest reverb TYPE + LOW CUT (rev_hil.py stress --full: hil_crackle's own
                                  takes and detectors, USB and the jack together); folded in (--fold): per engine a
                                  jack check (idle 3 s: USB exact zero = hil_scan silence; hil_jack's quiet low note,
                                  loud chord and harmonics notes: floor / hiss / SNR / harmonics) and a TAPE recipe
                                  (tape_jack), MIDI -> USB / jack onsets by clock_fit (usb_onset); QUICK=1: 5.5
  stress_48000_selftest   (4)     hil_crackle's host self-test on these takes (the same rev_hil arguments +
                                  --self-test-only), unless PYTEST_GREEN=<HEAD>: in the BACKGROUND (nice) while the
                                  FM-1 records stress_48000 (no FM-1, no lock; ~4 min saved), waited for before the
                                  summary; stress_48000 FAILs in SUMMARY.md unless it PASSes. SELFTEST_BG=0: inside
                                  stress_48000, first, as before
  autosave_stream          1      the settings autosave with the USB stream open (hil_autosave.py --rate both): a TONE
                                  change, silence, the save seen in CC 116 (item 17, else item 10) within 10 s, USB
                                  underruns / missed / overruns 0 over the take; AUTOSAVE_OLD=1: --old-rule (70 s), 3 min
  save_glitch              3      the worst case DURING an auto save (hil_save_glitch.py, 48 kHz; its host self-test
                                  first): the setup save (CC 57) and the pattern's (a step note, then UNDO), caught in
                                  CC 116 item 17 and attacked 0 / 30 / 60 ms later (10 voices, every effect, the pedal,
                                  INFINITE, chords + the bend): 0 stress_max events on USB and the jack, the whole ISR
                                  in the save's window <= CPU_LIMIT_US, USB underruns / missed / overruns 0
  release_level            4      no louder after the key-up (hil_release_level.py): the Rhodes TYPEs x NOISE 48 / 127 x
                                  v15 / v60 / v127 x C2 / C4 / C6 held 2 s + the pedal case, USB + jack; QUICK=1: 1.6
  midi_clock               8      every clock-synced item follows an external MIDI clock (hil_clock.py, CLOCK EXT): the
                                  tremolo, phaser, chorus, filter LFO and VIBES motor at SYNC 1/4 (period 0.5 %, phase
                                  5 % of a beat on the sent beats), the delay DIG / ANA / TAPE and the long echo halved
                                  (1 ms + 0.5 %), the arpeggiator and the sequencer on the 1/16 grid (Start / Continue
                                  from bar 1, Stop), a 120 -> 100 change tracked within 2 beats, a Stop / Start
                                  re-phasing, out-of-range clock holding the tempo; the clock over USB-MIDI and, with
                                  FRODHES_TRS_PORT set, over the TRS jack too (+8). The table row | INT | EXT-USB |
                                  EXT-TRS in midi_clock-summary.txt; INT (CLOCK INT + a BPM) and TRS IN OFF need the
                                  panel: hil_clock.py --via int / trsoff by hand, the wah and TAP are MANUAL rows
  midi_parity              3      with FRODHES_TRS_PORT: USB-MIDI and the TRS MIDI input play the same (hil_midi_parity.py):
                                  velocities, release velocity, sustain, drive / delay CCs, a Program Change, pitch
                                  bend, the CHANNEL filter; the start sound restored by its matching program
  cpu_rhodes_1..3          3x0.5m hil_cpu --worst --usb 48000, the Rhodes, every effect (the same choice), 1 take each
  cpu_rhodes_bend          3x0.5m + --bend-sweep (skipped when hil_cpu has none); CPU_TAKES=3 takes, the max judged
  cpu_rhodes_spread(_bend) 6x0.5  SPREAD 127 (CC 105 before the notes), and + the bend: the tight case (3 takes each)
  cpu_vibes / cpu_marimba  6x0.8m every effect, 3 takes each
  cpu_<engine>_<type>      18x0.6 every engine x each reverb TYPE beyond the heaviest: --rev-type, 3 takes each
                                  Every CPU step: FAIL when a take's whole ISR > CPU_LIMIT_US (4200: the margin rule;
                                  the design target 4500 stays hil_cpu's own gate); CPU_HURT's steps (default
                                  cpu_rhodes_spread_bend, cpu_vibes: over the margin before): 3 good takes in a row;
                                  SUMMARY.md: min-max per step. ~22 min of CPU steps (CPU_TAKES=1: ~8)
  keyadc_gate              6      hil_cpu --keyadc gate --worst --runs 3 --scenario pedal: each engine's worst
                                  scenarios only (PLAN 3.4); the whole gate (18m) when the tree's hil_cpu has no
                                  --scenario; keyadc_<engine>5 (5 runs) on that engine's FAIL
  stress_rev_48000         4      stress_max with INFINITE in every take (frozen in the worst), every engine; a
                                  COVERED row when stress_48000's heaviest TYPE is INFINITE already
  stress_44100             10.5   stress_max at 44.1 kHz: 2 worst x 60 s + the recipes at 30 s per engine (--short;
                                  the jack-alone take only at 48 kHz), every effect as at 48 kHz, no self-test
  t1_s3s4                  2.5    T1 S3 S4 (hil_scan --tier must); t1_s2 COVERED by stress_48000's *-no-usb takes
  usb_onset_44100          1.5    hil_usb_onset --rate 44100 (3 takes, USB + jack): the 44.1 kHz MIDI -> sound onset
                                  (the fold below is 48 kHz only)
  silence, jack_vibes, jack_marimba, tape_jack, usb_onset   COVERED rows: folded into stress_48000 (PASS with it;
                                  the numbers in stress_48000-fold.json and SUMMARY.md)
  jack_rhodes              2      hil_jack on the Rhodes: the cross-check of the folded numbers (CROSSCHECK=1)
  rev_jack                 4.5    the reverb on the jack + USB, Rhodes: 3 TYPEs x 3 settings (~16 s each), INFINITE
                                  frozen held 60 s, the TYPE switched while playing; the WAVs in $TAKES/48000/
  s5_install               3      T1 S5 LAST: 0.9.4, a panel saved, the release over it, compared with the same panel
                                  sent to the release itself (hil_must: the build under test is the reference)
  pattern_power_cycle      -      MANUAL (the power switch and the user's eyes): the steps are in SUMMARY.md
Checks at the start: the package's sha256 (FW_SHA256=<sha> or PACKAGE.sha256: a mismatch stops the run; checked
again at the end; in SUMMARY.md), a package or PREV under OUT is refused; before waiting on DEVICE.lockdir the running
run_final*.sh are printed, and a lock whose owner pid is gone (or, without a pid, with no FM-1 tool running) stops
the run at once, naming it. RESUME=1 restarts at the first step that did not pass (same package, tree and OUT).

The early mode, ONLY=rev_early (a reverb kernel branch's build, PLATE or INFINITE alone, as soon as it compiles;
~9 min per TYPE; for each TYPE in REV_TYPE, default every TYPE in the tree but HALL):
  early_install            1.5    fm1_install.py --yes PACKAGE (then fm1_info)
  early_cpu_vibes_<t>       3x0.8  hil_cpu --engine vibes --worst --usb 48000 (10 voices), every effect with that TYPE,
                                  CPU_TAKES takes (cpu_vibes in CPU_HURT: 3 in a row), whole ISR <= CPU_LIMIT_US
  early_cpu_rhodes_spread_bend_<t> 3x0.5  the Rhodes, SPREAD 127 + --bend-sweep (when hil_cpu has it), that TYPE
  early_stress_<t>         2.5    stress_max at 48 kHz with that TYPE in every take, quick (20 s worst + 12 s recipe
                                  per engine; rev_hil.py stress --type T --quick)
EOF
}
case "${1:-}" in -h|--help) sed -n '2,/^set -u/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; echo; steps_help; exit 0 ;; esac

FW_ARG="${1:-}"
if [ -z "$FW_ARG" ]; then
    echo "usage: $0 PACKAGE.fwsc   (the release build, e.g. build/frodhes-0.9.5.fwsc); $0 --help: the steps" >&2
    exit 2
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
TREE="${TREE:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
TREE="$(cd "$TREE" && pwd)"
COMMON="$(git -C "$TREE" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo "$TREE/.git")"
MAIN="$(dirname "$COMMON")"
PREV="${PREV:-$MAIN/build/release-0.9.4/frodhes-0.9.4.fwsc}"
PY="${PY:-$MAIN/.local/venv/bin/python}"
OUT="${OUT:-$MAIN/build/final-rev}"
TAKES="${TAKES:-$MAIN/private/reverb/takes}"
JACK_DEV="${JACK_DEV:-M6}"
JACK_CH="${JACK_CH:-5}"
LOCK_WAIT="${LOCK_WAIT:-600}"
DRY="${DRY_RUN:-0}"
QUICK="${QUICK:-0}"
ONLY="${ONLY:-}"
REV_TYPE="${REV_TYPE:-}"
SKIP_COVERED="${SKIP_COVERED:-0}"
RESUME="${RESUME:-0}"
CROSSCHECK="${CROSSCHECK:-1}"
CPU_TAKES="${CPU_TAKES:-3}"
CPU_LIMIT_US="${CPU_LIMIT_US:-4200}"
CPU_HURT="${CPU_HURT:-cpu_rhodes_spread_bend,cpu_vibes}"
case "$CPU_TAKES$CPU_LIMIT_US" in *[!0-9]*|"") echo "CPU_TAKES / CPU_LIMIT_US: integers" >&2; exit 2 ;; esac
[ "$CPU_TAKES" -ge 1 ] || { echo "CPU_TAKES >= 1" >&2; exit 2; }
TIMEOUT_SCALE="${TIMEOUT_SCALE:-1}"
PRIOR="${PRIOR:-$MAIN/build/final2}"
EARLY=0; [ "$ONLY" = "rev_early" ] && { EARLY=1; ONLY=""; }
LOCK="$MAIN/build/hil/DEVICE.lockdir"

abspath() { (cd "$(dirname "$1")" && echo "$(pwd)/$(basename "$1")"); }
case "$FW_ARG" in /*) FW="$FW_ARG" ;; *) FW="$(abspath "$FW_ARG")" ;; esac
[ -f "$FW" ] || { echo "no package $FW" >&2; exit 2; }
[ -f "$PREV" ] || { echo "no previous release $PREV (PREV=...)" >&2; exit 2; }
FW_SHA="$(shasum -a 256 "$FW" | cut -d' ' -f1)"   # the package checked before anything (test-speed plan, TONIGHT 5)
WANT_SHA="${FW_SHA256:-}"
[ -z "$WANT_SHA" ] && [ -f "$FW.sha256" ] && WANT_SHA="$(awk '{print $1; exit}' "$FW.sha256")"
if [ -n "$WANT_SHA" ] && [ "$FW_SHA" != "$WANT_SHA" ]; then
    echo "the package $FW has sha256 $FW_SHA, not the expected $WANT_SHA (FW_SHA256 / $FW.sha256): stopped" >&2; exit 2
fi
echo "package $FW sha256 $FW_SHA$([ -n "$WANT_SHA" ] && echo ' (as expected)')"
HEAD_SHA="$(git -C "$TREE" rev-parse HEAD 2>/dev/null || echo '?')"
GREEN=0                                 # PYTEST_GREEN=<sha>: make pytest (test_hil_crackle's self-test) green on HEAD
if [ -n "${PYTEST_GREEN:-}" ]; then
    if [ "${#PYTEST_GREEN}" -ge 7 ] && [[ "$HEAD_SHA" == "$PYTEST_GREEN"* ]]; then GREEN=1
    else echo "PYTEST_GREEN=$PYTEST_GREEN is not the tree's HEAD $HEAD_SHA: the stress self-test runs" >&2; fi
fi
[ -x "$PY" ] || PY="$(command -v python3)"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
case "$FW/" in "$OUT"/*)                # OUT is archived at the start: the package must live elsewhere
    echo "the package $FW is inside OUT=$OUT, which this run archives at its start: copy it out first" >&2; exit 2 ;;
esac
case "$PREV/" in "$OUT"/*) echo "the previous release $PREV is inside OUT=$OUT: copy it out first" >&2; exit 2 ;; esac
for t in hil_cpu.py hil_crackle.py hil_must.py hil_scan.py hil_usb_onset.py fm1_install.py; do
    [ -f "$TREE/tools/$t" ] || { echo "$TREE/tools/$t missing: run this from the release tree (or TREE=...)" >&2; exit 2; }
done
for h in hil_cpu_fx.py tape_jack.py summarize_rev.py rev_hil.py; do
    [ -f "$HERE/$h" ] || { echo "$HERE/$h missing (it ships with this script)" >&2; exit 2; }
done

if [ "$DRY" = "1" ]; then
    OUT="$OUT/dry-run"
fi
mkdir -p "$OUT"
KEEP='^(run_final\.sh|run_final_rev\.sh|hil_cpu_fx\.py|tape_jack\.py|summarize\.py|summarize_rev\.py|rev_hil\.py|archive-.*|dry-run|__pycache__)$'
RESUMED=0; ATTEMPT=1; FIRST_START=""
if [ "$RESUME" = "1" ]; then              # the same package and tree as the last attempt: its passed steps are DONE
    psha="$(sed -n 's/^sha256=//p' "$OUT/run.txt" 2>/dev/null)"; ptree="$(sed -n 's/^tree_sha=//p' "$OUT/run.txt" 2>/dev/null)"
    pmode="$(sed -n 's/^mode=//p' "$OUT/run.txt" 2>/dev/null)"
    if [ -f "$OUT/steps.tsv" ] && [ "$psha" = "$FW_SHA" ] && [ "$ptree" = "$HEAD_SHA" ] \
        && [ "$pmode" = "$([ "$EARLY" = "1" ] && echo early || echo full)" ]; then
        RESUMED=1
        ATTEMPT=$(( $(sed -n 's/^attempt=//p' "$OUT/run.txt" | tail -1 || echo 1) + 1 ))
        FIRST_START="$(sed -n 's/^start_first=//p' "$OUT/run.txt")"; [ -z "$FIRST_START" ] && FIRST_START="$(sed -n 's/^start=//p' "$OUT/run.txt")"
        mv "$OUT/steps.tsv" "$OUT/steps.prev.tsv"
        echo "RESUME: attempt $ATTEMPT; done before: $(awk -F'\t' '$2 == "0" && $4 != "info" {printf "%s ", $1}' "$OUT/steps.prev.tsv")"
    else
        echo "RESUME=1 but not the same run (package ${psha:0:12} vs ${FW_SHA:0:12}, tree ${ptree:0:12} vs ${HEAD_SHA:0:12}," \
             "mode ${pmode:-?}${OUT:+, $OUT/steps.tsv $([ -f "$OUT/steps.tsv" ] && echo there || echo missing)}): a fresh run"
    fi
fi
if [ "$DRY" != "1" ] && [ "$RESUMED" = "0" ]; then   # an earlier run's files moved aside (never deleted; never a .fwsc)
    old="$(ls -A "$OUT" | grep -v -E "$KEEP" | grep -v -E '\.fwsc$' || true)"
    if [ -n "$old" ]; then
        arch="$OUT/archive-$(date +%Y%m%d-%H%M%S)"
        mkdir -p "$arch"
        echo "$old" | while IFS= read -r f; do mv "$OUT/$f" "$arch/"; done
        echo "earlier files moved to $arch"
    fi
elif [ "$DRY" = "1" ] && [ "$RESUMED" = "0" ]; then
    rm -f "$OUT"/*.log "$OUT"/steps.tsv
fi
: > "$OUT/steps.tsv"

# ---- the device lock ------------------------------------------------------------------------------------------
OWNER="run_final_rev.sh $(basename "$FW") pid $$ since $(date)"
release_lock() {
    if [ -f "$LOCK/owner" ] && [ "$(cat "$LOCK/owner")" = "$OWNER" ]; then
        # the whole directory (a heartbeat or any file a tool left inside kept rmdir from removing it: the lock
        # stayed behind, empty but present, and blocked the next run)
        mv "$LOCK" "$LOCK.released-$$" 2>/dev/null && rm -rf "$LOCK.released-$$" && echo "device lock released"
    fi
}
if [ "$DRY" = "1" ]; then
    echo "DRY RUN: would take $LOCK (no lock taken, nothing sent to the FM-1)"
else
    mkdir -p "$MAIN/build/hil"
    procs() {                           # the processes matching $1, this script and its own subshells left out
        ps -axo pid=,ppid=,command= | awk -v me=$$ -v pat="$1" '$1 != me && $2 != me && $0 ~ pat && $0 !~ /awk -v me/'
    }
    others="$(procs 'run_final[a-z_]*\.sh')"
    [ -n "$others" ] && echo "running now: $others"
    waited=0
    until mkdir "$LOCK" 2>/dev/null; do
        own="$(cat "$LOCK/owner" 2>/dev/null || echo '?')"   # fail fast on a stale lock (TONIGHT 6), never wait on it
        opid="$(echo "$own" | sed -n 's/.*pid \([0-9][0-9]*\).*/\1/p')"
        busy="$(procs 'hil_[a-z_]*\.py|run_final[a-z_]*\.sh|fm1_install')"
        if { [ -n "$opid" ] && ! kill -0 "$opid" 2>/dev/null; } || { [ -z "$opid" ] && [ -z "$busy" ]; }; then
            echo "the FM-1 lock $LOCK is stale: owner '$own'$([ -n "$opid" ] && echo ", pid $opid is not running")," \
                 "no FM-1 tool running: check it and remove it (rm -rf $LOCK), then start again" >&2
            exit 3
        fi
        if [ "$waited" -ge "$LOCK_WAIT" ]; then
            echo "the FM-1 is locked ($LOCK): $(cat "$LOCK/owner" 2>/dev/null || echo '?'); gave up after ${LOCK_WAIT} s" >&2
            exit 3
        fi
        [ $((waited % 60)) -eq 0 ] && echo "waiting for the device lock: $(cat "$LOCK/owner" 2>/dev/null || echo '?')"
        sleep 10; waited=$((waited + 10))
    done
    echo "$OWNER" > "$LOCK/owner"
    trap release_lock EXIT
    trap 'release_lock; exit 130' INT TERM
    echo "device lock taken: $LOCK"
fi

{
    echo "fwsc=$FW"
    echo "sha256=$FW_SHA"
    echo "sha256_expected=${WANT_SHA:-not given (FW_SHA256= or PACKAGE.sha256)}"
    echo "tree_sha=$HEAD_SHA"
    echo "pytest_green=$([ "$GREEN" = "1" ] && echo "$PYTEST_GREEN (= HEAD): the stress steps without the host self-test" || echo no)"
    echo "prev=$PREV"
    echo "tree=$TREE"
    echo "tree_head=$(git -C "$TREE" log -1 --format='%h %s' 2>/dev/null | cut -c1-100)"
    echo "start=$(date '+%Y-%m-%d %H:%M:%S')"
    echo "start_first=${FIRST_START:-$(date '+%Y-%m-%d %H:%M:%S')}"
    echo "attempt=$ATTEMPT"
    echo "resumed=$RESUMED"
    echo "dry_run=$DRY"
    echo "takes=$TAKES"
    echo "cpu_takes=$CPU_TAKES"
    echo "cpu_limit_us=$CPU_LIMIT_US"
    echo "cpu_hurt=$CPU_HURT"
    echo "mode=$([ "$EARLY" = "1" ] && echo early || echo full)"
    echo "skip_covered=$SKIP_COVERED"
    echo "prior=$PRIOR"
} > "$OUT/run.txt"

export TREE PYTHONUNBUFFERED=1 CPU_LIMIT_US
STRANDED=0

want() { [ -z "$ONLY" ] || [[ ",$ONLY," == *",$1,"* ]]; }

# step NAME KIND CMD...: runs CMD in the tree, its output to $OUT/NAME.log, a row in steps.tsv
# load1: the computer's 1-minute load average (macOS sysctl, else /proc): in each step's lines (a busy host starves
# the recorder: the rig's problem, not the FM-1's)
load1() { { sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}'; awk '{print $1}' /proc/loadavg 2>/dev/null; } | head -1; }
step() {
    local name="$1" kind="$2"; shift 2
    if ! want "$name"; then
        printf '%s\tnotrun\t0\t%s\tnot in ONLY=%s\n' "$name" "$kind" "$ONLY" >> "$OUT/steps.tsv"; return 0
    fi
    if [ "$STRANDED" = "1" ]; then
        printf '%s\tnotrun\t0\t%s\tthe FM-1 is not on the package (the install or S5 failed): not run\n' "$name" "$kind" >> "$OUT/steps.tsv"
        return 0
    fi
    if [ "$RESUMED" = "1" ] && [ "${ALWAYS:-0}" != "1" ]; then
        local prev
        prev="$(awk -F'\t' -v n="$name" '$1 == n && $2 == "0" {print $3; exit}' "$OUT/steps.prev.tsv")"
        if [ -n "$prev" ] && [ -f "$OUT/$name.log" ]; then
            printf '%s\t0\t%s\t%s\tDONE in an earlier attempt of this run (the same package and tree; its log kept)\n' \
                "$name" "$prev" "$kind" >> "$OUT/steps.tsv"
            echo "$(date '+%H:%M:%S') $name: DONE before (RESUME)"
            return 0
        fi
    fi
    local t0=$SECONDS code tmo
    tmo=$(( ($(est_s "$name" "$kind") * 2 + 120) * TIMEOUT_SCALE ))
    local l0; l0="$(load1)"
    echo "$(date '+%H:%M:%S') $name (timeout ${tmo} s, host load ${l0:-?}): $*"
    { echo "# $name: $*"; echo "# $(date); timeout $tmo s; host load ${l0:-?}"; } > "$OUT/$name.log"
    (cd "$TREE" && exec perl -e '$t = shift; alarm $t; exec @ARGV or die "exec: $!"' "$tmo" "$@") >> "$OUT/$name.log" 2>&1
    code=$?
    local note=""
    if [ "$code" = "142" ]; then              # SIGALRM: the step's timeout
        note="timeout after $tmo s"
        echo "# TIMEOUT after $tmo s: the FM-1 looked for again" >> "$OUT/$name.log"
        [ "$DRY" != "1" ] && (cd "$TREE" && perl -e 'alarm 60; exec @ARGV' "$PY" tools/fm1_install.py --info) >> "$OUT/$name.log" 2>&1
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$code" "$((SECONDS - t0))" "$kind" "$note" >> "$OUT/steps.tsv"
    echo "# host load before / after: ${l0:-?} / $(load1)" >> "$OUT/$name.log"
    echo "$(date '+%H:%M:%S') $name: exit $code in $((SECONDS - t0)) s${note:+ ($note)}, host load ${l0:-?} / $(load1)"
    return 0
}
# est_s NAME KIND: the step's estimated seconds (its timeout: x 2 + 120, x TIMEOUT_SCALE)
est_s() {
    case "$1" in
        install|early_install) echo 120 ;;  fm1_info*) echo 20 ;;
        stress_48000) [ "$QUICK" = "1" ] && echo 330 || echo 1380 ;;
        stress_44100) [ "$QUICK" = "1" ] && echo 210 || echo 630 ;;
        stress_*_selftest) echo 600 ;;
        autosave_stream) [ "${AUTOSAVE_OLD:-0}" = "1" ] && echo 180 || echo 60 ;;
        save_glitch) echo 180 ;;
        release_level) [ "$QUICK" = "1" ] && echo 100 || echo 230 ;;
        midi_clock) [ -n "${FRODHES_TRS_PORT:-}" ] && echo 1000 || echo 500 ;;
        midi_parity) echo 200 ;;
        usb_onset_44100) echo 90 ;;
        stress_rev_48000) echo 240 ;;  early_stress_*) echo 150 ;;
        keyadc_gate) [ ${#SC[@]} -gt 0 ] && echo 400 || echo 1100 ;;  keyadc_*5) echo 600 ;;
        t1_s3s4) echo 160 ;;  s5_install) echo 200 ;;  jack_*) echo 150 ;;  rev_jack) echo 300 ;;
        *) case "$2" in cpu) echo $((60 * ${CUR_TAKES:-1})) ;; *) echo 300 ;; esac ;;
    esac
}
SC=()
copy() { [ "$DRY" != "1" ] && [ -f "$1" ] && cp "$1" "$OUT/$2"; return 0; }
rmrep() { [ "$DRY" = "1" ] || rm -f "$@"; }
last_code() { awk -F'\t' -v n="$1" '$1 == n {c = $2} END {print c}' "$OUT/steps.tsv"; }
# covered NAME KIND: a step the reverb cannot change, not run: the PRIOR run's row for it named in steps.tsv
covered() {
    local name="$1" kind="$2" pr="" psha="?" pfw="?"
    if [ -f "$PRIOR/steps.tsv" ]; then
        pr="$(awk -F'\t' -v n="$name" '$1 == n {r = ($2 == "0" ? "exit 0" : $2 == "notrun" ? "not run: " $5 : "exit " $2) " in " $3 " s"} END {print r}' "$PRIOR/steps.tsv")"
        psha="$(sed -n 's/^sha256=//p' "$PRIOR/run.txt" 2>/dev/null | cut -c1-12)"
        pfw="$(sed -n 's/^fwsc=//p' "$PRIOR/run.txt" 2>/dev/null)"; pfw="$(basename "${pfw:-?}")"
    fi
    printf '%s\tnotrun\t0\t%s\tcovered by %s: %s (package %s, sha256 %s...); the reverb cannot change it (SKIP_COVERED=1)\n' \
        "$name" "$kind" "$PRIOR" "${pr:-no row there: CHECK BY HAND}" "$pfw" "${psha:-?}" >> "$OUT/steps.tsv"
}
BG_PIDS=()
# wait_bg: the background host steps (the stress self-tests) finished, their rows in steps.tsv
wait_bg() {
    local p
    for p in "${BG_PIDS[@]+"${BG_PIDS[@]}"}"; do wait "$p"; done
    BG_PIDS=()
}
finish() {
    wait_bg
    echo "end=$(date '+%Y-%m-%d %H:%M:%S')" >> "$OUT/run.txt"
    echo "sha256_end=$(shasum -a 256 "$FW" | cut -d' ' -f1)" >> "$OUT/run.txt"
    "$PY" "$HERE/summarize_rev.py" "$OUT" > "$OUT/summarize.log" 2>&1
    local sc=$?
    echo
    echo "SUMMARY: $OUT/SUMMARY.md"
    head -3 "$OUT/SUMMARY.md" 2>/dev/null
    exit $sc
}

D=""; [ "$DRY" = "1" ] && D="--dry-run"
CPU=("$PY" "$HERE/hil_cpu_fx.py")
[ "$DRY" = "1" ] && CPU+=(--dry-run)
[ -n "$REV_TYPE" ] && CPU+=(--rev-type "$REV_TYPE")
CPUT=("$PY" "$HERE/hil_cpu_fx.py")     # (the other reverb TYPEs: --rev-type each)
[ "$DRY" = "1" ] && CPUT+=(--dry-run)
CPU+=(--limit "$CPU_LIMIT_US"); CPUT+=(--limit "$CPU_LIMIT_US")
# hurt BASE: the step BASE is in CPU_HURT (over the margin before: 3 good takes in a row)
hurt() { [[ ",$CPU_HURT," == *",$1,"* ]]; }
# cpu_step NAME BASE T|H CMD...: a worst-case CPU step, CPU_TAKES takes (BASE in CPU_HURT: --hurt, at least 3 in a
# row); CMD is "${CPU[@]}" or "${CPUT[@]}" with its own options, then -- and hil_cpu's: --takes goes in front of them
cpu_step() {
    local name="$1" base="$2"; shift 2
    local n="$CPU_TAKES" h=()
    if hurt "$base"; then h=(--hurt); [ "$n" -lt 3 ] && n=3; fi
    local cmd=("$1" "$2"); shift 2
    CUR_TAKES=$n step "$name" cpu "${cmd[@]}" --takes "$n" "${h[@]+"${h[@]}"}" "$@"
}

# the reverb TYPEs (hil_cpu_fx.py --list-rev: NAME INDEX US CC VALUE, the heaviest first) and the ones beyond it
REVS="$(cd "$TREE" && "$PY" "$HERE/hil_cpu_fx.py" --list-rev 2>/dev/null)"
[ -n "$REV_TYPE" ] && [ "$EARLY" != "1" ] && REVS="$(cd "$TREE" && "$PY" "$HERE/hil_cpu_fx.py" --list-rev --rev-type "$REV_TYPE" 2>/dev/null)"
HEAVY_REV="$(echo "$REVS" | awk 'NR == 1 {print $1}')"
OTHER_REVS="$(echo "$REVS" | awk -v h="$HEAVY_REV" 'NF && NR > 1 && $1 != h {print $1}')"
if [ -z "$REVS" ]; then
    echo "no reverb TYPEs in $TREE (params.c has no [P_RV_TYPE])$([ "$DRY" = "1" ] && echo ': the dry run plans the contract HALL / PLATE beyond INFINITE')"
    [ "$DRY" = "1" ] && OTHER_REVS="HALL PLATE"
fi
echo "reverb TYPEs: $(echo "$REVS" | awk 'NF {printf "%s%s (%s us, CC %s = %s)", (NR > 1 ? ", " : ""), $1, $3, $4, $5}');" \
     "every effect: ${HEAVY_REV:-none}; once each: $(echo $OTHER_REVS)" | tee "$OUT/reverb-types.txt"

BEND=0
(cd "$TREE" && "$PY" tools/hil_cpu.py --help 2>/dev/null | grep -q -- "--bend-sweep") && BEND=1
INST=("$PY" tools/fm1_install.py --yes "$FW"); [ "$DRY" = "1" ] && INST=("$PY" tools/fm1_install.py --help)

# 0. the early mode: one kernel branch's build, its new TYPE(s) only
if [ "$EARLY" = "1" ]; then
    ET="${REV_TYPE:-$(echo "$REVS" | awk '$1 != "HALL" && NF {print $1}')}"
    [ -z "$ET" ] && [ "$DRY" = "1" ] && ET="PLATE INFINITE"
    step early_install info "${INST[@]}"
    if [ "$DRY" != "1" ] && [ "$(last_code early_install)" != "0" ]; then
        STRANDED=1; echo "*** the install failed: nothing else is run ***"
    fi
    [ "$DRY" != "1" ] && step fm1_info info "$PY" tools/fm1_install.py --info
    if [ -z "$ET" ]; then
        printf 'early_types\tnotrun\t0\tcpu\tno reverb TYPE beyond HALL in this tree (REV_TYPE=...)\n' >> "$OUT/steps.tsv"
    fi
    for t in $ET; do
        lt="$(echo "$t" | tr A-Z a-z)"
        cpu_step "early_cpu_vibes_$lt" cpu_vibes "${CPUT[@]}" --rev-type "$t" -- --engine vibes --voices 10 --worst --usb 48000
        if [ "$BEND" = "1" ]; then
            cpu_step "early_cpu_rhodes_spread_bend_$lt" cpu_rhodes_spread_bend "${CPUT[@]}" --rev-type "$t" --spread 127 -- --worst --usb 48000 --bend-sweep
        else
            cpu_step "early_cpu_rhodes_spread_$lt" cpu_rhodes_spread "${CPUT[@]}" --rev-type "$t" --spread 127 -- --worst --usb 48000
        fi
        rmrep "$TREE/build/hil/stress_rev/48000/report.txt" "$TREE/build/hil/stress_rev/48000/report.json"
        step "early_stress_$lt" stress "$PY" "$HERE/rev_hil.py" stress --type "$t" --quick --rate 48000 \
            --jack-dev "$JACK_DEV" --jack-ch "$JACK_CH" $D
        copy "$TREE/build/hil/stress_rev/48000/report.txt" "early_stress_$lt-report.txt"
        copy "$TREE/build/hil/stress_rev/48000/report.json" "early_stress_$lt-report.json"
    done
    finish
fi

# ---- the full run, v2 (private/plans/test-speed/PLAN.md 3.3, 3.4, 3.10; P2): fail-fast order, resumable, timeouts --
# 1. the release on the FM-1 (always, also on RESUME: S5, or a hand, may have left another build on it)
ALWAYS=1 step install info "${INST[@]}"
if [ "$DRY" != "1" ] && want install && [ "$(last_code install)" != "0" ]; then
    STRANDED=1; echo "*** the install failed: the remaining steps are not run ***"
fi
if [ "$DRY" != "1" ] && want install; then ALWAYS=1 step fm1_info info "$PY" tools/fm1_install.py --info; fi

# 2. stress_max at 48 kHz FIRST (the user's DAW rate: no 48 kHz glitch is the priority), full length, every engine,
# every effect with the heaviest reverb TYPE; hil_jack / silence / usb_onset / tape_jack folded into its captures
stress_full() {
    local r="$1"; shift
    local st=("$@"); [ "$GREEN" = "1" ] && st+=(--no-self-test)
    local q=(); [ "$QUICK" = "1" ] && q=(--quick)
    rmrep "$TREE/build/hil/stress_max/$r/report.txt" "$TREE/build/hil/stress_max/$r/report.json" \
        "$TREE/build/hil/stress_max/$r/fold.json"
    # the pipeline (suite-speed): hil_crackle's host self-test (the detectors proven on host renders of these same
    # takes, the same rev_hil arguments + --self-test-only) runs as its own step stress_<rate>_selftest in the
    # background, niced, while the FM-1 records; the device step gets --no-self-test. Its verdict is collected
    # before the summary (finish: wait), and SUMMARY.md FAILs stress_<rate> when its self-test did not PASS (the
    # old rule: no stress PASS without the detectors proven). The device lock is not touched (host only).
    # SELFTEST_BG=0: the self-test inside the device step as before
    if [ "${SELFTEST_BG:-1}" = "1" ] && want "stress_$r" && [ "$STRANDED" = "0" ] \
        && [[ " ${st[*]+"${st[*]}"} " != *" --no-self-test "* ]]; then
        ( step "stress_${r}_selftest" selftest nice -n 10 "$PY" "$HERE/rev_hil.py" stress --full --rate "$r" \
            ${REV_TYPE:+--type "$REV_TYPE"} --jack-dev "$JACK_DEV" --jack-ch "$JACK_CH" "${q[@]+"${q[@]}"}" \
            "${st[@]+"${st[@]}"}" --self-test-only $D ) &
        BG_PIDS+=("$!")
        st+=(--no-self-test)
    fi
    step "stress_$r" stress "$PY" "$HERE/rev_hil.py" stress --full --rate "$r" ${REV_TYPE:+--type "$REV_TYPE"} \
        --jack-dev "$JACK_DEV" --jack-ch "$JACK_CH" "${q[@]+"${q[@]}"}" "${st[@]+"${st[@]}"}" $D
    copy "$TREE/build/hil/stress_max/$r/report.txt" "stress_$r-report.txt"
    copy "$TREE/build/hil/stress_max/$r/report.json" "stress_$r-report.json"
    copy "$TREE/build/hil/stress_max/$r/fold.json" "stress_$r-fold.json"
}
stress_full 48000 --fold

# 2a. autosave_stream: the settings autosave while the USB stream runs (hil_autosave.py, 48 then 44.1 kHz): a TONE
# change, silence, the save seen in the FM-1's own CC 116 report within the rule's time, USB underruns / missed /
# overruns 0 over the whole take (AUTOSAVE_OLD=1: --old-rule, the 60 s rule's 70 s)
if [ -f "$TREE/tools/hil_autosave.py" ]; then
    rmrep "$TREE/build/hil/autosave/report.txt" "$TREE/build/hil/autosave/report.json"
    ao=(); [ "${AUTOSAVE_OLD:-0}" = "1" ] && ao=(--old-rule)
    step autosave_stream autosave "$PY" tools/hil_autosave.py --rate both "${ao[@]+"${ao[@]}"}" $D
    copy "$TREE/build/hil/autosave/report.txt" autosave_stream-report.txt
    copy "$TREE/build/hil/autosave/report.json" autosave_stream-report.json
else
    printf 'autosave_stream\tnotrun\t0\tautosave\tthis tree has no tools/hil_autosave.py\n' >> "$OUT/steps.tsv"
fi

# 2a'. save_glitch: the worst case played EXACTLY during an auto save (hil_save_glitch.py, 48 kHz): the setup save
# (CC 57 INVERSION) and the pattern's (one step-recorded note, then UNDO: the pattern as it was), each caught in CC 116
# (item 17) and attacked 0 / 30 / 60 ms later: 10 voices, every effect, the pedal, INFINITE, chords + the bend; the
# stress_max detectors on USB and the jack, the whole ISR in the save's window <= CPU_LIMIT_US, USB missed / overruns 0
if [ -f "$TREE/tools/hil_save_glitch.py" ]; then
    rmrep "$TREE/build/hil/save_glitch/report.txt" "$TREE/build/hil/save_glitch/report.json"
    step save_glitch saveglitch "$PY" tools/hil_save_glitch.py --rate 48000 --limit "$CPU_LIMIT_US" \
        --jack-dev "$JACK_DEV" --jack-ch "$JACK_CH" $D
    copy "$TREE/build/hil/save_glitch/report.txt" save_glitch-report.txt
    copy "$TREE/build/hil/save_glitch/report.json" save_glitch-report.json
else
    printf 'save_glitch\tnotrun\t0\tsaveglitch\tthis tree has no tools/hil_save_glitch.py\n' >> "$OUT/steps.tsv"
fi

# 2b. "no louder after the key-up" (TEST-PLAN, its last section): the Rhodes TYPEs x NOISE 48 / 127, v15 / v60 / v127 x
# C2 / C4 / C6 held 2 s, the key-up (and the pedal case), USB + the jack (QUICK=1: v15 only)
rq=(); [ "$QUICK" = "1" ] && rq=(--quick)
rmrep "$TREE/build/hil/release_level/48000/report.txt" "$TREE/build/hil/release_level/48000/report.json"
step release_level release "$PY" tools/hil_release_level.py --rate 48000 --jack-dev "$JACK_DEV" --jack-ch "$JACK_CH" \
    "${rq[@]+"${rq[@]}"}" $D
copy "$TREE/build/hil/release_level/48000/report.txt" release_level-report.txt
copy "$TREE/build/hil/release_level/48000/report.json" release_level-report.json

# 2c. midi_clock: the synced effects against an external MIDI clock (hil_clock.py): the tremolo's period and phase
# and the delay's echo spacing on the sent beats, a tempo change, a Stop / Start; the clock over the FM-1's USB-MIDI,
# and over the TRS jack too when FRODHES_TRS_PORT names the interface output wired to its TRS IN
if [ -f "$TREE/tools/hil_clock.py" ]; then
    cv=(--via usb); [ -n "${FRODHES_TRS_PORT:-}" ] && cv=(--via usb,trs --port "$FRODHES_TRS_PORT")
    rmrep "$TREE"/build/hil/clock/usb/report.* "$TREE"/build/hil/clock/trs/report.*
    step midi_clock clock "$PY" tools/hil_clock.py "${cv[@]}" $D
    for v in usb trs; do
        copy "$TREE/build/hil/clock/$v/report.txt" "midi_clock-$v-report.txt"
        copy "$TREE/build/hil/clock/$v/report.json" "midi_clock-$v-report.json"
    done
    copy "$TREE/build/hil/clock/summary.txt" "midi_clock-summary.txt"
else
    printf 'midi_clock\tnotrun\t0\tclock\tthis tree has no tools/hil_clock.py\n' >> "$OUT/steps.tsv"
fi

# 2d. midi_parity: USB-MIDI against the TRS MIDI input (only with FRODHES_TRS_PORT: the interface wired to TRS IN)
if [ -n "${FRODHES_TRS_PORT:-}" ] && [ -f "$TREE/tools/hil_midi_parity.py" ]; then
    rmrep "$TREE"/build/hil/midi_parity/report.*
    step midi_parity release "$PY" tools/hil_midi_parity.py --port "$FRODHES_TRS_PORT" $D
    copy "$TREE/build/hil/midi_parity/report.txt" midi_parity-report.txt
    copy "$TREE/build/hil/midi_parity/report.json" midi_parity-report.json
else
    printf 'midi_parity\tnotrun\t0\trelease\t%s\n' "$([ -n "${FRODHES_TRS_PORT:-}" ] && echo 'this tree has no tools/hil_midi_parity.py' || echo 'FRODHES_TRS_PORT not set (no interface on the TRS IN)')" >> "$OUT/steps.tsv"
fi

# 3. the CPU: the Rhodes x3 (+ the bend sweep, + SPREAD 127), VIBES, MARIMBA, every effect with the heaviest reverb
# TYPE; then every engine once with each other reverb TYPE
for k in 1 2 3; do
    step "cpu_rhodes_$k" cpu "${CPU[@]}" --takes 1 -- --worst --usb 48000
done
if [ "$BEND" = "1" ]; then
    cpu_step cpu_rhodes_bend cpu_rhodes_bend "${CPU[@]}" -- --worst --usb 48000 --bend-sweep
else
    printf 'cpu_rhodes_bend\tnotrun\t0\tcpu\thil_cpu has no --bend-sweep in this tree (the bend was dropped)\n' >> "$OUT/steps.tsv"
fi
cpu_step cpu_rhodes_spread cpu_rhodes_spread "${CPU[@]}" --spread 127 -- --worst --usb 48000
if [ "$BEND" = "1" ]; then
    cpu_step cpu_rhodes_spread_bend cpu_rhodes_spread_bend "${CPU[@]}" --spread 127 -- --worst --usb 48000 --bend-sweep
else
    printf 'cpu_rhodes_spread_bend\tnotrun\t0\tcpu\thil_cpu has no --bend-sweep in this tree (the bend was dropped)\n' >> "$OUT/steps.tsv"
fi
cpu_step cpu_vibes cpu_vibes "${CPU[@]}" -- --engine vibes --worst --usb 48000
cpu_step cpu_marimba cpu_marimba "${CPU[@]}" -- --engine marimba --worst --usb 48000
if [ -z "$OTHER_REVS" ] && [ "$DRY" != "1" ]; then
    printf 'cpu_rev_types\tnotrun\t0\tcpu\tno reverb TYPEs beyond %s in this tree\n' "${HEAVY_REV:-HALL}" >> "$OUT/steps.tsv"
fi
for t in $OTHER_REVS; do
    lt="$(echo "$t" | tr A-Z a-z)"
    cpu_step "cpu_rhodes_$lt" "cpu_rhodes_$lt" "${CPUT[@]}" --rev-type "$t" -- --worst --usb 48000
    cpu_step "cpu_vibes_$lt" "cpu_vibes_$lt" "${CPUT[@]}" --rev-type "$t" -- --engine vibes --worst --usb 48000
    cpu_step "cpu_marimba_$lt" "cpu_marimba_$lt" "${CPUT[@]}" --rev-type "$t" -- --engine marimba --worst --usb 48000
done

# 4. the key velocity converter's CPU gate (TEST-PLAN 3b; PLAN 3.4): each engine's worst scenarios only (the pedal
# ones: hil_cpu --scenario pedal) x OFF / idle / STRESS x 3 runs; the whole gate when the tree's hil_cpu has no
# --scenario yet (feature/rev-run adds it)
SC=(); (cd "$TREE" && "$PY" tools/hil_cpu.py --help 2>/dev/null | grep -q -- "--scenario") && SC=(--scenario pedal)
if [ "$SKIP_COVERED" = "1" ]; then
    covered keyadc_gate keyadc
elif [ "$DRY" = "1" ]; then
    step keyadc_gate keyadc "$PY" tools/hil_cpu.py --help
else
    step keyadc_gate keyadc "$PY" tools/hil_cpu.py --keyadc gate --worst --runs 3 "${SC[@]+"${SC[@]}"}"
    [ ${#SC[@]} -eq 0 ] && echo "keyadc_gate: this tree's hil_cpu has no --scenario: the whole gate ran (~18 min)" >> "$OUT/keyadc_gate.log"
    # TEST-PLAN 3b: an engine that FAILed on 3 runs gets 5 (its run-to-run spread, ~+-100 us, is wider than the
    # limits); the 5 decide its verdict (summarize_rev.py). 0.9.7: VIBES and MARIMBA too, the same rule as the Rhodes
    for eng in rhodes vibes marimba; do
        if [ "$(last_code keyadc_gate)" != "0" ] && want keyadc_gate && [ "$STRANDED" = "0" ] \
            && grep -q -E "^  $eng .*FAIL" "$OUT/keyadc_gate.log"; then
            step "keyadc_${eng}5" keyadc "$PY" tools/hil_cpu.py --keyadc gate --worst --runs 5 --engines "$eng" "${SC[@]+"${SC[@]}"}"
        fi
    done
fi

# 5. the reverb's own stress (unless stress_48000 had INFINITE already), then stress_max at 44.1 kHz: 2 worst takes
# x 60 s + the recipes at 30 s (PLAN 3.3 step 4; 48 kHz stays full)
if [ -n "$HEAVY_REV" ] && [ "${HEAVY_REV:0:3}" = "INF" ]; then
    printf 'stress_rev_48000\tnotrun\t0\tstress\tcovered by step stress_48000: it ran with the reverb TYPE %s (frozen in its worst takes)\n' \
        "$HEAVY_REV" >> "$OUT/steps.tsv"
else
    rmrep "$TREE/build/hil/stress_rev/48000/report.txt" "$TREE/build/hil/stress_rev/48000/report.json"
    step stress_rev_48000 stress "$PY" "$HERE/rev_hil.py" stress --type INFINITE --rate 48000 --jack-dev "$JACK_DEV" \
        --jack-ch "$JACK_CH" $D
    copy "$TREE/build/hil/stress_rev/48000/report.txt" stress_rev_48000-report.txt
    copy "$TREE/build/hil/stress_rev/48000/report.json" stress_rev_48000-report.json
fi
stress_full 44100 --short --no-self-test

# 5a. usb_onset_44100: MIDI -> USB / jack onset at 44.1 kHz (hil_usb_onset.py, 3 takes, ~1 min): stress_48000's fold
# measures the onsets at 48 kHz only; the release notes give both rates
rmrep "$TREE/build/hil/usb_onset.json"
if [ "$DRY" = "1" ]; then
    step usb_onset_44100 onset "$PY" tools/hil_usb_onset.py --help
else
    step usb_onset_44100 onset "$PY" tools/hil_usb_onset.py --rate 44100 --jack-dev "$JACK_DEV" --jack-ch "$JACK_CH"
fi
copy "$TREE/build/hil/usb_onset.json" usb_onset_44100.json

# 6. T1 S3 S4 (S2, the jack alone, is stress_48000's *-no-usb takes: the same recipe, longer)
rmrep "$TREE/build/hil/must/report.md" "$TREE/build/hil/must/report.json"
step t1_s3s4 must "$PY" tools/hil_scan.py --tier must --scenarios S3,S4 --jack-dev "$JACK_DEV" --jack-ch "$JACK_CH" $D
copy "$TREE/build/hil/must/report.md" t1_s3s4-report.md
copy "$TREE/build/hil/must/report.json" t1_s3s4-report.json
printf 't1_s2\tnotrun\t0\tmust\tcovered by step stress_48000: T1 S2 (the jack alone, no USB stream) is its *-no-usb takes, the same recipe for 58.6 s per engine instead of 34 s, judged by the same detectors (test-speed plan 3.3)\n' >> "$OUT/steps.tsv"

# 7. the folded jack steps (PLAN 3.3 step 3): rows naming stress_48000's fold; one hil_jack on the Rhodes as the
# cross-check of the folded numbers (CROSSCHECK=1, the plan's P3 side-by-side, until it is proven)
for f in silence:"hil_scan silence: the USB peak 0 at idle (the jack check's 3 s idle, gated)" \
         jack_vibes:"hil_jack's silence / hiss / loud chord / harmonics, VIBES (the jack check take)" \
         jack_marimba:"hil_jack's silence / hiss / loud chord / harmonics, MARIMBA (the jack check take)" \
         tape_jack:"a TAPE recipe per engine with the moderate effects, judged by every detector" \
         usb_onset:"MIDI -> USB / jack onset at 48 kHz, clock_fit over the jack check and TAPE takes' onsets (44.1 kHz: step usb_onset_44100)"; do
    n="${f%%:*}"; printf '%s\tnotrun\t0\tjack\tcovered by step stress_48000: folded (stress_48000-fold.json): %s\n' "$n" "${f#*:}" >> "$OUT/steps.tsv"
done
if [ "$CROSSCHECK" = "1" ]; then
    step jack_rhodes jack "$PY" tools/hil_jack.py --engine rhodes --dev "$JACK_DEV" --ch "$JACK_CH" $D
else
    printf 'jack_rhodes\tnotrun\t0\tjack\tcovered by step stress_48000: folded (stress_48000-fold.json), the jack check take\n' >> "$OUT/steps.tsv"
fi

# 8. the reverb TYPEs on the jack (never cut: the jack takes)
rmrep "$TAKES/48000/rev_jack.txt" "$TAKES/48000/rev_jack.json"
step rev_jack revjack "$PY" "$HERE/rev_hil.py" jack --rate 48000 --seconds 14 --out "$TAKES" --jack-dev "$JACK_DEV" \
    --jack-ch "$JACK_CH" $D
copy "$TAKES/48000/rev_jack.txt" rev_jack-report.txt
copy "$TAKES/48000/rev_jack.json" rev_jack-report.json

# 9. T1 S5 LAST (a harness problem in S5 cannot strand the run): 0.9.4 installed, a panel saved, the release over it,
# compared with the same panel sent to the release itself (hil_must's reference: the build under test)
if [ "$SKIP_COVERED" = "1" ]; then
    covered s5_install must
else
    rmrep "$TREE/build/hil/must/report.md" "$TREE/build/hil/must/report.json"
    step s5_install must "$PY" tools/hil_scan.py --tier must --scenarios S5 --fwsc "$FW" --prev "$PREV" \
        --jack-dev "$JACK_DEV" --jack-ch "$JACK_CH" $D
    copy "$TREE/build/hil/must/report.md" s5_install-report.md
    copy "$TREE/build/hil/must/report.json" s5_install-report.json
    if [ "$DRY" != "1" ] && want s5_install && [ "$(last_code s5_install)" != "0" ]; then
        if grep -q "IS LEFT ON" "$OUT/s5_install.log" || ! grep -q -E "^T1 (PASS|FAIL|INCOMPLETE)" "$OUT/s5_install.log"; then
            echo "*** S5 did not finish with the release installed: install it again (RESUME=1 does) ***"
        fi
    fi
    [ "$DRY" != "1" ] && want s5_install && ALWAYS=1 step fm1_info_end info "$PY" tools/fm1_install.py --info
fi

# 10. the pattern-save power cycle: by hand (the power switch; no MIDI read-back of the pattern)
PATTERN_STEPS="needs the user: (1) SEQ page, record a short pattern (4 notes), stop, wait 3 s silent; (2) switch the FM-1 off and on: the pattern plays back the same (sector A); (3) add 2 notes, stop, wait 3 s; (4) power cycle: the 6 notes are there (sector B); (5) edit again, wait 3 s, power cycle: the edit is there (A again); (6) hold SEQ 1 s to clear it, wait 3 s, power cycle: empty. No USB recording open meanwhile (the saves wait for it)."
printf 'pattern_power_cycle\tmanual\t0\tmanual\t%s\n' "$PATTERN_STEPS" >> "$OUT/steps.tsv"

finish
