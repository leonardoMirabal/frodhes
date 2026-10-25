#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Release-note fragments: each change (a branch, a PR) adds its own docs/changes/<topic>.md; tools/release.py
assembles them into docs/releases/vX.Y.Z.md and removes them in the release commit. Nobody writes the release notes
by hand, and two PRs never edit the same notes file.

    tools/changes.py              check every fragment (exit 1 and the problems)
    tools/changes.py --preview    the notes they would make (as version X.Y.Z)

A fragment (file name: lowercase letters, digits and '-', then .md) is only these sections, in this order, each at
most once, at least one of them:

    ## New
    ## Changes
    ## Fixed
    ## Tools
    ## Tested

and each section's body is a Markdown list: lines starting "- ", their continuation lines indented by two or more
spaces, blank lines. Nothing else: no other heading, no text before the first section (an <!-- HTML comment --> is
allowed and dropped), no DRAFT line, no version number of the coming release (the release names itself; history such
as "saved by 0.9.3" is fine). The fragments are assembled in file-name order, section by section (a number in front,
01-usb-audio-clicks.md, sets the order by importance; a fragment without one comes after the numbered ones); the notes' head
(the instrument line from docs/MANUAL.md's first line, the download and install) and foot are written here.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join("docs", "changes")
SECTIONS = ("New", "Changes", "Fixed", "Tools", "Tested")
PAGES_URL = "https://leonardomirabal.github.io/frodhes/"
DOWNLOAD = "https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes.fwsc"
NAME = re.compile(r"[a-z0-9][a-z0-9-]*\.md")


class Bad(Exception):
    pass


def parse(text, name="fragment"):
    """{section: body} of a fragment's text; Bad with every problem"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    errs, out, cur = [], {}, None
    for i, line in enumerate(text.splitlines(), 1):
        h = re.match(r"\s*#+\s*(.*?)\s*$", line)
        if h:
            title = h.group(1)
            if not line.startswith("## ") or title not in SECTIONS:
                errs.append(f"{name}:{i}: heading {line.strip()!r}: only '## ' + one of {', '.join(SECTIONS)}")
            elif title in out:
                errs.append(f"{name}:{i}: '## {title}' a second time")
            elif cur and SECTIONS.index(title) < SECTIONS.index(cur):
                errs.append(f"{name}:{i}: '## {title}' after '## {cur}' (the order is {', '.join(SECTIONS)})")
            else:
                cur = title
                out[cur] = []
            continue
        if re.match(r"^\W*DRAFT", line):
            errs.append(f"{name}:{i}: a DRAFT line (fragments are final; the release dry-run shows the notes)")
        elif not line.strip():
            if cur:
                out[cur].append("")
        elif cur is None:
            errs.append(f"{name}:{i}: text before the first section heading")
        elif not (line.startswith("- ") or re.match(r"  +\S", line)):
            errs.append(f"{name}:{i}: not a list line ('- ' or an indented continuation): {line[:60]!r}")
        else:
            out[cur].append(line.rstrip())
    body = {s: "\n".join(v).strip("\n") for s, v in out.items()}
    for s, v in body.items():
        if not v:
            errs.append(f"{name}: '## {s}' is empty (leave the section out)")
    if not body and not errs:
        errs.append(f"{name}: no section")
    if errs:
        raise Bad("\n".join(errs))
    return body


def load(root=ROOT):
    """[(file name, {section: body})] of every fragment in file-name order; Bad with every problem"""
    d = os.path.join(root, DIR)
    names = sorted(f for f in os.listdir(d) if f.endswith(".md")) if os.path.isdir(d) else []
    frags, errs = [], []
    for f in names:
        if not NAME.fullmatch(f):
            errs.append(f"{DIR}/{f}: file name not lowercase letters, digits and '-'")
            continue
        try:
            frags.append((f, parse(open(os.path.join(d, f), encoding="utf-8").read(), f"{DIR}/{f}")))
        except Bad as e:
            errs.append(str(e))
    if errs:
        raise Bad("\n".join(errs))
    return frags


def tagline(root=ROOT):
    """'a physically modelled ... for the M-VAVE FM-1' from docs/MANUAL.md's first bold line"""
    m = re.search(r"^\*\*A (physically modelled .+? for the M-VAVE FM-1)\.\*\*",
                  open(os.path.join(root, "docs", "MANUAL.md"), encoding="utf-8").read(), re.M)
    if not m:
        raise Bad("docs/MANUAL.md: no '**A physically modelled ... for the M-VAVE FM-1.**' line for the notes' head")
    return "a " + m.group(1)


def assemble(ver, frags, root=ROOT):
    """the release notes for ver from frags (load()); Bad if there are none or one names ver"""
    if not frags:
        raise Bad(f"no release-note fragments in {DIR}/ (each change adds its own {DIR}/<topic>.md)")
    for f, secs in frags:
        for s, body in secs.items():
            if re.search(r"(?<![\d.])" + re.escape(ver) + r"(?![\d])", body):
                raise Bad(f"{DIR}/{f}: '## {s}' names {ver}, the coming release (the notes' headings say it)")
    parts = [f"**Frodhes {ver}**: {tagline(root)}. Every note is computed live from the mechanics of the instrument; "
             "there are no samples.",
             f"**Download:** `frodhes.fwsc` below (the same file as `frodhes-{ver}.fwsc`; the newest is always at "
             f"{DOWNLOAD}). **Try it first in your browser:** {PAGES_URL}",
             "### Install\n```sh\npip install mido python-rtmidi\npython3 tools/fm1_install.py frodhes.fwsc\n```\n"
             "If anything goes wrong, switch the FM-1 off, hold **OCT−** and switch it on to get the USB rescue, then "
             "install again."]
    for s in SECTIONS:
        bodies = [secs[s] for _, secs in frags if s in secs]
        if bodies:
            parts.append(f"### {'New in ' + ver if s == 'New' else s}\n" + "\n".join(bodies))
    parts.append("Full manual: https://github.com/leonardoMirabal/frodhes/blob/main/docs/MANUAL.md · Built on "
                 "[Dinghy](https://github.com/zvenson/dinghy). GPL-3.0.")
    return "\n\n".join(parts) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        frags = load()
        if "--preview" in argv:
            print(assemble("X.Y.Z", frags), end="")
        else:
            tagline()
            print(f"{DIR}: {len(frags)} fragment(s), all well formed: {', '.join(f for f, _ in frags) or '-'}")
    except Bad as e:
        print(e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
