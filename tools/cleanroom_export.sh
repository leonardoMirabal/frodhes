#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The plugin's own-code build, the delay core: makes a source tree for the implementer of firmware/src/dly_own.c,
# a tree that holds no trace of the old delay loops.
#
#   tools/cleanroom_export.sh REF OUTDIR
#
# - git archive REF into OUTDIR (no .git, no history); OUTDIR must not exist or be empty.
# - fx_fel.c (fx.c in a REF before the split): the two loops (dly_loop with its comment, and dly_loop_kb with its comment; the statics between them stay)
#   are replaced by an #include of dly_own.c; a skeleton firmware/src/dly_own.c (signatures only) is written.
# - the gitignored notes folder is deleted. Every other file that carries the loops' text (three or more distinctive lines of them, the
#   rule inline below) is removed. firmware/src/dly_tape.c must stay to build: the functions derived from the loops
#   (dly_rd, dly_fade, dly_wr_c, dly_loop_c / _cd / _ca, dly_fade_c, tp_loop_c, tp_tape_k / _kb, tp_tape, tp_loop) are cut
#   out, in four groups, and each group becomes an include of dly_own.c with DLY_OWN_PART 2 to 5 (the skeleton lists the signatures).
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
fx = os.path.join(out, "firmware/src/fx_fel.c")      # the old loops live in fx_fel.c (parts 1 and 2); a REF before the split: fx.c
if not os.path.exists(fx):
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
                  ' * the specification, firmware/src/dly_own.c */', '#define DLY_OWN_PART 1', '#include "dly_own.c"', '#undef DLY_OWN_PART'] + src[a1 + 1:b0] + \
      ['/* (dly_loop_kb: in dly_own.c) */'] + src[b1 + 1:]
