#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Closure guard of the plugin's own-code build (FR_OWN=1): the plugin compiles only Frodhes' own code plus the
permissive libraries (plugin/deps.lock) and the window's OFL fonts.

    tools/own_check.py [-v]        exit 0 when the three checks pass (pending items are listed, not failures)

 (a) every file of the plugin's closure (tools/target_closure.py, target "plugin") is listed in plugin/own_allow.txt,
     and every listed file is in the closure (a stale line fails too);
 (b) no closure file carries a copyright or SPDX author line naming anyone but Leo (the library files under the
     permissive deps and the OFL fonts are exempt by path);
 (c) the preprocessed slot source (cc -E with the slot switches of plugin/cmake/slots.cmake) contains none of the
     third-party fingerprints of plugin/own_fingerprints.txt: normalized-line hashes (comments and white space
     dropped), made by tools/gen_own_fingerprints.py; the file holds hashes only, never text. A hit that was
     reviewed is listed in plugin/own_known.txt ("status path hash", made by --write-known): accepted = the line
     states the app's own interface (an enum value, a signature) in the plugin's own HAL files, todo = a leftover
     to rewrite. Any other hit fails; so does a listed line that no longer hits (delete it).

The DRIVE kernel (group fdrv) is being replaced in this build (part 3 of the plan); until then its fingerprints, and
the copyright lines of PENDING_COPYRIGHT's files, are reported as pending. PENDING_PART3 = () and an empty
PENDING_COPYRIGHT make them failures: that is the whole change when part 3 lands."""
import argparse
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import target_closure as tcl  # noqa: E402

ALLOW = "plugin/own_allow.txt"
FINGERPRINTS = "plugin/own_fingerprints.txt"
KNOWN = "plugin/own_known.txt"

# fingerprint groups that part 3 (the new DRIVE, replacing the FDRV kernel fx.c:632-911 in this build) removes:
# reported as pending, not as failures. Remove the list's entry (PENDING_PART3 = ()) when part 3 is merged.
PENDING_PART3 = ("fdrv",)
# closure files whose copyright lines still name another author: path -> why. Reported as pending.
PENDING_COPYRIGHT = {
    "firmware/src/fx.c": "header names Felucca's ports (the FDRV kernel, part 3); the FM-1 builds still need it",
    "firmware/src/fx_own.h": "line 2 repeats the Felucca copyright line: a stray line, to delete",
}
# paths of other people's permissive code that the plugin links: exempt from (b)
LIBRARY_PREFIXES = (".local/plugin-deps/", "assets/fonts/")
# numbers fixed by mathematics (a generated table of 2^(k/48) - 1): equal lines are not copying
MATH_FIXED = {"firmware/src/bend_own.h": "Q30 2^(k/48) - 1, made by tools/gen_bend_own.py from its definition"}

MIN_LEN = 20
COPYRIGHT_RE = re.compile(r"copyright|\(c\)\s*\d{4}|©|SPDX-FileCopyrightText|SPDX-FileContributor|@author|"
                          r"^\W*authors?\s*:", re.I)
LEO_RE = re.compile(r"Leo Mirabal|leonardoMirabal|Frodhes", re.I)
BINARY_EXT = (".ttf", ".otf", ".png")


def strip_comments(text):
    """text without C comments, the same number of lines (a comment keeps its newlines); strings and chars kept"""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" " + "\n" * text.count("\n", i, j))
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def norm(line):
    return re.sub(r"\s+", "", line)


def significant(n):
    """a normalized line worth a fingerprint: not short, not a directive of the preprocessor's control flow, not
    only punctuation or a few repeated characters"""
    if len(n) < MIN_LEN or len(set(n)) < 8:
        return False
    if n.startswith(("#include", "#if", "#el", "#endif", "#pragma", "#undef", "#line", "#error")):
        return False
    if re.search(r"[A-Za-z_]\w{2,}", n):
        return True
    return len(re.findall(r"\d+", n)) >= 3             # a long row of numbers (a table)


def fp(n):
    return hashlib.sha256(n.encode()).hexdigest()[:16]


def load_list(path):
    p = os.path.join(ROOT, path)
    out = []
    for l in open(p).read().splitlines() if os.path.exists(p) else []:
        l = l.split("#", 1)[0].strip()
        if l:
            out.append(l)
    return out


def load_fingerprints():
    """hash -> group"""
    d = {}
    for l in load_list(FINGERPRINTS):
        g, h = l.split()
        d[h] = g
    return d


def check_allow(files):
    allow = set(load_list(ALLOW))
    return sorted(files - allow), sorted(allow - files)


def check_copyright(files):
    """[(path, line number, text)] of copyright / author lines that do not name Leo"""
    bad = []
    for f in sorted(files):
        if f.endswith(BINARY_EXT) or f.startswith(LIBRARY_PREFIXES):
            continue
        for i, l in enumerate(open(os.path.join(ROOT, f), errors="replace").read().splitlines(), 1):
            if COPYRIGHT_RE.search(l) and not LEO_RE.search(l):
                bad.append((f, i, l.strip()))
    return bad


def preprocess():
    """the slot source after the preprocessor: [(path relative to ROOT, line number, line)] for the files of the
    repository (the system headers are dropped)"""
    cc = os.environ.get("CC", "cc")
    flags = ["-DFRP_SLOT=0"] + tcl.plugin_defs() + ["-Iplugin/compat", "-Ifirmware/src", "-Ifirmware/gen"]
    r = subprocess.run([cc, "-E", *flags, "plugin/src/engine_unit.c"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("own_check: %s -E failed:\n%s" % (cc, r.stderr[-2000:]))
    out, cur, ln = [], None, 0
    for l in r.stdout.splitlines():
        m = re.match(r'# (\d+) "([^"]*)"', l)
        if m:
            ln = int(m.group(1))
            p = os.path.normpath(m.group(2) if os.path.isabs(m.group(2)) else os.path.join(ROOT, m.group(2)))
            cur = os.path.relpath(p, ROOT).replace(os.sep, "/") if p.startswith(ROOT + os.sep) else None
            continue
        if cur is not None:
            out.append((cur, ln, l))
        ln += 1
    return out


def check_fingerprints(lines, fps):
    """[(group, path, line number, hash)] of the preprocessed lines that carry a fingerprint"""
    hits = []
    for f, ln, l in lines:
        n = norm(l)
        if n and fp(n) in fps and significant(n):
            hits.append((fps[fp(n)], f, ln, fp(n)))
    return hits


def load_known():
    """(path, hash) -> status"""
    d = {}
    for l in load_list(KNOWN):
        st, path, h = l.split()[:3]
        d[(path, h)] = st
    return d


def classify(path):
    """the status a new known line gets: the plugin's own HAL files state the app's interface, the rest is a leftover"""
    return "accepted" if re.match(r"plugin/src/own_\w+\.(c|h)$", path) else "todo"


def write_known(hits):
    rows = sorted({(classify(f), f, h) for g, f, ln, h in hits})
    open(os.path.join(ROOT, KNOWN), "w").write(
        "# SPDX-License-Identifier: GPL-3.0-only\n"
        "# Reviewed fingerprint hits (tools/own_check.py (c)): status path hash. accepted: the line states the app's\n"
        "# interface in the plugin's own HAL files (enum values, signatures); todo: a leftover to rewrite. Delete a line\n"
        "# when its source line is gone (the check fails on a line that no longer hits).\n"
        + "".join("%s %s %s\n" % r for r in rows))


def run(verbose=False, write=False, out=sys.stdout):
    closure = tcl.plugin_closure()
    fps = load_fingerprints()
    failed = False

    def say(s):
        print(s, file=out)

    say("own_check: closure %d files, %d fingerprints" % (len(closure), len(fps)))
    unlisted, stale = check_allow(closure)
    say("(a) allow-list: %s" % ("ok" if not (unlisted or stale) else "FAIL"))
    for f in unlisted:
        say("    in the closure, not in %s: %s" % (ALLOW, f))
    for f in stale:
        say("    in %s, not in the closure: %s" % (ALLOW, f))
    failed |= bool(unlisted or stale)

    bad = check_copyright(closure)
    hard = [b for b in bad if b[0] not in PENDING_COPYRIGHT]
    pend = [b for b in bad if b[0] in PENDING_COPYRIGHT]
    say("(b) copyright lines: %s, %d pending" % ("ok" if not hard else "FAIL", len(pend)))
    for f, i, t in hard:
        say("    %s:%d: %s" % (f, i, t[:100]))
    if verbose:
        for f, i, t in pend:
            say("    pending %s:%d (%s)" % (f, i, PENDING_COPYRIGHT[f]))
    failed |= bool(hard)

    hits = check_fingerprints(preprocess(), fps)
    hits = [h for h in hits if h[1] not in MATH_FIXED]
    if write:
        write_known([h for h in hits if h[0] not in PENDING_PART3])
    known = load_known()
    pend = [h for h in hits if h[0] in PENDING_PART3]
    rest = [h for h in hits if h[0] not in PENDING_PART3]
    hard = [h for h in rest if (h[1], h[3]) not in known]
    seen = {(h[1], h[3]) for h in rest}
    stale = sorted(k for k in known if k not in seen)
    acc = [h for h in rest if known.get((h[1], h[3])) == "accepted"]
    todo = [h for h in rest if known.get((h[1], h[3])) == "todo"]
    say("(c) fingerprints in the preprocessed slot source: %s; %d pending (%s), %d known leftovers (todo), "
        "%d accepted (interface)" % ("ok" if not (hard or stale) else "FAIL", len(pend),
                                     ", ".join(PENDING_PART3) or "none", len(todo), len(acc)))
    for g, f, i, h in hard:
        say("    %s: %s:%d" % (g, f, i))
    for path, h in stale:
        say("    %s lists %s %s, which no longer hits: delete the line" % (KNOWN, path, h))
    if verbose:
        for tag, lst in (("pending", pend), ("todo", todo), ("accepted", acc)):
            for g, f, i, h in lst:
                say("    %s %s: %s:%d" % (tag, g, f, i))
    failed |= bool(hard or stale)
    say("own_check: %s" % ("FAIL" if failed else "ok"))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-v", "--verbose", action="store_true", help="list the pending items")
    ap.add_argument("--write-known", action="store_true", help="rewrite %s from the current hits (review it)" % KNOWN)
    a = ap.parse_args()
    return run(a.verbose, a.write_known)


if __name__ == "__main__":
    sys.exit(main())
