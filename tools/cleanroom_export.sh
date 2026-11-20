#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The plugin's own-code build, the delay core: makes a source tree for the implementer of firmware/src/dly_own.c,
# a tree that holds no trace of the old delay loops.
#
#   tools/cleanroom_export.sh REF OUTDIR
#
# - git archive REF into OUTDIR (no .git, no history); OUTDIR must not exist or be empty.
# - fx.c: the two loops (dly_loop with its comment, and dly_loop_kb with its comment; the statics between them stay)
#   are replaced by an #include of dly_own.c; a skeleton firmware/src/dly_own.c (signatures only) is written.
# - private/ is deleted. Every other file that carries the loops' text (a line-for-line copy of any distinctive line:
#   tools/cleanroom_scan.py's rule, inline below) is removed; the one that must stay to build, firmware/src/dly_tape.c,
#   keeps its signatures and loses the bodies of the functions that carry the text.
# - the harness files (tests/c/dly_equiv*, dly_side.c, dly_state.h, tests/py/test_dly_equiv.py) are copied in from this
#   checkout, so REF may predate them.
# - the old loops come as tests/c/dly_old.o (built from REF's own tree), so the harness can still run them.
# - EXPORT.sha256: the manifest of every file in the export.
set -e
REF=${1:?usage: cleanroom_export.sh REF OUTDIR}
OUT=${2:?usage: cleanroom_export.sh REF OUTDIR}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$OUT"
[ -z "$(ls -A "$OUT")" ] || { echo "cleanroom_export: $OUT is not empty" >&2; exit 1; }
OUT=$(cd "$OUT" && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/cleanroom.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
cd "$ROOT"
mkdir "$TMP/full"
git archive "$REF" | tar -x -C "$TMP/full"
git archive "$REF" | tar -x -C "$OUT"
# the harness comes from this checkout (REF may predate it): the same files in both trees
for f in tests/c/dly_equiv.c tests/c/dly_side.c tests/c/dly_state.h tests/c/dly_equiv_build.sh tests/py/test_dly_equiv.py; do
    for t in "$TMP/full" "$OUT"; do mkdir -p "$t/$(dirname "$f")"; cp "$ROOT/$f" "$t/$f"; done
done
# the old side's object, from the full tree
CC=${CC:-cc} "$TMP/full/tests/c/dly_equiv_build.sh" --old-obj "$OUT/tests/c/dly_old.o"
rm -rf "$OUT/private"
python3 -I - "$OUT" <<'PY'
import os, re, sys
out = sys.argv[1]
fx = os.path.join(out, "firmware/src/fx.c")
src = open(fx).read().split("\n")

def find(pred, start=0):
    for i in range(start, len(src)):
        if pred(src[i]):
            return i
    raise SystemExit("cleanroom_export: anchor not found")

def end_of_fn(i):                                  # the first line that is exactly "}" at or after i
    while src[i] != "}":
        i += 1
    return i

a0 = find(lambda l: l.startswith("/* the delay's sample loop, one version"))
f0 = find(lambda l: "void dly_loop(int32_t *L" in l, a0)
a1 = end_of_fn(f0)
b0 = find(lambda l: l.startswith("/* ANA on the bars (0.9.13): dly_loop with"), a1)
g0 = find(lambda l: "static void dly_loop_kb(" in l, b0)
b1 = end_of_fn(g0)
assert a0 < f0 < a1 < b0 < g0 < b1 and b1 - g0 < 12 and a1 - a0 < 120, (a0, a1, b0, b1)
seg = src[a0:a1 + 1] + src[b0:b1 + 1]
print("fx.c old loops: lines %d-%d (dly_loop and its comment), %d-%d (dly_loop_kb and its comment); the statics "
      "between them stay" % (a0 + 1, a1 + 1, b0 + 1, b1 + 1))
# distinctive lines of the old loops: code lines (trailing comment cut) of 22+ characters that name the delay's
# state / helpers, or are 50+ characters
TOK = ("dly_", "DLY_", "kcol", "kfb", "fq", "wq", "osc_sine", "69000", "softclip", "fx_sck_k", "FX_HC", "lb")
pats = set()
for l in seg:
    t = l.strip()
    if t.startswith("/*") or t.startswith("*"):
        continue
    t = re.sub(r"\s*/\*.*$", "", t).strip()
    if len(t) >= 22 and (len(t) >= 50 or any(k in t for k in TOK)):
        pats.add(t)
new = src[:a0] + ['/* the delay\'s two sample loops, the main loop (dly_loop) and the bars wrapper (dly_loop_kb): written from',
                  ' * the specification, firmware/src/dly_own.c */', '#include "dly_own.c"'] + src[a1 + 1:b0] + \
      ['/* (dly_loop_kb: in dly_own.c) */'] + src[b1 + 1:]
open(fx, "w").write("\n".join(new))
open(os.path.join(out, "firmware/src/dly_own.c"), "w").write(
"""/* SPDX-License-Identifier: GPL-3.0-only */
/* The delay's two sample loops, written from the specification (the plugin's own-code build). Included by fx.c in
 * the place the old loops had; tests/c/dly_equiv.c says what must be exposed:
 *   static inline __attribute__((always_inline)) void dly_loop(...)   the main loop
 *   static void dly_loop_kb(...)                                      the bars wrapper (noinline) */
static inline __attribute__((always_inline)) void dly_loop(int32_t *L, int32_t *R, uint32_t n, const int tape,
                                                           const int32_t msk, int32_t sg16, int32_t target, int32_t col,
                                                           int32_t fb, int32_t wet, int32_t dgt, const int rst,
                                                           const int kb)
{
    (void)L; (void)R; (void)n; (void)tape; (void)msk; (void)sg16; (void)target; (void)col; (void)fb; (void)wet;
    (void)dgt; (void)rst; (void)kb;
}
__attribute__((noinline)) static void dly_loop_kb(int32_t *L, int32_t *R, uint32_t n, int32_t sg16, int32_t target,
                                                  int32_t col, int32_t fb, int32_t wet, int32_t dgt, int rst)
{
    (void)L; (void)R; (void)n; (void)sg16; (void)target; (void)col; (void)fb; (void)wet; (void)dgt; (void)rst;
}
""")

def carriers():
    res = {}
    for d, _, fs in os.walk(out):
        for f in fs:
            p = os.path.join(d, f)
            if f.endswith(".o") or os.path.islink(p) or p == os.path.join(out, "firmware/src/dly_own.c"):
                continue
            try:
                s = open(p, errors="ignore").read()
            except OSError:
                continue
            n = sum(1 for t in pats if t in s)
            if n >= 3:
                res[p] = n
    return res

def redact(p):
    """keep every top-level function's signature, drop the body of those that hold a distinctive line"""
    s = open(p).read()
    i, n, depth, start, last = 0, len(s), 0, 0, 0   # last: end of the previous top-level statement or block
    outp, cut = [], []
    while i < n:
        if s.startswith("/*", i):
            i = s.index("*/", i) + 2
            continue
        if s.startswith("//", i):
            i = s.index("\n", i)
            continue
        c = s[i]
        if c == '"' or c == "'":
            j = i + 1
            while s[j] != c:
                j += 2 if s[j] == "\\" else 1
            i = j + 1
            continue
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                body = s[start:i + 1]
                if any(t in body for t in pats):
                    hdr = re.sub(r"/\*.*?\*/", "", s[last:start], flags=re.S)
                    hdr = re.sub(r"__attribute__\(\(.*?\)\)", "", hdr).split("(")[0]
                    cut.append((start, i + 1, bool(re.search(r"\bvoid\b", hdr))))
                last = i + 1
        elif c == ";" and depth == 0:
            last = i + 1
        i += 1
    for st, en, isvoid in reversed(cut):
        s = s[:st] + "{\n    /* the old loops' text, removed from this tree */\n" + ("" if isvoid else "    return 0;\n") + "}" + s[en:]
    open(p, "w").write(s)
    return len(cut)

removed, redacted = [], []
for p, nlines in sorted(carriers().items()):
    rel = os.path.relpath(p, out)
    if rel == "firmware/src/dly_tape.c":
        redacted.append((rel, redact(p)))
    else:
        os.remove(p)
        removed.append(rel)
left = {os.path.relpath(p, out): k for p, k in carriers().items()}
print("removed files:", removed if removed else "none")
print("redacted:", ["%s (%d function bodies)" % r for r in redacted])
open(os.path.join(out, ".export_patterns_left"), "w").write(repr(left))
if left:
    raise SystemExit("cleanroom_export: carriers left: %r" % left)
os.remove(os.path.join(out, ".export_patterns_left"))
PY
( cd "$OUT" && find . -type f ! -name EXPORT.sha256 | LC_ALL=C sort | while read -r f; do shasum -a 256 "$f"; done > EXPORT.sha256 )
echo "manifest: $(wc -l < "$OUT/EXPORT.sha256") files, $OUT/EXPORT.sha256"
