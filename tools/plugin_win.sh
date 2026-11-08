#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# The desktop plugin for Windows x64 (CLAP + VST3), cross-built on macOS (or Linux) with clang-cl + lld-link.
#
#   tools/plugin_win.sh setup    the toolchain, once: Homebrew llvm@20 + lld@20 (clang-cl, lld-link, llvm-lib/rc/mt/nm),
#                                xwin 0.10.0 (cargo) and its splat of the Windows SDK + MSVC CRT, x86_64 only, into
#                                .local/win-toolchain/sdk (~630 MB; xwin asks to accept Microsoft's licence: --accept-
#                                license is passed, read https://go.microsoft.com/fwlink/?LinkId=2086102 first)
#   tools/plugin_win.sh build    build/plugin-win/bundles/CLAP/Frodhes.clap and VST3/Frodhes.vst3/ + the test programs
#   tools/plugin_win.sh check    tools/plugin_depcheck.py windows: x86_64 PE, the CLAP / VST3 exports, only Windows'
#                                own DLLs imported (the static CRT: no VCRUNTIME, no UCRT DLLs)
#   tools/plugin_win.sh test     under wine in Docker (amd64, plugin/tests/docker/wine.Dockerfile): the coroutine, the
#                                window's controller, the view's self-test (Xvfb), the CLAP host's --selftest and a
#                                Gate D subset (CLASSIC, 44.1 kHz) against the host build's sha256. wine is not
#                                Windows: a pass is a smoke test, not a release check.
#   tools/plugin_win.sh pack     build/plugin-pkg/frodhes-plugin-windows.zip (tools/plugin_pack.sh windows)
#   tools/plugin_win.sh all      build, check, test, pack
# On a Windows machine none of this is needed: cmake -S plugin -B build/plugin -G Ninja with clang-cl in PATH
# (CC=clang-cl CXX=clang-cl), plus the same dependencies (tools/plugin_deps.sh).
# Light: every build under nice -n 15 and build/cpuq.py when it exists; Docker under tools/lock.py DOCKER.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="$(cd "$(git -C "$ROOT" rev-parse --git-common-dir)/.." && pwd)"     # (a worktree: the main checkout)
LOCAL="$MAIN/.local"
TC="$LOCAL/win-toolchain"
B="$ROOT/build/plugin-win"
LLVM="${FRP_LLVM_BIN:-/opt/homebrew/opt/llvm@20/bin}"
LLD="${FRP_LLD_BIN:-/opt/homebrew/opt/lld@20/bin}"
IMG=frodhes-wine:bookworm-amd64
CPUQ=()
[ -f "$MAIN/build/cpuq.py" ] && CPUQ=(python3 "$MAIN/build/cpuq.py" --why "plugin_win $1" --)
LOCKRUN=()
[ -f "$MAIN/tools/lock.py" ] && LOCKRUN=(python3 "$MAIN/tools/lock.py" run DOCKER --why "plugin_win $1" --wait 3600 --)

setup() {
    [ -x "$LLVM/clang-cl" ] || brew install llvm@20
    [ -x "$LLD/lld-link" ] || brew install lld@20
    if [ ! -x "$TC/xwin-bin/bin/xwin" ]; then
        mkdir -p "$TC"
        nice -n 15 cargo install xwin --version 0.10.0 --locked -j2 --root "$TC/xwin-bin"
    fi
    if [ ! -d "$TC/sdk/sdk/lib/um/x86_64" ]; then
        (cd "$TC" && nice -n 15 ./xwin-bin/bin/xwin --accept-license --arch x86_64 --cache-dir xwin-cache splat --output sdk)
        rm -rf "$TC/xwin-cache"
    fi
    echo "plugin_win: toolchain ready in $TC (clang-cl: $LLVM, lld-link: $LLD)"
}

build() {
    [ -d "$TC/sdk/sdk/lib/um/x86_64" ] || { echo "plugin_win: run tools/plugin_win.sh setup first" >&2; exit 1; }
    nice -n 15 cmake -S "$ROOT/plugin" -B "$B" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$ROOT/plugin/cmake/win-x64-clangcl.cmake" \
        -DFRP_XWIN="$TC/sdk" -DFRP_LLVM_BIN="$LLVM" -DFRP_LLD_BIN="$LLD" -DFRP_DEPS_DIR="$LOCAL/plugin-deps" \
        ${FRP_VERSION:+-DFRP_VERSION="$FRP_VERSION"} >/dev/null
    "${CPUQ[@]}" nice -n 15 cmake --build "$B" -j2
}

