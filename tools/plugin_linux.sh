#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# The desktop plugin for Linux x86_64 (CLAP + VST3), built in Docker on Ubuntu 20.04 (glibc 2.31) with clang 12 + lld.
#
#   tools/plugin_linux.sh build   build/plugin-linux/bundles/Frodhes.clap and VST3/Frodhes.vst3/ + the tests
#   tools/plugin_linux.sh check   readelf: x86_64 ELF, the CLAP / VST3 exports and nothing else, the libraries needed
#                                 (libc, libm, libpthread, libdl, librt, ld-linux only: the C++ runtime is linked in),
#                                 no glibc symbol newer than 2.31, a non-executable stack
#   tools/plugin_linux.sh test    in the same container: the coroutine, the window's controller, the X11 view's
#                                 self-test under Xvfb, the CLAP host's --selftest and a Gate D subset (CLASSIC,
#                                 44.1 kHz) against the host build's sha256
#   tools/plugin_linux.sh all     build, check, test
# The dependencies come from .local/plugin-deps (tools/plugin_deps.sh), mounted read-only. One Docker job at a time
# (tools/lock.py DOCKER), under build/cpuq.py; the image is amd64 (on Apple silicon: emulated, slower).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="$(cd "$(git -C "$ROOT" rev-parse --git-common-dir)/.." && pwd)"
LOCAL="$MAIN/.local"
B="$ROOT/build/plugin-linux"
IMG=frodhes-plugin-linux:focal-amd64
CPUQ=()
[ -f "$MAIN/build/cpuq.py" ] && CPUQ=(python3 "$MAIN/build/cpuq.py" --why "plugin_linux $1" --)
LOCKRUN=()
[ -f "$MAIN/tools/lock.py" ] && LOCKRUN=(python3 "$MAIN/tools/lock.py" run DOCKER --why "plugin_linux $1" --wait 3600 --)

image() {
    docker image inspect "$IMG" >/dev/null 2>&1 && return 0
    "${LOCKRUN[@]}" "${CPUQ[@]}" nice -n 15 docker build -q --platform linux/amd64 -t "$IMG" \
        -f "$ROOT/plugin/tests/docker/linux.Dockerfile" "$ROOT/plugin/tests/docker"
}
in_docker() {   # a command in the container, the repo and the dependencies at their own paths
    "${LOCKRUN[@]}" "${CPUQ[@]}" nice -n 15 docker run --rm --platform linux/amd64 -v "$ROOT:$ROOT" \
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
    in_docker "set -e; fail=0
    for f in '$B/bundles/Frodhes.clap' '$B/bundles/Frodhes.vst3/Contents/x86_64-linux/Frodhes.so'; do
        [ -f \"\$f\" ] || { echo \"FAIL missing \$f\"; fail=1; continue; }
        n=\$(basename \"\$f\")
        readelf -h \"\$f\" | grep -q 'X86-64' && echo \"ok    \$n: x86_64 ELF (\$(stat -c %s \"\$f\") bytes)\" || { echo \"FAIL \$n not x86_64\"; fail=1; }
        ex=\$(nm -D --defined-only \"\$f\" | awk '{print \$3}' | sort | tr '\n' ' ')
        need=\$(readelf -d \"\$f\" | sed -n 's/.*Shared library: \[\(.*\)\]/\1/p' | sort | tr '\n' ' ')
        glibc=\$(objdump -T \"\$f\" | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1)
        echo \"      exports: \$ex\"; echo \"      needs: \$need (newest \$glibc)\"
        for d in \$need; do case \$d in libc.so.6|libm.so.6|libpthread.so.0|libdl.so.2|librt.so.1|ld-linux-x86-64.so.2|libX11.so.6) ;;
            *) echo \"FAIL \$n needs \$d\"; fail=1 ;; esac; done
        for s in \$ex; do case \$s in clap_entry|GetPluginFactory|ModuleEntry|ModuleExit) ;;
            *) echo \"FAIL \$n exports \$s\"; fail=1 ;; esac; done
        case \"\$ex\" in *clap_entry*) ;; *) echo \"FAIL \$n: no clap_entry\"; fail=1 ;; esac
        case \"\$n\" in *.so) case \"\$ex\" in *GetPluginFactory*ModuleEntry*ModuleExit*) ;; *) echo \"FAIL \$n: VST3 entry points missing\"; fail=1 ;; esac ;; esac
        printf '%s\n%s\n' \"\$glibc\" GLIBC_2.31 | sort -CV || { echo \"FAIL \$n needs \$glibc (> 2.31)\"; fail=1; }
        readelf -lW \"\$f\" | grep GNU_STACK | grep -q RWE && { echo \"FAIL \$n: executable stack\"; fail=1; } || true
    done
    [ \$fail = 0 ] && echo 'ok    check: exports, libraries, glibc 2.31, stack'; exit \$fail"
}

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
        w.write(f"{n} {want16.get(n, base.get(n))}\n")
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
    all) build; check; test_linux ;;
    *) sed -n '3,16p' "$0"; exit 2 ;;
esac
