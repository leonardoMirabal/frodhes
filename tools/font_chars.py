#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The characters the firmware can draw, per font (the font diet, 2026-10-24: the image keeps only those glyphs).

  font_chars.py                 print the sets (AA small face, AA value face, the bitmap FONT_S)
  font_chars.py --why C         every literal that puts character C in a set (file:line)

The sets come from the sources, not from a run, so a text that a test never shows still counts:

  AA (aatext.c AF_S, every small text; the value faces take AA_L below): every character of every string literal and
     character constant in the app's sources (firmware/src minus the platform files, PLATFORM), except the literals
     that never reach the screen (NOT_DRAWN: printf / sscanf / strcmp / getenv / fopen / host_fl_check arguments,
     _Static_assert, #error, section names, asm, a #define of a file name that an #include takes: drive.c DRV_FILE_*) and the code built for the host only (#if DINGHY_HOST blocks without
     DINGHY_WEB: the test hooks; the web emulator's code is kept). printf-style conversions are not characters.
     Plus what is formatted at run time (RUNTIME: the digits; '.' for the ellipsis aatext.c draws as '.'; '?' the
     missing-glyph mark). User text: none can be typed; a user preset's name is written by presets.c preset_autoname
     from N_TYPE / N_TW and literals (upper case, digits, space) and was in every release (0.8 on), so it is in the set.
  AA_L (the value face: gen_aa_font.py L_CHARS, sparse; aatext.c aa_covers sends any other text to AF_S): L_CHARS
     restricted to AA, lower case folded to capitals (aatext.c aa_fold).
  FONT_S (gfx.c's 16 px bitmap font: the update, crash, UBOOT, USB rescue screens and the SMK's panel check): every
     literal of FONT_S_SOURCES (same exclusions), the app's name, plus the crash screen's hex and smk_int's sign
     (FONT_S_RUNTIME) and '?' (gfx.c glyph(): anything missing draws as '?').

tests/py/test_font_glyphs.py checks that every generated face has these glyphs and renders every reachable text on
the host with a missing-glyph counter (aatext.c AA_GLYPH_CHECK).
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "firmware" / "src"
# Dinghy / Felucca's platform: they draw only with FONT_S (gfx.c draw_text_box / cv_text)
PLATFORM = {"platform.c", "libc.c", "lcd.c", "gfx.c", "usb.c", "midi_uart.c", "ota.c", "recovery.c", "store.h",
            "bootguard.h", "smk_wheel.h", "uac_fir.h"}
FONT_S_SOURCES = ["platform.c", "recovery.c", "frodhes_smk.h"]
RUNTIME = set("0123456789.?")
FONT_S_RUNTIME = set("0123456789ABCDEF-?")
NOT_DRAWN = re.compile(r"\b(?:f?printf|snprintf|sscanf|strcmp|strncmp|getenv|fopen|host_fl_check|_Static_assert|"
                       r"__attribute__|\w+_OWN_SECTION|\w+_SEC|__asm__)\b|"   # (\w+_SEC: a section macro, RH_SP_SEC)
                       r"^\s*#\s*(?:error|pragma|include|warning)\b|"
                       r"^\s*#\s*define\s+(?:FELUCCA_ID|OTA_IDENTITY)\b|"   # (the USB identity, never drawn)
                       r'^\s*#\s*define\s+\w+\s+"[\w./-]+\.[ch]"\s*$')       # (a file name for an #include)
# a character constant compared with (a range test, a parse) is not drawn
CMP_CHAR = re.compile(r"(?:[<>]=?|[=!]=)\s*'(?:\\.|[^'\\\n])+'|'(?:\\.|[^'\\\n])+'\s*(?:[<>]=?|[=!]=)")
CONV = re.compile(r"%[-+ 0#]*\d*(?:\.\d+)?(?:hh|h|ll|l|z|j|t)?[diouxXcsp%]")
LIT = re.compile(r'"((?:\\.|[^"\\\n])*)"|\'((?:\\.|[^\'\\\n])+)\'')


def unescape(s):
    return bytes(s, "latin-1").decode("unicode_escape")


def strip_comments(t):
    t = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), t, flags=re.S)
    return re.sub(r"//[^\n]*", "", t)


