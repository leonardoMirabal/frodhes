#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Builds the delay equivalence harness (tests/c/dly_equiv.c, see its header):
#   tests/c/dly_equiv_build.sh OUTDIR          -> OUTDIR/dly_equiv
#   tests/c/dly_equiv_build.sh --old-obj FILE  -> only the old side as an object (the sparse tree ships it)
# Env: CC (default cc), DLY_OWN_FILE (path of the file to use as dly_own.c, default firmware/src/dly_own.c).
# With tests/c/dly_old.o present (the sparse tree: fx.c includes dly_own.c itself and holds no old loops), the old side
# is that object and the new side is fx.c as it is; otherwise both sides are built from fx.c (the old loops) and
# dly_own.c is spliced in after it.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
CC=${CC:-cc}
INC="-I$ROOT/firmware/src -I$ROOT/firmware/gen -I$HERE"
CF="-O2 -Wall -Wno-unused-function -Wno-unused-variable $INC"
if [ "$1" = "--old-obj" ]; then
    $CC $CF -DSIDE_OLD -c "$HERE/dly_side.c" -o "$2"
    exit 0
fi
OUT=${1:?usage: dly_equiv_build.sh OUTDIR}
mkdir -p "$OUT"
OWN=
[ -n "$DLY_OWN_FILE" ] && OWN="-DDLY_OWN_FILE=\"$DLY_OWN_FILE\""
if [ -f "$HERE/dly_old.o" ]; then
    OLDOBJ="$HERE/dly_old.o"; NEWDEF=-DDLY_FROM_FX
else
    OLDOBJ="$OUT/dly_old.o"; NEWDEF=
    $CC $CF -DSIDE_OLD -c "$HERE/dly_side.c" -o "$OLDOBJ"
fi
$CC $CF -DSIDE_NEW $NEWDEF $OWN -c "$HERE/dly_side.c" -o "$OUT/dly_new.o"
$CC $CF -c "$HERE/dly_equiv.c" -o "$OUT/dly_equiv.o"
$CC -o "$OUT/dly_equiv" "$OUT/dly_equiv.o" "$OLDOBJ" "$OUT/dly_new.o"
