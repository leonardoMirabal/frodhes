#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The links in README.md and docs/MANUAL.md that change by content, never by release.

    tools/doc_links.py            check (the default): exit 1 and list every problem
    tools/doc_links.py --fix      rewrite every picture's ?v= to its file's hash, then check

The version lives in one place, the GitHub release (its tag, page, notes and assets), so these docs carry no version
of their own:
  - every picture link (Markdown ![..](x.png?v=H) or <img src="x.png?v=H">) has ?v= the first 8 hex digits of the
    file's sha256: the link (GitHub's image cache key) changes exactly when the picture does. tools/manual_shots.py
    runs --fix after shooting; run it by hand after editing a picture (overview.png, any hand-made image);
  - no download, "Latest release" or "Version" line names a version: the package is linked as
    releases/latest/download/frodhes.fwsc (release.yml uploads it under that fixed name too). History ("settings
    saved by 0.9.3 are read as they are") is allowed.
tests/py/test_doc_links.py runs the check; tools/release.py's docs step too.
"""
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = ("README.md", "docs/MANUAL.md")
LATEST = "https://github.com/leonardoMirabal/frodhes/releases/latest"
DOWNLOAD = LATEST + "/download/frodhes.fwsc"
IMG = r"\.(?:png|jpe?g|gif|svg|webp)"
# a picture link: group 1 the path, 2 the ?v= value (or None)
PIC = re.compile(r"(?:!\[[^\]]*\]\(|<img\s[^>]*?src=\")([^)\"\s?#]+" + IMG + r")(?:\?v=([^)\"\s#]*))?")
# a version pinned where it would need a hand edit per release
PINS = (
    (re.compile(r"frodhes-\d+\.\d+\.\d+\.fwsc"), "a versioned package name (use frodhes.fwsc and " + DOWNLOAD + ")"),
    (re.compile(r"Latest release: \[?Frodhes \d+\.\d+"), "a numbered 'Latest release' line (link " + LATEST + ")"),
    (re.compile(r"\bVersion \d+\.\d+\.\d+"), "a 'Version X.Y.Z' line (the release says the version)"),
    (re.compile(r"releases/download/v\d+\.\d+\.\d+/"), "a download link to one release (use " + DOWNLOAD + ")"),
)


def short_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:8]


def _pics(root, name, text):
    """[(match, file path)] for every picture link in text"""
    base = os.path.dirname(os.path.join(root, name))
    return [(m, os.path.normpath(os.path.join(base, m.group(1)))) for m in PIC.finditer(text)
            if not re.match(r"[a-z]+:", m.group(1))]


def problems(root=ROOT, docs=DOCS):
    """every problem in the docs, as 'FILE:LINE: what'"""
    out = []
    for name in docs:
        text = open(os.path.join(root, name), encoding="utf-8").read()

        def where(pos):
            return f"{name}:{text.count(chr(10), 0, pos) + 1}"
        for m, path in _pics(root, name, text):
            if not os.path.exists(path):
                out.append(f"{where(m.start())}: {m.group(1)} does not exist")
            elif m.group(2) is None:
                out.append(f"{where(m.start())}: {m.group(1)} has no ?v= (want ?v={short_hash(path)})")
            elif m.group(2) != short_hash(path):
                out.append(f"{where(m.start())}: {m.group(1)}?v={m.group(2)}, the file's hash is {short_hash(path)}")
        for rx, what in PINS:
            for m in rx.finditer(text):
                out.append(f"{where(m.start())}: {m.group(0)!r}: {what}")
    return out


def fix(root=ROOT, docs=DOCS):
    """every picture link's ?v= set to its file's hash (missing files left alone); returns the files changed"""
    changed = []
    for name in docs:
        p = os.path.join(root, name)
        text = open(p, encoding="utf-8").read()
        new, last = [], 0
        for m, path in _pics(root, name, text):
            if os.path.exists(path):
                new += [text[last:m.end(1)], "?v=" + short_hash(path)]
                last = m.end()
        new = "".join(new) + text[last:]
        if new != text:
            open(p, "w", encoding="utf-8").write(new)
            changed.append(name)
    return changed


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--fix" in argv:
        for name in fix():
            print(f"{name}: picture links set to their files' hashes")
    bad = problems()
    for b in bad:
        print(b)
    n = sum(len(_pics(ROOT, d, open(os.path.join(ROOT, d), encoding="utf-8").read())) for d in DOCS)
    print(f"doc_links: {n} picture links in {', '.join(DOCS)}, {len(bad)} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
