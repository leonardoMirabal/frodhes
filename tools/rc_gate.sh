#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# The release candidate's device gate queue (AGENTS.md section 9, steps 2-3; tools/release.py's T1 / T2), one step
# after the other on the FM-1, each step's log and exit code kept:
#
#   tools/rc_gate.sh VERSION PREV_FWSC [--from STEP] [--only STEP[,STEP]] [--dry-run]
#
#   VERSION     the candidate (this tree's build/frodhes-VERSION.fwsc, or PKG=...; built with ./build.sh --release
#               VERSION: the candidate is the release build)
#   PREV_FWSC   the previous release's package (T1's --prev)
#
# Steps (names for --from / --only):
#   install    the candidate installed, its identity read back
#   t1         hil_scan --tier must --fwsc PKG --prev PREV (must PASS, not INCOMPLETE)
#   crackle44  hil_crackle --engine all --rate 44100 (full length, USB + jack)
#   crackle48  hil_crackle --engine all --rate 48000
#   cpu        hil_cpu (the default run), then hil_cpu --worst --usb 48000 x3 for each engine (whole ISR <= 4500 us,
#              nothing shed, 0 USB underruns / missed / overruns)
#   monkey     hil_monkey 20 min at 48 kHz, seeds 3000, 3100, 3200, 3300 (each take opens the USB stream again:
#              with s3 this is the "USB missed" soak)
#   s3         T1's S3 (preset and TYPE switching, the first 0.9.9 candidate's "USB missed 1") 3 more times
#   t2         private/release-095/t2_run.py (release.py's T2) on the package
#   autosave   hil_autosave --rate both --rounds 10
#
# Logs: the main checkout's build/rc-VERSION/ (or RC_OUT=DIR): <step>.log, gate-summary.txt (step, exit code,
# time). The whole queue holds DEVICE (tools/lock.py: the tools inherit it). Before it: the FM-1 on MIDI CHANNEL ALL
# (or set FRODHES_MIDI_CH), the sandbox off (CoreAudio), the MOTU M6 connected for the jack takes. Exit 0 only if
# every step run exited 0.
set -u
V=${1:?VERSION}; PREV=${2:?PREV_FWSC}; shift 2
FROM=; ONLY=; DRY=0
while [ $# -gt 0 ]; do
  case $1 in
    --from) FROM=$2; shift 2 ;;
    --only) ONLY=,$2,; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
R=$(cd "$(dirname "$0")/.." && pwd)
PY=${PY:-python3}
[ -x "$R/.local/venv/bin/python" ] && PY=$R/.local/venv/bin/python
MAIN=$(dirname "$(git -C "$R" rev-parse --path-format=absolute --git-common-dir)")   # (the main checkout: private/, the logs)
PKG=${PKG:-$R/build/frodhes-$V.fwsc}
OUT=${RC_OUT:-$MAIN/build/rc-$V}
SUM=$OUT/gate-summary.txt
STEPS="install t1 crackle44 crackle48 cpu monkey s3 t2 autosave"
cd "$R" || exit 2
if [ $DRY = 0 ]; then
  [ -f "$PKG" ] || { echo "no $PKG (./build.sh --release $V)" >&2; exit 2; }
  [ -f "$PREV" ] || { echo "no $PREV (the previous release's .fwsc)" >&2; exit 2; }
  mkdir -p "$OUT"
  if [ -z "${FRODHES_LOCK_DEVICE:-}" ]; then          # the whole queue under one DEVICE hold (the tools inherit it)
    exec $PY tools/lock.py run DEVICE --why "rc_gate $V" --wait 14400 -- "$0" "$V" "$PREV" \
      ${FROM:+--from "$FROM"} ${ONLY:+--only "${ONLY:1:${#ONLY}-2}"}
  fi
  echo "== rc_gate $V $(date '+%F %T') tree $(git log -1 --format=%h) package $(shasum -a 256 "$PKG" | cut -c1-16)" >> "$SUM"
fi

want() {                                              # want STEP: run it (--from / --only)
  if [ -n "$ONLY" ]; then case $ONLY in *,$1,*) return 0 ;; *) return 1 ;; esac; fi
  if [ -n "$FROM" ]; then
    case " $STEPS " in *" $FROM "*) ;; *) echo "unknown step $FROM" >&2; exit 2 ;; esac
    [ "$1" = "$FROM" ] && FROM=
    [ -z "$FROM" ] && return 0 || return 1
  fi
  return 0
}
FAILS=0
run() {                                               # run STEP LOGNAME CMD...: one command, its log, its exit code
  local step=$1 log=$2 t0 rc; shift 2
  if [ $DRY = 1 ]; then echo "[$step] $*"; return 0; fi
  t0=$(date +%s)
  echo "$(date '+%T') [$step] $*" | tee -a "$SUM"
  "$@" > "$OUT/$log.log" 2>&1; rc=$?
  echo "$(date '+%T') [$step] $log exit $rc ($(( $(date +%s) - t0 )) s)" | tee -a "$SUM"
  [ $rc = 0 ] || FAILS=$((FAILS + 1))
  return $rc
}

want install && {
  run install install sh -c "echo y | $PY tools/fm1_install.py '$PKG'"
  run install info $PY tools/fm1_install.py --info
}
want t1 && run t1 t1_must $PY tools/hil_scan.py --tier must --fwsc "$PKG" --prev "$PREV"
want crackle44 && run crackle44 hil_crackle_44100 $PY tools/hil_crackle.py --engine all --rate 44100
want crackle48 && run crackle48 hil_crackle_48000 $PY tools/hil_crackle.py --engine all --rate 48000
want cpu && {
  run cpu hil_cpu $PY tools/hil_cpu.py
  for e in rhodes vibes marimba; do
    for k in 1 2 3; do
      run cpu hil_cpu_worst_${e}_$k $PY tools/hil_cpu.py --worst --usb 48000 $([ $e = rhodes ] || echo --engine $e)
    done
  done
}
want monkey && for s in 3000 3100 3200 3300; do
  run monkey hil_monkey_$s $PY tools/hil_monkey.py --minutes 20 --seed $s --rate 48000 --out "$OUT/monkey-$s"
done
want s3 && for k in 1 2 3; do
  run s3 t1_s3_$k $PY tools/hil_scan.py --tier must --scenarios S3
done
want t2 && run t2 t2 $PY "$MAIN/private/release-095/t2_run.py" --tree "$R" -o "$OUT/t2" "$PKG"
want autosave && run autosave hil_autosave $PY tools/hil_autosave.py --rate both --rounds 10 --out "$OUT/autosave"
[ $DRY = 1 ] && exit 0
echo "== rc_gate $V done $(date '+%T'): $FAILS step(s) failed" | tee -a "$SUM"
[ $FAILS = 0 ]