open(fx, "w").write("\n".join(new))
open(os.path.join(out, "firmware/src/dly_own.c"), "w").write(
"""/* SPDX-License-Identifier: GPL-3.0-only */
/* The delay's own sample loops, written from the specification (the plugin's own-code build). One file, included
 * from five places, each with DLY_OWN_PART set to its number (tests/c/dly_equiv.c lists the exact signatures):
 *   1  fx.c, where the old main loops were:         dly_loop, dly_loop_kb
 *   2  dly_tape.c, where dly_rd and dly_fade were:  dly_rd, dly_fade
 *   3  dly_tape.c (FR_CHUNK), the chunk group:      dly_wr_c, dly_loop_c, dly_loop_cd, dly_loop_ca, dly_fade_c
 *   4  dly_tape.c (FR_CHUNK), where tp_loop_c was:  tp_loop_c
 *   5  dly_tape.c, where the tape pass and loop were: tp_tape_k, tp_tape_kb, tp_tape, tp_loop
 * Each part sees everything defined above its include site. fx.c includes part 1; dly_tape.c sets the macro, includes
 * the file and undefines it for parts 2 to 5. */
#if DLY_OWN_PART == 1
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
#elif DLY_OWN_PART == 2
static inline __attribute__((always_inline)) int32_t dly_rd(uint32_t w, int32_t d, uint32_t c)
{
    (void)w; (void)d; (void)c;
    return 0;
}
__attribute__((noinline)) static void dly_fade(int32_t *L, int32_t *R, uint32_t n, int tape, int32_t msk,
                                               int32_t sg16, int32_t target, int32_t col, int32_t fb, int32_t wet,
                                               int32_t dgt, int32_t gs)
{
    (void)L; (void)R; (void)n; (void)tape; (void)msk; (void)sg16; (void)target; (void)col; (void)fb; (void)wet;
    (void)dgt; (void)gs;
}
#elif DLY_OWN_PART == 3
__attribute__((noinline)) static void dly_wr_c(uint32_t w0, uint32_t n, int32_t sg16)
{
    (void)w0; (void)n; (void)sg16;
}
static inline __attribute__((always_inline)) void dly_loop_c(int32_t *L, int32_t *R, uint32_t n, const int tape,
                                                             int32_t target, int32_t col, int32_t fb, int32_t wet,
                                                             int32_t dgt)
{
    (void)L; (void)R; (void)n; (void)tape; (void)target; (void)col; (void)fb; (void)wet; (void)dgt;
}
__attribute__((noinline)) static void dly_loop_cd(int32_t *L, int32_t *R, uint32_t n, int32_t target, int32_t col,
                                                  int32_t fb, int32_t wet, int32_t dgt)
{
    dly_loop_c(L, R, n, 0, target, col, fb, wet, dgt);
}
__attribute__((noinline)) static void dly_loop_ca(int32_t *L, int32_t *R, uint32_t n, int32_t target, int32_t col,
                                                  int32_t fb, int32_t wet, int32_t dgt)
{
    dly_loop_c(L, R, n, 1, target, col, fb, wet, dgt);
}
__attribute__((noinline)) static void dly_fade_c(int32_t *L, int32_t *R, uint32_t n, int tape, int32_t msk,
                                                 int32_t target, int32_t col, int32_t fb, int32_t wet, int32_t dgt,
                                                 int32_t gs)
{
    (void)L; (void)R; (void)n; (void)tape; (void)msk; (void)target; (void)col; (void)fb; (void)wet; (void)dgt;
    (void)gs;
}
#elif DLY_OWN_PART == 4
FX_RAM __attribute__((noinline)) static void tp_loop_c(int32_t *L, int32_t *R, uint32_t j0, uint32_t j1, uint32_t p0,
                                                     uint32_t np, int32_t fb, int32_t wet, int32_t dgt, int32_t g0)
{
    (void)L; (void)R; (void)j0; (void)j1; (void)p0; (void)np; (void)fb; (void)wet; (void)dgt; (void)g0;
}
#elif DLY_OWN_PART == 5
static inline __attribute__((always_inline)) void tp_tape_k(int32_t *m, const int32_t *dt, uint32_t n, const int32_t msk,
                                                            int32_t fb, const int rst, const int kb)
{
    (void)m; (void)dt; (void)n; (void)msk; (void)fb; (void)rst; (void)kb;
}
__attribute__((noinline)) static void tp_tape_kb(int32_t *m, const int32_t *dt, uint32_t n, int32_t msk, int32_t fb)
{
    (void)m; (void)dt; (void)n; (void)msk; (void)fb;
}
FX_RAM __attribute__((noinline)) static void tp_tape(int32_t *m, const int32_t *dt, uint32_t n, const int32_t msk,
                                                     int32_t fb)
{
    (void)m; (void)dt; (void)n; (void)msk; (void)fb;
}
FX_RAM __attribute__((noinline)) static void tp_loop(int32_t *L, int32_t *R, int32_t *m, const int32_t *dt, uint32_t n,
                                                     const int32_t msk, int32_t sg16, int32_t fb, int32_t wet,
                                                     int32_t dgt, int32_t w0)
{
    (void)L; (void)R; (void)m; (void)dt; (void)n; (void)msk; (void)sg16; (void)fb; (void)wet; (void)dgt; (void)w0;
}
#endif
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

def top_blocks(s):
    """the top-level {} blocks of C text: (name, signature start, block end), the name from the header before '('"""
    i, n, depth, start, last, res = 0, len(s), 0, 0, 0, []
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
                hdr = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), s[last:start], flags=re.S)
                k = len(hdr) - len(hdr.lstrip())
                sig = s.rfind("\n", 0, last + k) + 1
                if sig < last:
                    sig = last
                h2 = re.sub(r"__attribute__\(\(.*?\)\)", "", hdr).split("(")[0].split()
                res.append((h2[-1].lstrip("*") if h2 else "", sig, i + 1))
                last = i + 1
        elif c == ";" and depth == 0:
            last = i + 1
        i += 1
    return res

# the derived functions of dly_tape.c, in the groups they are contiguous in: each group becomes one include of dly_own.c
GROUPS = [(2, ["dly_rd", "dly_fade"]), (3, ["dly_wr_c", "dly_loop_c", "dly_loop_cd", "dly_loop_ca", "dly_fade_c"]),
          (4, ["tp_loop_c"]), (5, ["tp_tape_k", "tp_tape_kb", "tp_tape", "tp_loop"])]
def stub_tape(p):
    s = open(p).read()
    blocks = {b[0]: b for b in top_blocks(s)}
    edits = []
    for part, names in GROUPS:
        bs = [blocks[nm] for nm in names]                  # KeyError: a name moved or renamed: stop
        assert all(bs[k][1] < bs[k + 1][1] for k in range(len(bs) - 1)), names
        edits.append((bs[0][1], bs[-1][2], part))
    for st, en, part in sorted(edits, reverse=True):
        s = s[:st] + "#define DLY_OWN_PART %d\n#include \"dly_own.c\"\n#undef DLY_OWN_PART" % part + s[en:]
    open(p, "w").write(s)
    return [nm for _, names in GROUPS for nm in names]

stubbed = stub_tape(os.path.join(out, "firmware/src/dly_tape.c"))
removed = []
for p, nlines in sorted(carriers().items()):
    rel = os.path.relpath(p, out)
    if rel != "firmware/src/dly_tape.c":
        os.remove(p)
        removed.append(rel)
left = {os.path.relpath(p, out): k for p, k in carriers().items()}
print("removed files:", removed if removed else "none")
print("dly_tape.c functions stubbed (bodies replaced by the dly_own.c includes):", stubbed)
print("distinctive loop lines still in dly_tape.c:", left.pop("firmware/src/dly_tape.c", 0))
if left:
    raise SystemExit("cleanroom_export: carriers left: %r" % left)
PY
( cd "$OUT" && find . -type f ! -name EXPORT.sha256 | LC_ALL=C sort | while read -r f; do shasum -a 256 "$f"; done > EXPORT.sha256 )
echo "manifest: $(wc -l < "$OUT/EXPORT.sha256") files, $OUT/EXPORT.sha256"
