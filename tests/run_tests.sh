#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Frodhes' SMK-37 host tests (no hardware). Run from the repo root after building, the SMK-37 first, the FM-1 last
# (both boards write the same build/ intermediates; the tests expect the FM-1 binaries):
#   BOARD=smk37 sh build.sh; BOARD=smk37 MODEL=elite sh build.sh; BOARD=smk37 MODEL=mke sh build.sh; sh build.sh
#   sh tests/run_tests.sh
# The app on the host (both boards; tests/smk/*.txt: the SMK-37 mapping), the SMK-37 flash map, the update path
# (ota.c, the loader, tools/fm1_install.py; after the SLOOP SMK-37 target) and the packages. make test and
# make pytest stay the FM-1's own tests.
set -e
export AC79_SDK="${AC79_SDK:-$HOME/fw-AC79_AIoT_SDK}"   # (fm1pkg_make.py: the SDK files of a package)
cd "$(dirname "$0")/.."
OUT=build/host
mkdir -p "$OUT"
CC="${CC:-cc} -O1 -Wall -Wno-unused-function"
fail=0
run() { echo "== $1"; shift; "$@" || fail=1; }

[ -f build/frodhes.fwsc ] || { echo "run ./build.sh first"; exit 1; }

run "FM-1 app on the host: the default script plays, screens and sound" sh tests/run_host.sh
run "SMK-37 app on the host: the default script" env BOARD=smk37 sh tests/run_host.sh
cc -O2 -Wall -Wno-unused-function -DDINGHY_HOST -DBOARD_SMK37=1 -DAPP_VER='"dev"' -Ifirmware/src -Ifirmware/gen \
   -o "$OUT/frodhes-smk37" firmware/src/frodhes.c -lm
for s in tests/smk/*.txt; do
    n=$(basename "$s" .txt)
    run "SMK-37 $n ($s)" sh -c "'$OUT/frodhes-smk37' '$OUT/smk-$n' '$s' > '$OUT/smk-$n.log' 2>&1 || { grep FAIL '$OUT/smk-$n.log'; exit 1; }"
done

$CC -DBOARD_SMK37=1 -Ifirmware/hal -Ifirmware/src -o "$OUT/smk_flash_test" tests/smk_flash_test.c
run "SMK-37 flash map: settings, presets, pattern and owner tag in the store; never the head, BTIF/USRTRIM, USRFLASH, USR, key_mac" "$OUT/smk_flash_test"
$CC -Ifirmware/src -o "$OUT/smk_wheel_test" tests/smk_wheel_test.c
run "SMK-37 pitch wheel: the bend is back to 0 at rest, however slowly the wheel was moved" "$OUT/smk_wheel_test"
$CC -w -Ifirmware/hal -o "$OUT/smk_key_test" tests/smk_key_test.c
run "SMK-37 keys: velocity from the two contacts, a slow press plays after SMK_KEY_TMAX_MS, once" "$OUT/smk_key_test"

$CC -o "$OUT/ota_test" tests/ota_test.c
run "M-UPGRADE entry (FM-1)" "$OUT/ota_test" build/frodhes.fwsc
head -c 60000 build/frodhes.bin > "$OUT/old_app.bin"
python3 tools/fm1pkg_make.py "$OUT/old_app.bin" build/loader/ota.bin "$OUT/old.fwsc" >/dev/null
$CC -o "$OUT/ldr_test" tests/ldr_test.c
run "update loader (FM-1): other app -> this build" "$OUT/ldr_test" "$OUT/old.fwsc" build/frodhes.fwsc

# SMK-37: the OTA staging and the loader windows on SMK-layout packages (made here from the FM-1 binaries: only the
# layout and the protocol are under test), then the real SMK packages
python3 tools/fm1pkg_make.py --board smk37 build/frodhes.bin build/loader/ota.bin "$OUT/smk_new.fwsc" >/dev/null
python3 tools/fm1pkg_make.py --board smk37 "$OUT/old_app.bin" build/loader/ota.bin "$OUT/smk_old.fwsc" >/dev/null
run "SMK-37 package layout (fm1pkg_make --board smk37): PRCT 0x9D000, stock region table" \
    python3 tests/smk_pkg_test.py "$OUT/smk_new.fwsc" "SMK-37 Pro_900"
$CC -DBOARD_SMK37=1 -o "$OUT/ota_test_smk" tests/ota_test.c
run "M-UPGRADE entry, SMK-37 Pro (staging 0xBB000, parm ota-SMK-37 Pro_016)" "$OUT/ota_test_smk" "$OUT/smk_new.fwsc"
for m in 1 2; do
    $CC -DBOARD_SMK37=1 -DT_MODEL=$m -o "$OUT/ota_test_smk_m$m" tests/ota_test.c
    run "M-UPGRADE entry, SMK-37 model $m (1 Elite, 2 MKE-P37): identity and parm under its name" "$OUT/ota_test_smk_m$m" "$OUT/smk_new.fwsc"
done
$CC -DBOARD_SMK37=1 -o "$OUT/ldr_test_smk" tests/ldr_test.c
run "update loader, SMK-37: app area 0x4000-0x9CFFF, protected ranges untouched" "$OUT/ldr_test_smk" "$OUT/smk_old.fwsc" "$OUT/smk_new.fwsc"
found=0
for m in ":SMK-37 Pro_470" "-elite:SMK-37 Elite_470" "-mke:MKE-P37_470"; do
    for f in build/frodhes*-smk37${m%%:*}.fwsc; do
        [ -f "$f" ] || continue
        found=1
        run "SMK-37 package $f: layout, identity ${m#*:}, the loader marker" python3 tests/smk_pkg_test.py "$f" "${m#*:}"
    done
done
[ "$found" = 1 ] || echo "(no SMK-37 packages in build/: BOARD=smk37 sh build.sh first)"

run "installer (tools/fm1_install.py): FM-1 and SMK-37, install and the update-mode resume check the model" python3 tests/install_test.py

[ "$fail" = 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit "$fail"
