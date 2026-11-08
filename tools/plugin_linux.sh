#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# The desktop plugin for Linux x86_64 or aarch64 (CLAP + VST3), built in Docker on Ubuntu 20.04 (glibc 2.31) with
# clang 12 + lld. FRP_LINUX_ARCH=x86_64 (the default; build/plugin-linux) or aarch64 (build/plugin-linux-aarch64;
# native on Apple silicon and CI's arm runners, emulated elsewhere). CI runs this same script (.github/workflows/plugin.yml).
#
#   tools/plugin_linux.sh build   build/plugin-linux/bundles/Frodhes.clap and VST3/Frodhes.vst3/ + the tests
#   tools/plugin_linux.sh check   tools/plugin_depcheck.py linux (the libraries needed: libc, libm, libpthread, libdl,
#                                 librt, ld-linux, libX11 / libxcb only; no glibc symbol newer than 2.31; the entry
#                                 points) in the container, then readelf: the architecture, nothing exported but the
#                                 entry points, a non-executable stack
#   tools/plugin_linux.sh test    in the same container: the coroutine, the window's controller, the X11 view's
#                                 self-test under Xvfb, the CLAP host's --selftest and a Gate D subset (CLASSIC,
#                                 44.1 kHz) against the host build's sha256
#   tools/plugin_linux.sh pack    build/plugin-pkg/frodhes-plugin-linux-<arch>.zip (tools/plugin_pack.sh linux)
#   tools/plugin_linux.sh all     build, check, test, pack
# The dependencies come from .local/plugin-deps (tools/plugin_deps.sh), mounted read-only. One Docker job at a time
# (tools/lock.py DOCKER), under build/cpuq.py; the image is amd64 (on Apple silicon: emulated, slower).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="$(cd "$(git -C "$ROOT" rev-parse --git-common-dir)/.." && pwd)"
LOCAL="$MAIN/.local"
ARCH="${FRP_LINUX_ARCH:-x86_64}"
case "$ARCH" in
    x86_64) PLAT=linux/amd64; B="$ROOT/build/plugin-linux"; RE='X86-64' ;;
    aarch64) PLAT=linux/arm64; B="$ROOT/build/plugin-linux-aarch64"; RE='AArch64' ;;
    *) echo "plugin_linux: FRP_LINUX_ARCH x86_64 or aarch64" >&2; exit 2 ;;
