"""docs/MOVE.md names only the pages and controls the Move module has (M1-20)."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "MOVE.md").read_text()
LEVELS = json.loads((ROOT / "move" / "module" / "ui_hierarchy.json").read_text())["levels"]


def doc_pages():
    rows = re.findall(r"^\| \*\*(.+?)\*\* \| (.+?) \|$", DOC, re.M)
    return {name: ctl.split(" · ") for name, ctl in rows}


def test_pages_match_module():
    want = {l["name"]: [p["name"] for p in l["params"] if "key" in p] for l in LEVELS.values()}
    assert doc_pages() == want


def test_unverified_marked():
    assert "Nothing on this page has been\nchecked on a Move yet" in DOC
    assert DOC.count("*(not yet verified on a device)*") >= 5


def test_module_name_generic():
    mod = json.loads((ROOT / "move" / "module" / "module.json").read_text())
    assert mod["name"] == "Frodhes" and mod["id"] == "frodhes"
    assert "Frodhes" in DOC and "FRDH" in DOC
