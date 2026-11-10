#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The VST3 SDK's own conformance test (its "validator" hosting sample), built from the pinned SDK checkout with the
# command line tools only (the SDK's CMake wants Xcode on macOS), then run on a Frodhes.vst3:
#   tools/plugin_vst3_validator.sh [Frodhes.vst3]     (default build/plugin/bundles/Frodhes.vst3)
# The binary lands in build/vst3-validator/validator (built once). macOS only. Exit: the validator's (0 = pass).
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SDK="${FRP_DEPS_DIR:-$ROOT/.local/plugin-deps}/vst3sdk"
O="$ROOT/build/vst3-validator"
V="${1:-$ROOT/build/plugin/bundles/Frodhes.vst3}"
[ -d "$SDK/public.sdk" ] || { echo "no VST3 SDK in $SDK (make plugin-deps)" >&2; exit 2; }
if [ ! -x "$O/validator" ]; then
    mkdir -p "$O"
    # the validator's own sources (its CMakeLists), the hosting classes, base and the interfaces
    python3 - "$SDK" > "$O/sources.txt" <<'EOF'
import glob, os, re, sys
sdk = sys.argv[1]
s = open(os.path.join(sdk, "public.sdk/samples/vst-hosting/validator/CMakeLists.txt")).read()
fs = [f.replace("${SDK_ROOT}/", "") for f in re.findall(r"(\S+\.cpp)", s)]
fs = ["public.sdk/samples/vst-hosting/validator/" + f if f.startswith("source/") else f for f in fs]
os.chdir(sdk)
for g in ["public.sdk/source/vst/hosting/*.cpp", "base/source/*.cpp", "base/thread/source/*.cpp", "pluginterfaces/base/*.cpp",
          "public.sdk/source/vst/utility/stringconvert.cpp", "public.sdk/source/common/commonstringconvert.cpp",
          "public.sdk/source/vst/vstinitiids.cpp", "public.sdk/source/vst/vstpresetfile.cpp"]:
    fs += glob.glob(g)
print("\n".join(sorted(f for f in set(fs) if not re.search(r"module_(linux|win32)", f))))
EOF
    (cd "$SDK" && nice -n 15 clang++ -std=c++17 -O1 -w -DRELEASE=1 -I. $(cat "$O/sources.txt") \
        -x objective-c++ -fobjc-arc public.sdk/source/vst/hosting/module_mac.mm public.sdk/source/common/threadchecker_mac.mm \
        -framework Cocoa -o "$O/validator")
    "$O/validator" -selftest > "$O/selftest.log" 2>&1 || { echo "the validator's selftest failed ($O/selftest.log)" >&2; exit 1; }
fi
"$O/validator" "$V" > "$O/report.txt" 2>&1 && rc=0 || rc=$?
grep -E "^Result|ERROR" "$O/report.txt" || true
echo "(full report: $O/report.txt)"
exit $rc