esac
IMG=frodhes-plugin-linux:focal-${PLAT#linux/}
CPUQ=()
[ -f "$MAIN/build/cpuq.py" ] && CPUQ=(python3 "$MAIN/build/cpuq.py" --why "plugin_linux $1" --)
LOCKRUN=()
[ -f "$MAIN/tools/lock.py" ] && LOCKRUN=(python3 "$MAIN/tools/lock.py" run DOCKER --why "plugin_linux $1" --wait 3600 --)

image() {
    docker image inspect "$IMG" >/dev/null 2>&1 && return 0
    "${LOCKRUN[@]}" "${CPUQ[@]}" nice -n 15 docker build -q --platform "$PLAT" -t "$IMG" \
        -f "$ROOT/plugin/tests/docker/linux.Dockerfile" "$ROOT/plugin/tests/docker"
}
in_docker() {   # a command in the container, the repo and the dependencies at their own paths
    "${LOCKRUN[@]}" "${CPUQ[@]}" nice -n 15 docker run --rm --platform "$PLAT" -v "$ROOT:$ROOT" \
        -v "$LOCAL/plugin-deps:$LOCAL/plugin-deps:ro" -w "$ROOT" "$IMG" bash -c "$1"
}

build() {
    image
    in_docker "cmake -S plugin -B '$B' -G Ninja -DCMAKE_C_COMPILER=clang-12 -DCMAKE_CXX_COMPILER=clang++-12 \
        -DCMAKE_AR=/usr/bin/llvm-ar-12 -DCMAKE_RANLIB=/usr/bin/llvm-ranlib-12 -DCMAKE_NM=/usr/bin/llvm-nm-12 \
        -DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=lld-12 -DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=lld-12 \
        -DCMAKE_MODULE_LINKER_FLAGS=-fuse-ld=lld-12 -DFRP_DEPS_DIR='$LOCAL/plugin-deps' ${FRP_VERSION:+-DFRP_VERSION=$FRP_VERSION} \
        >/dev/null && nice -n 15 cmake --build '$B' -j2"
}

check() {
    image
    in_docker "set -e; python3 tools/plugin_depcheck.py linux '$B/bundles' --glibc 2.31; fail=0
    for f in '$B/bundles/Frodhes.clap' '$B/bundles/Frodhes.vst3/Contents/$ARCH-linux/Frodhes.so'; do
        n=\$(basename \"\$f\")
        readelf -h \"\$f\" | grep -q '$RE' || { echo \"FAIL \$n not $ARCH\"; fail=1; }
        for s in \$(nm -D --defined-only \"\$f\" | awk '{print \$3}'); do case \$s in clap_entry|GetPluginFactory|ModuleEntry|ModuleExit) ;;
            *) echo \"FAIL \$n exports \$s\"; fail=1 ;; esac; done
        readelf -lW \"\$f\" | grep GNU_STACK | grep -q RWE && { echo \"FAIL \$n: executable stack\"; fail=1; } || true
    done
    [ \$fail = 0 ] && echo 'ok    check: $ARCH, exports, stack'; exit \$fail"
}

pack() { sh "$ROOT/tools/plugin_pack.sh" linux "$B"; }

test_linux() {
    image
    local T="$ROOT/build/plugin-linux-test"
    rm -rf "$T"; mkdir -p "$T/out"
    python3 - "$ROOT" "$T" "$B" <<'PY'
import sys
from pathlib import Path
root, t, b = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(root / "tools"))
import gate_m, gate_d
midi, _ = gate_m.entries()
base, want16 = gate_m.read_sums(gate_m.BASE), gate_m.read_sums(gate_m.BASE16)
sub = gate_d.subset(midi, 8)
with open(t / "renders.sh", "w") as f, open(t / "want.txt", "w") as w:
    for n, script in sub:
        o = t / "out" / (n.replace("/", "__") + ".wav")
        f.write(f'"{b}/frp_clap_render" "{b}/bundles/Frodhes.clap" "{root / script}" "{o}" --engine classic --block 512 >/dev/null 2>&1 || echo "render failed: {n}"\n')
        w.write(f"{n} {base.get(n)}\n")   # (CLASSIC: the FM-1's build, its Gate R render)
print(f"plugin_linux: {len(sub)} Gate D renders")
PY
    in_docker "cd '$T'
    echo '== coroutine';  '$B/frp_test_coro' 2>&1 | tail -3
    echo '== controller'; '$B/frp_test_gui_ctl' 2>&1 | grep -v '^PASS ' | tail -5
    echo '== view';       xvfb-run -a -s '-screen 0 1920x1200x24' '$B/frp_gui_demo' --selftest 2>&1 | tail -12
    echo '== clap host';  '$B/frp_clap_render' '$B/bundles/Frodhes.clap' --selftest 2>&1 | tail -4
    echo '== gate d';     . '$T/renders.sh'" | tee "$T/log.txt"
    python3 - "$T" <<'PY'
import hashlib, sys
from pathlib import Path
t = Path(sys.argv[1]); ok = bad = 0
for line in (t / "want.txt").read_text().splitlines():
    n, want = line.split()
    p = t / "out" / (n.replace("/", "__") + ".wav")
    got = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"
    if got == want: ok += 1
    else: bad += 1; print(f"FAIL gate d {n}: {got[:12]} want {want[:12]}")
print(f"{'ok   ' if not bad else 'FAIL '} gate d on Linux: {ok} identical, {bad} differ")
sys.exit(1 if bad else 0)
PY
    if grep -q "FAIL" "$T/log.txt"; then echo "plugin_linux: failures above"; return 1; fi
}

case "${1:-}" in
    build) build ;;
    check) check ;;
    test) test_linux ;;
    pack) pack ;;
    all) build; check; test_linux; pack ;;
    *) sed -n '3,20p' "$0"; exit 2 ;;
esac