check() {
    FRP_OBJDUMP="${FRP_OBJDUMP:-$LLVM/llvm-objdump}" python3 "$ROOT/tools/plugin_depcheck.py" windows "$B/bundles"
}

pack() { sh "$ROOT/tools/plugin_pack.sh" windows "$B"; }

wine_image() {
    docker image inspect "$IMG" >/dev/null 2>&1 && return 0
    "${LOCKRUN[@]}" "${CPUQ[@]}" nice -n 15 docker build -q --platform linux/amd64 -t "$IMG" \
        -f "$ROOT/plugin/tests/docker/wine.Dockerfile" "$ROOT/plugin/tests/docker"
}

test_wine() {
    wine_image
    local T="$ROOT/build/plugin-win-test"
    rm -rf "$T"; mkdir -p "$T/out"
    # a Gate D subset: CLASSIC 44.1 kHz, every 8th MIDI-driven Gate R entry, against its baseline sha256
    python3 - "$ROOT" "$T" <<'EOF'
import sys
from pathlib import Path
root, t = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root / "tools"))
import gate_m, gate_d
midi, _ = gate_m.entries()
base, want16 = gate_m.read_sums(gate_m.BASE), gate_m.read_sums(gate_m.BASE16)
sub = gate_d.subset(midi, 8)
z = lambda p: "Z:" + str(p)
with open(t / "renders.sh", "w") as f, open(t / "want.txt", "w") as w:
    for e in sub:
        n, script = e[0], root / e[1]
        want = want16.get(n, base.get(n))
        o = t / "out" / (n.replace("/", "__") + ".wav")
        f.write(f'wine "$B/frp_clap_render.exe" "{z("$CLAP")}" "{z(script)}" "{z(o)}" --engine classic --block 512 >/dev/null 2>&1 || echo "render failed: {n}"\n')
        w.write(f"{n} {want}\n")
print(f"plugin_win: {len(sub)} Gate D renders under wine")
EOF
    sed -i.bak "s|\"Z:\$CLAP\"|\"Z:$B/bundles/CLAP/Frodhes.clap\"|" "$T/renders.sh" && rm -f "$T/renders.sh.bak"
    cat > "$T/run.sh" <<EOF
set -u
B="$B"
cd "$T"
echo "== coroutine";  wine "\$B/frp_test_coro.exe" 2>&1 | tail -3
echo "== controller"; wine "\$B/frp_test_gui_ctl.exe" 2>&1 | grep -v '^PASS ' | tail -5
echo "== view";       xvfb-run -a -s "-screen 0 1920x1200x24" wine "\$B/frp_gui_demo.exe" --selftest >/dev/null 2>&1; cat frp_gui_demo.log
echo "== clap host";  wine "\$B/frp_clap_render.exe" "Z:\$B/bundles/CLAP/Frodhes.clap" --selftest 2>&1 | tail -4
echo "== gate d";     . "$T/renders.sh"
EOF
    "${LOCKRUN[@]}" "${CPUQ[@]}" nice -n 15 docker run --rm --platform linux/amd64 -v "$ROOT:$ROOT" -v "$MAIN/.local:$MAIN/.local:ro" \
        "$IMG" bash "$T/run.sh" | tee "$T/log.txt"
    python3 - "$T" <<'EOF'
import hashlib, sys
from pathlib import Path
t = Path(sys.argv[1]); ok = bad = 0
for line in (t / "want.txt").read_text().splitlines():
    n, want = line.split()
    p = t / "out" / (n.replace("/", "__") + ".wav")
    got = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"
    if got == want: ok += 1
    else: bad += 1; print(f"FAIL gate d {n}: {got[:12]} want {want[:12]}")
print(f"{'ok   ' if not bad else 'FAIL '} gate d under wine: {ok} identical, {bad} differ")
sys.exit(1 if bad else 0)
EOF
    grep -q "FAIL" "$T/log.txt" && { echo "plugin_win: failures above"; return 1; } || true
}

case "${1:-}" in
    setup) setup ;;
    build) build ;;
    check) check ;;
    test) test_wine ;;
    pack) pack ;;
    all) build; check; test_wine; pack ;;
    *) sed -n '3,22p' "$0"; exit 2 ;;
esac
