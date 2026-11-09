#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# tools/tail_build.sh REF [--ver X.Y.Z] [--out DIR] [--keep] [--dry-run]
#
# The release's tail after a firmware candidate, host only (no device step): from the commit REF (a branch, a tag or a
# sha), in a clean detached worktree of it (build/tail/wt-<sha>), every package that is not the FM-1 image, and the
# host checks on them:
#   1 host     make host (the host build and its HQ twin: the preset renders and make quick need them)
#   2 presets  the preset lists, from presets.c: tools/gen_move_presets.py --sync brings the Move's records
#              (move/presets/proposed_records.txt: F01-F19's A, the variants F20-F57) to presets.c, the B / C with
#              their A (P_LEVEL by the same step), and rewrites move/move_presets.h (the Move's and the plugin's
#              F20-F57); a change is saved as presets.patch, for a commit on the firmware branch. Then the --check of
#              every generated table: gen_move_presets, gen_move_module (the Move's pages + preset browser),
#              gen_plugin_params (the plugin's parameter map; its PRESET list is presets.c + move_presets.h)
#   then in parallel, each with its log in OUT:
#   move     tools/ci_move.sh (gate U, make move test-move with MOVE_VER=X, Gate M, the Move's pytest):
#            build/move/frodhes-module.tar.gz
#   quick    make quick (the FM-1, HQ and Move host checks)
#   mac      make plugin-pack (CLAP, VST3, AU; universal; .pkg + .zip), make test-plugin, tools/plugin_os.py --quick
#            and tools/plugin_timing.py --quick (P6: the oversampler, events on their exact sample)
#   win      make plugin-win (clang-cl over xwin: build, depcheck, the wine tests, the zip)
#   linux    make plugin-linux (Docker: x86_64 then aarch64, build, depcheck, the Xvfb tests, the zips)
# Out (OUT, default build/tail/<sha>): the packages, the Move tarball, every log, SHA256SUMS, summary.txt (each step's
# result and time). Exit 0 only when every step passed. Light by default: nice, -j2, PYTEST_N=2.
#   --keep     keep the worktree afterwards (default: removed when every step passed)
#   --dry-run  print the plan only
set -u
M=$(cd "$(dirname "$0")/.." && pwd)
MAIN=$(cd "$(git -C "$M" rev-parse --git-common-dir)/.." && pwd)     # (the main checkout: .local lives there)
REF=""; VER=0.9.10; OUT=""; KEEP=0; DRY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --ver) VER=$2; shift ;;
        --out) OUT=$2; shift ;;
        --keep) KEEP=1 ;;
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '3,30p' "$0"; exit 0 ;;
        *) REF=$1 ;;
    esac
    shift
done
[ -n "$REF" ] || { echo "tail_build: no REF (a branch, tag or sha)" >&2; exit 2; }
SHA=$(git -C "$M" rev-parse --short=8 "$REF^{commit}") || { echo "tail_build: unknown REF $REF" >&2; exit 2; }
OUT=${OUT:-$MAIN/build/tail/$SHA}
WT=$MAIN/build/tail/wt-$SHA
PY=${PY:-$MAIN/build/t3/venv/bin/python}
[ -x "$PY" ] || PY=python3
export PY PYTEST_N=${PYTEST_N:-2} MOVE_VER=$VER FRP_VERSION=$VER
NICE="nice -n 15"
LANES="move quick mac win linux"

if [ $DRY = 1 ]; then
    echo "tail_build: $REF = $SHA, version $VER; worktree $WT; out $OUT; python $PY"
    echo "  1 host     make host"
    echo "  2 presets  gen_move_presets.py --sync (diff: presets.patch); --check: gen_move_presets, gen_move_module,"
    echo "             gen_plugin_params"
    echo "  3 parallel: $LANES"
    exit 0
fi