def host_only(cond, directive):
    """an #if / #ifdef condition that holds on the host build and not in the firmware or the web emulator"""
    if directive == "ifdef":
        return cond.strip() == "DINGHY_HOST"
    if directive != "if":
        return False
    return (re.search(r"(?<!!)defined\s*\(?\s*DINGHY_HOST\b", cond) is not None
            and re.search(r"!\s*defined\s*\(?\s*DINGHY_WEB\b", cond) is not None)


def blank_static_asserts(t):
    """_Static_assert(...) blanked (they span lines; their message is the compiler's)"""
    out, i = [], 0
    for m in re.finditer(r"\b_Static_assert\s*\(", t):
        if m.start() < i:
            continue
        j, depth = m.end(), 1
        while depth and j < len(t):
            if t[j] == '"':
                j = re.compile(r'"(?:\\.|[^"\\])*"').match(t, j).end()
                continue
            depth += {"(": 1, ")": -1}.get(t[j], 0)
            j += 1
        out.append(t[i:m.start()] + re.sub(r"[^\n]", " ", t[m.start():j]))
        i = j
    return "".join(out) + t[i:]


def literals(path):
    """(line, text, char constant?) of every literal that may reach the screen"""
    out, stack = [], []          # one [host-only now, the #if's own test host-only] per open #if
    lines = blank_static_asserts(strip_comments(path.read_text())).splitlines()
    for no, ln in enumerate(lines, 1):
        m = re.match(r"\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)", ln)
        if m:
            d, cond = m.group(1), m.group(2)
            if d in ("ifdef", "ifndef", "if"):
                h = host_only(cond, d)
                stack.append([h, h])
            elif d == "elif" and stack:
                stack[-1][0] = False
            elif d == "else" and stack:
                stack[-1][0] = False
            elif d == "endif" and stack:
                stack.pop()
            continue
        if any(s[0] for s in stack) or NOT_DRAWN.search(ln):
            continue
        ln = CMP_CHAR.sub(" ", ln)
        for lm in LIT.finditer(ln):
            if lm.group(1) is not None:
                out.append((no, CONV.sub("", unescape(lm.group(1))), False))
            else:
                out.append((no, unescape(lm.group(2)), True))
    return out


def scan(files):
    chars, why = set(), {}
    for f in files:
        for no, s, _ in literals(f):
            for c in s:
                if 32 <= ord(c) < 256:
                    chars.add(c)
                    why.setdefault(c, []).append(f"{f.name}:{no} {s!r}")
    return chars, why


def app_files():
    return sorted(p for p in SRC.iterdir() if p.suffix in (".c", ".h") and p.name not in PLATFORM)


def aa_chars():
    c, why = scan(app_files())
    return c | RUNTIME, why


def aa_l_chars(l_chars):
    aa = aa_chars()[0]
    fold = {ch.upper() if "a" <= ch <= "z" else ch for ch in aa}
    return "".join(ch for ch in l_chars if ch in fold)


def font_s_chars():
    c, why = scan([SRC / f for f in FONT_S_SOURCES])
    m = re.search(r'^#define APP_NAME "([^"]*)"', (SRC / "frodhes.c").read_text(), re.M)
    c |= set(m.group(1)) | FONT_S_RUNTIME
    return c, why


def fmt(s):
    return "".join(sorted(s, key=ord))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--why", help="the literals that bring this character in")
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT / "tools"))
    from gen_aa_font import L_CHARS
    aa, why = aa_chars()
    fs, fwhy = font_s_chars()
    if a.why:
        for w in why.get(a.why, []):
            print("AA     ", w)
        for w in fwhy.get(a.why, []):
            print("FONT_S ", w)
        return
    print(f"AA     {len(aa):3d}  {fmt(aa)!r}")
    print(f"AA_L   {len(aa_l_chars(L_CHARS)):3d}  {aa_l_chars(L_CHARS)!r}")
    print(f"FONT_S {len(fs):3d}  {fmt(fs)!r}")


if __name__ == "__main__":
    main()