mkdir -p "$OUT" || exit 2
T0=$(date +%s)
SUM=$OUT/summary.txt
echo "tail_build $REF = $SHA, version $VER, $(date '+%Y-%m-%d %H:%M')" > "$SUM"
note() { echo "$*" | tee -a "$SUM"; }
fail=0
step() {  # $1 name, then the command (in the worktree); its output to OUT/<name>.log
    local name=$1 s rc; shift
    s=$(date +%s)
    ( cd "$WT" && $NICE "$@" ) > "$OUT/$name.log" 2>&1
    rc=$?
    note "$( [ $rc = 0 ] && echo ok || echo FAIL)  $name ($(( $(date +%s) - s )) s): $*"
    return $rc
}

# the worktree: a clean checkout of REF, the main checkout's .local (toolchains, plugin deps) linked in
if [ ! -d "$WT" ]; then
    git -C "$M" worktree add -q --detach "$WT" "$SHA" || exit 2
fi
[ "$(git -C "$WT" rev-parse --short=8 HEAD)" = "$SHA" ] || { echo "tail_build: $WT is not at $SHA" >&2; exit 2; }
[ -e "$WT/.local" ] || ln -s "$MAIN/.local" "$WT/.local"
# (a REF older than gen_move_presets.py --sync: this tree's copy, the change kept out of presets.patch)
if ! grep -q -- "--sync" "$WT/tools/gen_move_presets.py"; then
    cp "$M/tools/gen_move_presets.py" "$WT/tools/gen_move_presets.py"
    note "      (tools/gen_move_presets.py from $(git -C "$M" rev-parse --short=8 HEAD): $REF has no --sync)"
fi

step host make -j2 host build/host-hq/frodhes || fail=1

# the preset lists: the Move's F20-F57 records follow presets.c (tools/gen_move_presets.py --sync, no render), then
# every generated table is checked
if [ $fail = 0 ]; then
    step presets-sync "$PY" tools/gen_move_presets.py --sync || fail=1
    git -C "$WT" diff -- move/presets move/move_presets.h > "$OUT/presets.patch"
    if [ -s "$OUT/presets.patch" ]; then
        note "      presets: CHANGED with presets.c: $(grep '^+# ' "$OUT/presets.patch" | sed 's/^+# //'); presets.patch (commit it on the branch)"
    else
        note "      presets: the Move's records are presets.c's already"
    fi
    step presets-check-move "$PY" tools/gen_move_presets.py --check || fail=1
    step presets-check-module "$PY" tools/gen_move_module.py --check || fail=1
    step presets-check-plugin "$PY" tools/gen_plugin_params.py --check || fail=1
fi

if [ $fail = 0 ]; then
    lane() {
        case "$1" in
            move)  step move-ci sh tools/ci_move.sh ;;     # (its make move takes MOVE_VER from the environment)
            quick) step quick make quick ;;
            mac)   step mac-pack make plugin-pack PLUGIN_VER="$VER" && step mac-test make test-plugin &&
                   step mac-os "$PY" tools/plugin_os.py --quick && step mac-timing "$PY" tools/plugin_timing.py --quick ;;
            win)   step win make plugin-win ;;
            linux) step linux make plugin-linux ;;
        esac
    }
    pids=""
    for l in $LANES; do lane "$l" & pids="$pids $!"; done
    for p in $pids; do wait "$p" || fail=1; done
fi

# what came out
for f in "$WT"/build/plugin-pkg/* "$WT"/build/move/frodhes-module.tar.gz; do
    [ -f "$f" ] && cp "$f" "$OUT/"
done
( cd "$OUT" && shasum -a 256 ./*.zip ./*.pkg ./*.tar.gz 2>/dev/null > SHA256SUMS )
[ -f "$WT/build/move/frodhes/engine.so" ] && note "      move engine.so $(shasum -a 256 "$WT/build/move/frodhes/engine.so" | cut -c1-16)"
note "$( [ $fail = 0 ] && echo PASS || echo FAIL): tail_build $SHA in $(( ($(date +%s) - T0) / 60 )) min $(( ($(date +%s) - T0) % 60 )) s; out $OUT"
if [ $fail = 0 ] && [ $KEEP = 0 ] && [ ! -s "$OUT/presets.patch" ]; then
    git -C "$M" worktree remove "$WT" 2>/dev/null || note "      (the worktree $WT stays: not clean)"
fi
exit $fail
