#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Move module's description (move/module/) from the firmware's own tables: params.c PARAMS[] (ranges, defaults,
enum names, CCs), eng_pages.h (the engines' VOICE cards, swapped by TYPE) and ui.c (the FM-1's pages: PAGE_TITLE,
PAGE_S, PAGE_LAB, PG_ORDER, and slot_val's ranges for the settings cards). Nothing is typed in twice: a new
parameter, card or enum name shows up here when the tool is rerun, and tests/py/test_move_module.py fails until it is.

  tools/gen_move_module.py           write move/module/*
  tools/gen_move_module.py --check   exit 1 if a committed file is not what the sources give

What it writes:

  module.json         the module's metadata (id frodhes, name Frodhes, api_version 2, a sound generator); small on
                      purpose: the host reads a sound generator's pages only from get_param("ui_hierarchy"), and it
                      caps module.json at 8 KB
  ui_hierarchy.json   the knob pages the plugin serves from get_param("ui_hierarchy"): levels of <= 8 knobs, each
                      two FM-1 pages of 4 cards (MOVE_LEVELS); the engine's VOICE levels carry visible_if on the
                      read-only key ui_engine (0 the Rhodes types, 1 VIBES, 2 MARIMBA: eng_pages.h TYPE_UI), so the
                      pages follow TYPE; the root level holds the preset browser (preset / preset_count /
                      preset_name) and SUSTAIN, a latch on CC 64 (the Move has no pedal jack)
  chain_params.json   every key once, with its range or options (get_param("chain_params"))
  help.json           the on-device help (lines <= 20 characters, ASCII)
  move_ui.h           the same for the plugin's C side: MOVE_KEYS[] (key -> parameter id or ui.c slot, its CC, the
                      offset between the wire value and the slot's 0..hi value) and the two JSON texts as literals,
                      so get_param never reads a file

The wire value of every key is the card's value, shifted so it reads as on the FM-1's screen where that is a plain
number: wire = slot_val (ui.c, 0..hi) - off, times scale for the tempo (BPM in tenths). Enums go by index.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "firmware", "src")
OUT = os.path.join(ROOT, "move", "module")
FILES = ("module.json", "ui_hierarchy.json", "chain_params.json", "help.json", "move_ui.h", "sustain.js", "screen.js")
UI_CORE = os.path.join(ROOT, "move", "ui", "screen_core.js")   # Frodhes' own screen: the hand-written part
# Frodhes' own screen (M6-02, private plan 05 M6-01 option F): a fullscreen canvas the first page's PAGES cell
# opens. screen.js = the pages (FR_PAGES, the levels below as the screen shows them) + FR_CONF + move/ui/screen_core.js.
# The live keys the host reads for it (at most four, one a tick): rev (a change the screen did not make: re-read),
# ui_engine (the VOICE pages follow TYPE), s_sustain (the SUS mark), preset_name (the first page). The COMPRESSOR
# page's gain-reduction meter reads r_cmp_gr itself each tick (getParam in onValues); fullscreen_live_ms 100 (the
# PEDAL screen's) so it moves at 10 Hz (the FM-1's meter: 30 Hz).
SCREEN = {"key": "s_screen", "name": "PAGES", "type": "canvas", "canvas_script": "screen.js", "enterable": True,
          "show_footer": False, "show_value": False, "fullscreen_live_ms": 100,
          "extra_keys": ["rev", "ui_engine", "s_sustain", "preset_name"]}
SCREEN_CONF = {"hold_ms": 1000,    # the large card stays this long after the knob is let go (tune on the device)
               "fast_ms": 40,      # two detents closer than this: a fast turn ...
               "fast_mul": 4,      # ... moves a wide range (> 24 steps) this many steps a detent
               "settle_ms": 600,   # a value written this recently is not re-read (the write may still be queued)
               "sus_cc": None}     # the claimed sustain button (SUS_BTN_CC)
# The sustain control (M1-13b): a Move button the module claims (capabilities.claims_ccs: while Frodhes' screen is up
# the button reaches the module's UI script and not the Move; Schwung delivers claimed buttons to UI scripts only).
# sustain.js, the SUS screen (a canvas), turns its press / release into set_param("s_susbtn", "1" / "0"), which the
# plugin hands to the FM-1's REC button code (hold = the pedal, tap = latch). The direct route, a knob or jog-wheel
# touch (capabilities.touch_observe, the plugin's on_midi), is off until dev_sus_touch picks one (the device session).
SUS_BTN_CC = 52                    # the Capture button (Schwung shadow_midi.h CC_CAPTURE): D1 confirms or picks another
LABEL_MAX = 5                      # the knob grid's cell: about five capitals
VALUE_MAX = 5                      # an enum option on the Move: the same five
# The Move's abbreviations of the firmware's option names longer than VALUE_MAX, one scheme everywhere (the pages, the
# screen, the docs, the mock-ups): four letters, the word's first syllable and the consonant after it (DORIAN DORN,
# DEGREE DEGR), a digit kept (DIA 7TH DIA7). A new long name fails the generator until it is named here.
VALUE_SHORT = {"DEGREE": "DEGR", "DORIAN": "DORN", "LYDIAN": "LYDN", "HARMON": "HARM", "MELODIC": "MELO",
               "MPENTA": "MPNT", "DIATONIC": "DIAT", "DIA 7TH": "DIA7", "RANDOM": "RAND"}
HELP_MAX = 20                      # a help line: about 20 characters on the 128 px screen

# the FM-1's pages that have no meaning on the Move (its knobs are direct, it has no screen or key lights of ours)
FM1_ONLY_PAGES = {"PERFORM": "the Move's knobs are direct: no assignable performance knobs",
                  "ASSIGN": "the Move's knobs are direct: no assignable performance knobs",
                  "DISPLAY": "the FM-1's screen and key lights"}
# the FM-1's settings cards that are hardware only (ui.c S_*), with the reason
FM1_ONLY_SLOTS = {"S_KVEL": "the FM-1's own keys' velocity", "S_TOUCH": "the FM-1's own keys' touch",
                  "S_USBVOL": "the FM-1's USB audio", "S_TRS": "the FM-1's TRS MIDI jack",
                  "S_COLOR": "the FM-1's screen", "S_BRIGHT": "the FM-1's screen", "S_LEDS": "the FM-1's key lights"}
# the settings cards on the Move: their options (a names array of the firmware's), or a number range as shown:
# wire = slot_val - off; unit as the host shows it. hi always comes from ui.c slot_val (checked).
SLOTS = {
    "S_TUNE": {"off": 50, "unit": "c"},
    "S_BPM": {"off": -400, "scale": 10, "unit": "BPM"},     # slot = BPM x 10 - 400: 40.0 .. 240.0
    "S_CLOCK": {"names": "CLOCK_NAME"},
    "S_SCMODE": {"names": "SCMODE_NAME"},
    "S_SCALE": {"names": "SCALE_NAME"},
    "S_ROOT": {"names": "NOTE_NAME"},
    "S_TRANS": {"off": 24, "unit": "st"},
    "S_TWKIND": {"names": "TWKIND_NAME"},
    "S_ARPMODE": {"names": "ARP_MODE_NAME", "cc": 52},
    "S_ARPRATE": {"names": "ARP_RATE_NAME", "cc": 53},
    "S_ARPOCT": {"off": -1, "cc": 54},                         # 1 .. 4 octaves
    "S_ARPGATE": {"off": -10, "unit": "%", "cc": 55},          # 10 .. 100 %
    "S_CHTYPE": {"names": "CH_TYPE_NAME", "cc": 56},
    "S_CHINV": {"names": "CH_INV_NAME", "cc": 57},
    "S_CHVOICE": {"names": "CH_VOICE_NAME", "cc": 58},
    "S_CHSTRUM": {"cc": 59},                                   # x 5 ms
    "S_SQBARS": {"min": 1, "cc": 61},
    "S_SQQUANT": {"names": "SQ_QUANT_NAME", "cc": 62},
    "S_SQSWING": {"off": -50, "unit": "%", "cc": 46},          # 50 .. 75 %
    "S_SQEDIT": {"options": ["UNDO", "ERASE"], "cc": 45},
    "S_MCH": {"options": ["ALL"] + [str(c) for c in range(1, 17)]},
}
# keys of the Move's own, with no FM-1 card: a CC sent as is (latch: 0 / 127; trigger: 127 on a write)
EXTRA = {
    "s_sustain": {"name": "SUS", "cc": 64, "options": ["OFF", "ON"], "kind": "MK_LATCH",
                  "help": "the sustain pedal (CC 64), latched"},
    "s_susbtn": {"name": "PEDAL", "cc": 0, "options": ["UP", "DOWN"], "kind": "MK_BTN",
                 "help": "the sustain control: held = the pedal, a tap latches, a tap again releases"},
    "s_sqplay": {"name": "PLAY", "cc": 60, "options": ["STOP", "PLAY"], "kind": "MK_LATCH",
                 "help": "the sequencer plays"},
    "s_sqrec": {"name": "REC", "cc": 63, "options": ["OFF", "REC"], "kind": "MK_LATCH",
                "help": "the sequencer records"},
    "s_sqclear": {"name": "CLEAR", "cc": 43, "options": ["-", "CLEAR"], "kind": "MK_TRIG",
                  "help": "the pattern cleared"},
    "s_squndo": {"name": "UNDO", "cc": 44, "options": ["-", "UNDO"], "kind": "MK_TRIG",
                 "help": "the last take undone"},
    # M5-01: the FM-1's FX button (CC 111, the bypass): every effect off with its settings kept; ON (not bypassed) by
    # default, as on the FM-1
    "s_fx": {"name": "FX", "cc": 111, "options": ["ON", "OFF"], "kind": "MK_LATCH",
             "help": "the effects on, or all off with their settings kept (the FM-1's FX button)"},
}
# the Move-only mappings (plan 05 M6-03, U9): a source the FM-1 has no use for (channel pressure, poly pressure: the
# Move's pads) turned into one of the app's own CCs, out = LO + (HI - LO) x pressure / 127 (HI < LO inverts), sent
# only when it changes; poly pressure follows the most pressed note. Every mapping is off (CC 0) by default, so
# nothing changes until one is set (Gate M); which ones ship on is the user's pick (U9). The bridge keeps them in
# its state (move_bridge.h MB_NMAP, "map" in the state JSON); the key's slot field is the mapping's index
for _i, (_k, _src) in enumerate((("m_at", "channel pressure"), ("m_pa", "poly pressure (the most pressed note)"))):
    EXTRA[_k + "_cc"] = {"name": _k[2:].upper() + " CC", "cc": 0, "kind": "MK_MAP", "map": 3 * _i, "min": 0,
                         "max": 119, "default": 0, "help": _src + " to this CC (0 off)"}
    EXTRA[_k + "_lo"] = {"name": _k[2:].upper() + " LO", "cc": 0, "kind": "MK_MAP", "map": 3 * _i + 1, "min": 0,
                         "max": 127, "default": 0, "help": "the CC's value at no " + _src.split(" (")[0]}
    EXTRA[_k + "_hi"] = {"name": _k[2:].upper() + " HI", "cc": 0, "kind": "MK_MAP", "map": 3 * _i + 2, "min": 0,
                         "max": 127, "default": 127, "help": "the CC's value at full " + _src.split(" (")[0]}
MAP_KEYS = ["m_at_cc", "m_at_lo", "m_at_hi", "m_pa_cc", "m_pa_lo", "m_pa_hi"]
# the Move's levels: (key, title, the FM-1 pages it holds (their cards in order), extra keys after them). "VOICE"
# stands for an engine's VOICE pages: one level per engine, VOICE 1 + 2, and VOICE 3 (VIBES' MOTOR) its own level.
MOVE_LEVELS = [
    ("root", "FRODHES", ["FRODHES"], ["p_spread", "s_sustain", "s_susbtn", "s_fx", "s_screen"]),
    ("voice", "VOICE", ["VOICE"], []),
    ("comp", "COMPRESSOR", ["COMPRESSOR"], ["p_cmp_sw", "p_cmp_rel"]),
    ("drv_flt", "DRIVE/FILTER", ["DRIVE", "FILTER"], []),
    ("trm_pha", "TREM/PHASER", ["TREM/WAH", "PHASER"], []),
    ("cho_dly", "CHORUS/DELAY", ["CHORUS", "DELAY"], []),
    ("reverb", "REVERB", ["REVERB", "REVERB 2"], []),
    ("tmp_scl", "TEMPO/SCALE", ["TEMPO", "SCALE"], []),
    ("chd_arp", "CHORD/ARP", ["CHORD", "ARPEGGIO"], []),
    ("seq", "SEQUENCER", ["SEQUENCER"], ["s_sqplay", "s_sqrec", "s_sqclear", "s_squndo"]),
    ("lfo_set", "LFO/SETUP", ["LFO", "SETUP", "MIDI"], []),
    ("maps", "MAPPINGS", [], MAP_KEYS),
]
# extra keys that are parameters with no FM-1 card (they keep their CC on the FM-1)
MOTOR_EXTRA = ["p_mot_sync"]       # VIBES' MOTOR level: the motor's sync (CC 51, no card on the FM-1)
# parameters reached through another key (the FM-1's combined card)
VIA = {"P_TW_SW": ("s_twkind", "TREM/WAH's EFFECT card: OFF / PAN / TREM / WAH sets the switch and the mode"),
       "P_TW_MODE": ("s_twkind", "TREM/WAH's EFFECT card: OFF / PAN / TREM / WAH sets the switch and the mode")}
# parameters left MIDI-only on the Move, with the reason (none today: tests/py/test_move_module.py lists the rule)
MIDI_ONLY = {"P_CP_FDBK": "reserved: the chorus has no feedback yet (CC 24 is accepted and saved, no effect)"}
# the card's label on the FM-1 -> the Move's cell (<= 5 characters); labels that fit are kept as they are
SHORT = {"THRESH": "THRSH", "ATTACK": "ATK", "MAKEUP": "MKUP", "EFFECT": None, "FEEDBK": "FDBK",
         "TREM SYNC": "TRSYN", "PHA SYNC": "PHSYN", "DLY SYNC": "DLSYN", "TRANSP": "TRNSP", "INVERSION": "INV",
         "VOICING": "VOICE", "OCTAVES": "OCT", "RETRIG": "TRIG", "DLY TONE": "DLTON", "CHANNEL": "CHAN",
         "PRE-DELAY": "PDLY", "LOW CUT": "LCUT", "STRIKE": "STRK", "MALLET": "MALLT", "HAMMER": "HAMMR",
         "RELEASE": "REL", "WEIGHT": "WGHT", "DAMPER": "DAMPR", "MOTOR": "MOTOR", "AMOUNT": "AMT"}
# EFFECT (each effect page's K1: its switch or TYPE) is named after the effect, so two on one level differ
EFFECT_NAME = {"DRIVE": "DRIVE", "TREM/WAH": "TREM", "PHASER": "PHASE", "CHORUS": "CHOR", "DELAY": "DELAY"}
TYPE_NAME = {"FILTER": "FILT", "REVERB": "RVB", "CHORD": "CHORD"}
ENGINE_TITLE = ["PIANO", "VIBES", "MARIMBA"]   # eng_pages.h ENG_UI rows (generic names: the help's)


def strip_comments(t):
    return re.sub(r"/\*.*?\*/", "", re.sub(r"//[^\n]*", "", t), flags=re.S)


def read(name):
    with open(os.path.join(SRC, name)) as f:
        return strip_comments(f.read())


def sources():
    """every firmware source's text, comments removed"""
    out = {}
    for n in sorted(os.listdir(SRC)):
        if n.endswith((".c", ".h")):
            out[n] = read(n)
    return out


def cint(tok, defs):
    """a C constant expression of ints and #define names (u suffixes, casts): 'ARP_NMODE - 1' -> 5; None if a name
    is not a plain #define"""
    expr = re.sub(r"\(\w+_t\)", "", re.sub(r"\b(\d+)u\b", r"\1", tok.strip()))
    if not re.fullmatch(r"[\w\s+\-*/()]+", expr) or any(n not in defs for n in re.findall(r"[A-Za-z_]\w*", expr)):
        return None
    return int(eval(expr, {"__builtins__": {}}, dict(defs)))   # (only ints, #define names and + - * / ( ))


def model():
    """the firmware's tables, parsed"""
    src = sources()
    allt = "\n".join(src.values())
    defs = {}
    for m in re.finditer(r"^\s*#define\s+(\w+)\s+\(?(\d+)u?\)?\s*$", allt, re.M):
        defs.setdefault(m.group(1), int(m.group(2)))
    for m in re.finditer(r"enum \{([^}=]*)\}", allt):             # plain enums: A, B, .., X_N (ARP_NMODE)
        for i, n in enumerate(x.strip() for x in m.group(1).split(",")):
            if re.fullmatch(r"[A-Za-z_]\w*", n):
                defs.setdefault(n, i)
    names = {m.group(1): re.findall(r'"([^"]*)"', m.group(2))
             for m in re.finditer(r"static const char \*const (\w+)\[[^\]]*\] = \{([^}]*)\};", allt)}
    pc = src["params.c"]
    penum = re.search(r"enum \{ (P_TYPE.*?)P_COUNT \};", pc, re.S).group(1)
    pids = [p.strip() for p in penum.split(",") if p.strip()]
    params = {}
    for m in re.finditer(r'\[(P_\w+)\] = \{"([^"]*)", (\d+), (\d+), (F_\w+), (\d+), (\w+)\}', pc):
        params[m.group(1)] = {"id": pids.index(m.group(1)), "lab": m.group(2), "hi": int(m.group(3)),
                              "def": int(m.group(4)), "fmt": m.group(5), "cc": int(m.group(6)),
                              "names": None if m.group(7) == "0" else m.group(7)}
    assert sorted(params) == sorted(pids), "PARAMS[] and the P_ enum disagree"
    ui = src["ui.c"]
    pg_enum = [p.strip() for p in re.search(r"enum \{ (PG_PERF.*?)PG_N \};", ui, re.S).group(1).split(",") if p.strip()]
    titles = re.findall(r'"([^"]*)"', re.search(r"PAGE_TITLE\[PG_N\] = \{(.*?)\};", ui, re.S).group(1))
    rows_s = re.findall(r"\{([^{}]*)\}", re.search(r"PAGE_S\[PG_N\]\[4\] = \{(.*?)\n\};", ui, re.S).group(1))
    rows_l = re.findall(r"\{([^{}]*)\}", re.search(r"PAGE_LAB\[PG_N\]\[4\] = \{(.*?)\n\};", ui, re.S).group(1))
    order = [p.strip() for p in re.search(r"PG_ORDER\[PG_NORD\] = \{(.*?)\};", ui, re.S).group(1).split(",")]
    assert len(titles) == len(pg_enum) == len(rows_s) == len(rows_l), "ui.c's page tables disagree"
    lab_tok = {"UI_TRS_LAB": "TRS IN", "UI_LEDS_LAB": "LEDS"}      # (the FM-1's: the SMK-37 leaves them empty)
    slot_tok = {"UI_TRS": "S_TRS", "UI_LEDS": "S_LEDS"}
    pages = {}
    for i, pg in enumerate(pg_enum):
        cards = [slot_tok.get(t.strip(), t.strip()) for t in rows_s[i].split(",")]
        labs = []
        for t in re.findall(r'"[^"]*"|\w+', rows_l[i]):
            labs.append(t[1:-1] if t.startswith('"') else lab_tok[t])
        pages[pg] = {"title": titles[i], "cards": cards, "labs": labs}
    sv = re.search(r"static int32_t slot_val\(uint32_t s, int32_t \*hi\)\n\{(.*?)\n\}", ui, re.S).group(1)
    slot_hi = {}
    for m in re.finditer(r"case (S_\w+): \*hi = ([^;]+);", sv):
        slot_hi[m.group(1)] = cint(m.group(2), defs)
    ep = read("eng_pages.h")
    engines = []
    rows = re.findall(r"ENG_UI_ROW\((ENG_\w+),", ep)
    for arr in rows:
        body = re.search(r"static const ectl_t " + arr + r"\[\] = \{(.*?)\n\};", ep, re.S).group(1)
        ctls = []
        for m in re.finditer(r'\{((?:P|S)_\w+), "([^"]+)", (EK_\w+), (EU_\w+)([^}]*)\}', body):
            rest = [x.strip() for x in m.group(5).split(",") if x.strip()]
            c = {"slot": m.group(1), "name": m.group(2), "kind": m.group(3), "unit": m.group(4),
                 "head": int(rest[0]) if rest else 0}
            if m.group(3) == "EK_DETENT":
                c["detents"] = list(zip(names[rest[3]], [int(x) for x in re.search(
                    r"static const uint8_t " + rest[4] + r"\[\d+\] = \{([^}]*)\}", ep).group(1).split(",")]))
            ctls.append(c)
        engines.append(ctls)
    type_ui = [int(x) for x in re.search(r"TYPE_UI\[N_TYPE_N\] = \{([^}]*)\}", ep).group(1).split(",")]
    return {"params": params, "pids": pids, "names": names, "pages": pages, "order": order, "slot_hi": slot_hi,
            "engines": engines, "type_ui": type_ui, "defs": defs}


def pkey(p):
    return "p_" + p[2:].lower()


def skey(s):
    return "s_" + s[2:].lower()


def short(label, fallback):
    if label in SHORT and SHORT[label]:
        return SHORT[label]
    if len(label) <= LABEL_MAX:
        return label
    return fallback


def short_value(o):
    v = VALUE_SHORT.get(o, o)
    assert len(v) <= VALUE_MAX, f"the option {o!r} is longer than {VALUE_MAX}: name it in VALUE_SHORT"
    return v


def key_def(m, slot, label, key=None, bip=True):
    """a key's entry: (key, its JSON item, its C row) for a parameter id or a ui.c slot; key: another name for the
    same parameter (an engine's card), bip: a bipolar parameter reads -64..63 (not on a card with named detents)"""
    if slot.startswith("P_"):
        p = m["params"][slot]
        off = 64 if p["fmt"] == "F_BIP" and bip else 0
        key = key or pkey(slot)
        item = {"key": key, "name": label}
        if p["fmt"] in ("F_ENUM", "F_DIV"):
            opts = m["names"][p["names"]][:p["hi"] + 1]
            assert len(opts) == p["hi"] + 1, (slot, opts)
            item.update({"type": "enum", "options": [short_value(o) for o in opts], "default": p["def"]})
        else:
            item.update({"type": "int", "min": -off, "max": p["hi"] - off, "default": p["def"] - off})
        return item, {"key": key, "slot": slot, "cc": p["cc"], "off": off, "scale": 1, "kind": "MK_PARAM"}
    sd = SLOTS[slot]
    hi = m["slot_hi"][slot]
    item = {"key": skey(slot), "name": label}
    off, scale = sd.get("off", 0), sd.get("scale", 1)
    if "names" in sd or "options" in sd:
        opts = sd["options"] if "options" in sd else m["names"][sd["names"]]
        assert len(opts) == hi + 1, (slot, hi, opts)
        item.update({"type": "enum", "options": [short_value(o) for o in opts]})
    elif scale != 1:
        item.update({"type": "float", "min": (0 - off) / scale, "max": (hi - off) / scale, "step": 1.0 / scale})
    else:
        item.update({"type": "int", "min": max(sd.get("min", 0), 0) - off, "max": hi - off})
    if sd.get("unit"):
        item["unit"] = sd["unit"]
    return item, {"key": skey(slot), "slot": slot, "cc": sd.get("cc", 0), "off": off, "scale": scale,
                  "kind": "MK_SLOT"}


def extra_def(k):
    e = EXTRA[k]
    if e["kind"] == "MK_MAP":                  # a number, the bridge's own (the slot field: the mapping's index)
        return ({"key": k, "name": e["name"], "type": "int", "min": e["min"], "max": e["max"], "default": e["default"]},
                {"key": k, "slot": str(e["map"]), "cc": 0, "off": 0, "scale": 1, "kind": "MK_MAP"})
    item = {"key": k, "name": e["name"], "type": "enum", "options": e["options"]}
    if e["kind"] == "MK_TRIG":
        item["access"] = "write"
    if e["kind"] == "MK_BTN":                  # the SUS screen: a canvas the hierarchy opens (sustain.js), no knob
        item = {"key": k, "name": e["name"], "type": "canvas", "canvas_script": "sustain.js", "show_value": False,
                "extra_keys": ["s_sustain", "s_susbtn"], "fullscreen_live_ms": 100}
    return item, {"key": k, "slot": "S_NONE", "cc": e["cc"], "off": 0, "scale": 1, "kind": e["kind"]}


def engine_key(m, c):
    """an engine's card on a parameter the first engine (the piano) names otherwise: its own key (p_strike for VIBES'
    and MARIMBA's STRIKE on P_TONE), so a key means one thing; else None (the parameter's key)"""
    first = {x["slot"]: x["name"] for x in m["engines"][0]}
    if c["slot"].startswith("P_") and c["slot"] in first and first[c["slot"]] != c["name"]:
        return "p_" + re.sub(r"[^a-z0-9]", "", c["name"].lower())
    return None


def card_label(m, page, k):
    lab = m["pages"][page]["labs"][k]
    slot = m["pages"][page]["cards"][k]
    title = m["pages"][page]["title"]
    if lab == "EFFECT":
        return EFFECT_NAME[title]
    if lab == "TYPE" and title in TYPE_NAME:   # FILTER's and REVERB's K1: named after the page
        return TYPE_NAME[title]
    fb = m["params"][slot]["lab"] if slot.startswith("P_") else lab[:LABEL_MAX]
    return short(lab, fb)


def build(m):
    """-> (levels dict, chain_params list, C key rows, reach {P_: where})"""
    page_of = {v["title"]: pg for pg, v in m["pages"].items()}
    levels, chain, crow, reach, seen = {}, [], [], {}, {}

    def add(lvl, item, row, where):
        lvl["params"].append(item)
        lvl["knobs"].append(item["key"])
        if row["key"] not in seen:
            seen[row["key"]] = 1
            c = {k: v for k, v in item.items()}
            if c.get("type") == "canvas":          # (chain_params: the key's values, not the screen)
                c = {"key": c["key"], "name": c["name"], "type": "enum", "options": EXTRA[c["key"]]["options"]}
            chain.append(c)
            crow.append(row)
        if row["slot"].startswith("P_"):
            reach.setdefault(row["slot"], where)

    def page_cards(lvl, title):
        pg = page_of[title]
        for k, slot in enumerate(m["pages"][pg]["cards"]):
            if slot == "S_NONE" or slot in FM1_ONLY_SLOTS:
                continue
            item, row = key_def(m, slot, card_label(m, pg, k))
            add(lvl, item, row, lvl["name"])

    def extras(lvl, keys):
        for k in keys:
            if k == SCREEN["key"]:                 # a screen of ours: a hierarchy item only (no value, no C row)
                lvl["params"].append(dict(SCREEN))
                lvl["knobs"].append(k)
                continue
            if k in EXTRA:
                item, row = extra_def(k)
            else:
                slot = "P_" + k[2:].upper()
                p = m["params"][slot]
                item, row = key_def(m, slot, short(p["lab"], p["lab"]))
            add(lvl, item, row, lvl["name"])

    root_links = []
    for key, title, pgs, ext in MOVE_LEVELS:
        if pgs == ["VOICE"]:
            for e, ctls in enumerate(m["engines"]):
                groups = [ctls[i:i + 8] for i in range(0, len(ctls), 8)]
                for g, grp in enumerate(groups):
                    lk = "%s_%d%s" % (key, e, "" if g == 0 else "_%d" % (g + 1))
                    lt = ENGINE_TITLE[e] + (" " + ("VOICE" if g == 0 else grp[0]["name"]))
                    lvl = {"name": lt, "params": [], "knobs": [], "visible_if": {"param": "ui_engine", "equals": e}}
                    for c in grp:
                        fb = m["params"][c["slot"]]["lab"] if c["slot"].startswith("P_") else c["name"][:LABEL_MAX]
                        item, row = key_def(m, c["slot"], short(c["name"], fb), engine_key(m, c),
                                            c["kind"] != "EK_DETENT")
                        add(lvl, item, row, lt)
                    if g == len(groups) - 1 and g > 0:
                        extras(lvl, MOTOR_EXTRA)
                    levels[lk] = lvl
                    root_links.append({"level": lk, "label": lt})
            continue
        lvl = {"name": title, "params": [], "knobs": []}
        for t in pgs:
            page_cards(lvl, t)
        extras(lvl, ext)
        assert len(lvl["knobs"]) <= 8, (key, lvl["knobs"])
        levels[key] = lvl
        if key != "root":
            root_links.append({"level": key, "label": title})
    root = levels["root"]
    root["list_param"], root["count_param"], root["name_param"] = "preset", "preset_count", "preset_name"
    root["params"] += root_links
    for p in m["pids"]:
        if p not in reach and p in VIA:
            reach[p] = "via " + VIA[p][0]
    return levels, chain, crow, reach


def dumps(o):
    return json.dumps(o, indent=2, ensure_ascii=True) + "\n"


def help_lines(text):
    """words -> lines of <= HELP_MAX characters"""
    out, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > HELP_MAX:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur:
        out.append(cur)
    return out


def help_json(m, levels, reach):
    kids = [{"title": "Overview", "lines": help_lines(
        "Physical models of an electric piano (four types), a vibraphone and a marimba. No samples. TYPE on the "
        "first page picks the instrument; its VOICE pages follow it.") + [""] + help_lines(
        "SUS holds the notes as the sustain pedal does; a pedal on CC 64 works too. On the PEDAL screen the Capture "
        "button is the pedal: hold it, or tap to latch and tap again to let go.") + [""] + help_lines(
        "PAGES opens Frodhes' own screen: eight cards a page, a touched knob shown large. The jog wheel turns the "
        "pages; a click lists them. Capture is the pedal there too. FX turns all effects off or on.")}]
    pages = []
    for k, lvl in levels.items():
        lines = []
        for i, key in enumerate(lvl["knobs"]):
            item = next(p for p in lvl["params"] if p.get("key") == key)
            lines.append("K%d %s" % (i + 1, item["name"]))
        pages.append({"title": lvl["name"][:HELP_MAX], "lines": lines})
    kids.append({"title": "Pages", "children": pages})
    midi = help_lines("Notes with velocity and release velocity, pitch bend (piano types), sustain CC 64 with half "
                      "pedal, program change 0-15 F01-F16, 16-31 U01-U16, 32-34 F17-F19, MIDI clock. Every "
                      "parameter has its CC, as on the FM-1:")
    for p in m["pids"]:
        cc = m["params"][p]["cc"]
        if cc:
            midi.append("CC %d %s" % (cc, m["params"][p]["lab"]))
    kids.append({"title": "MIDI", "lines": midi})
    return {"title": "Frodhes", "children": kids}


def c_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def c_header(hier, chain, crow):
    lines = ["/* SPDX-License-Identifier: GPL-3.0-only */",
             "/* GENERATED by tools/gen_move_module.py from params.c, eng_pages.h and ui.c - do not edit. */",
             "/* The Move's keys: MOVE_KEYS[] (a parameter id < P_COUNT or a ui.c S_* slot, its CC (0: none), the",
             " * offset off and scale: wire = (slot value - off) / scale) and the texts get_param serves. Included by",
             " * the plugin after frodhes.c (P_* / S_* in scope). */",
             "#ifndef MOVE_UI_H", "#define MOVE_UI_H",
             "#define MOVE_GATE_KEY \"ui_engine\"   /* get_param: TYPE_UI[PV(P_TYPE)] (eng_pages.h), the VOICE levels' gate */",
             "enum { MK_PARAM, MK_SLOT, MK_LATCH, MK_TRIG, MK_BTN, MK_MAP };   /* PV(id) / ui.c slot_val / a CC 0 or 127 /",
             "                                                   * a CC 127 / the sustain control (the FM-1's REC button) /",
             "                                                   * a Move-only mapping's setting (slot: its index) */",
             "typedef struct { const char *key; uint8_t slot, cc, kind, scale; int16_t off; } move_key_t;",
             "static const move_key_t MOVE_KEYS[] = {"]
    for r in crow:
        lines.append("    {%s, %s, %d, %s, %d, %d}," % (c_str(r["key"]), r["slot"], r["cc"], r["kind"], r["scale"],
                                                       r["off"]))
    lines.append("};")
    lines.append("#define MOVE_NKEYS (sizeof MOVE_KEYS / sizeof MOVE_KEYS[0])")
    for name, obj in (("MOVE_UI_HIERARCHY", hier), ("MOVE_CHAIN_PARAMS", chain)):
        txt = json.dumps(obj, separators=(",", ":"), ensure_ascii=True)
        lines.append("static const char %s[] =" % name)
        for i in range(0, len(txt), 100):
            lines.append("    " + c_str(txt[i:i + 100]) + ("" if i + 100 < len(txt) else ";"))
    lines.append("#endif")
    return "\n".join(lines) + "\n"


def outputs():
    m = model()
    levels, chain, crow, reach = build(m)
    hier = {"levels": levels}
    module = {"id": "frodhes", "name": "Frodhes", "abbrev": "FRDH", "version": "0.0.0-dev",
              "description": "Physical-model electric piano, vibraphone and marimba",
              "author": "Frodhes", "dsp": "dsp.so", "api_version": 2, "component_type": "sound_generator",
              # requires_continuous_processing (Schwung 1.8.1 chain_synth_load.c): the host otherwise stops calling
              # render_block after ~1 s of silence, and the app's clock (the arp, the sequencer, the tempo's beat,
              # the settings' save) runs on render_block
              "capabilities": {"audio_out": True, "midi_in": True, "chainable": True,
                               "requires_continuous_processing": True, "component_type": "sound_generator",
                               "claims_ccs": [SUS_BTN_CC], "touch_observe": True}}
    return {"module.json": dumps(module), "ui_hierarchy.json": dumps(hier), "chain_params.json": dumps(chain),
            "help.json": dumps(help_json(m, levels, reach)), "move_ui.h": c_header(hier, chain, crow),
            "sustain.js": sustain_js(), "screen.js": screen_js(levels)}, m, reach


def screen_pages(levels):
    """the levels as Frodhes' own screen shows them: [{title, gate (ui_engine, -1 always shown), part (an engine's
    VOICE page 0, its next page 1, -1 not an engine page), root, knobs}], each knob {key, name, type, min, max, step,
    options, write}; a canvas cell (PEDAL, PAGES) is no knob there (the screen does both itself), and the first page
    gets the preset as a knob (PRSET)"""
    out = []
    for lk, lvl in levels.items():
        gate = lvl.get("visible_if", {}).get("equals", -1)
        part = (1 if lk.count("_") == 2 else 0) if gate >= 0 else -1
        ks = []
        for key in lvl["knobs"]:
            it = next(p for p in lvl["params"] if p.get("key") == key)
            if it["type"] == "canvas":
                continue
            k = {"key": key, "name": it["name"], "type": it["type"]}
            for f in ("min", "max", "step", "options"):
                if f in it:
                    k[f] = it[f]
            if it.get("access") == "write":
                k["write"] = True
            ks.append(k)
        if lk == "root":
            ks.append({"key": "preset", "name": "PRSET", "type": "preset"})
        assert len(ks) <= 8, (lk, ks)
        out.append({"title": lvl["name"], "gate": gate, "part": part, "root": lk == "root", "knobs": ks})
    return out


def screen_js(levels):
    # the gain-reduction meter's scale (COMPRESSOR): ui.c's GR_DB10, dB x 10 at each of its evenly spaced segments
    m = re.search(r"GR_DB10\[GR_SEGS\] = \{([^}]*)\}", read("ui.c"))
    conf = dict(SCREEN_CONF, sus_cc=SUS_BTN_CC, gr_db10=[int(x) for x in m.group(1).split(",")])
    with open(UI_CORE) as f:
        core = f.read()
    head = ("// SPDX-License-Identifier: GPL-3.0-only\n"
            "// GENERATED by tools/gen_move_module.py from params.c, eng_pages.h, ui.c and move/ui/screen_core.js - do "
            "not edit.\n"
            "// Frodhes' own screen (option F): the pages and the settings, then the screen's code.\n")
    # one closure: nothing of the screen's lands in the host's globals but globalThis.canvas_overlay (and the tests'
    # globalThis.frodhes_screen)
    return (head + "(function () {\n\"use strict\";\nconst FR_PAGES = "
            + json.dumps(screen_pages(levels), separators=(",", ":")) + ";\n"
            + "const FR_CONF = " + json.dumps(conf, separators=(",", ":")) + ";\n" + core + "})();\n")


def sustain_js():
    """the SUS screen: Schwung's canvas overlay (docs/MODULES.md "canvas"): the claimed button's press / release ->
    set_param("s_susbtn"); the screen shows SUS while the notes are held (the button, the latch or CC 64)"""
    return """// SPDX-License-Identifier: GPL-3.0-only
// GENERATED by tools/gen_move_module.py - do not edit.
// Frodhes' PEDAL screen (M1-13b): the claimed button (CC %d) is the sustain pedal, as REC on the FM-1: hold it like
// a pedal, tap to latch, tap again to release. Its press and release go to the plugin as s_susbtn 1 / 0; the
// plugin's own code (the FM-1's) times hold against tap. A MIDI pedal on CC 64 works with it as on the FM-1.
const SUS_CC = %d;
function suspedal(ctx, down) {
    ctx.state.down = down;
    ctx.setParam("s_susbtn", down ? "1" : "0");
}
globalThis.canvas_overlay = {
    onOpen(ctx) { ctx.state.down = false; ctx.state.on = false; },
    onMidi(ctx, msg) {
        const d = (msg && msg.data) || [];
        if (((d[0] | 0) & 0xF0) === 0xB0 && (d[1] | 0) === SUS_CC) { suspedal(ctx, (d[2] | 0) > 0); return true; }
        return false;
    },
    onValues(ctx, p) {
        const v = (p && p.values) || {};
        ctx.state.on = v.s_sustain === "ON" || v.s_sustain === "1";
    },
    onClose(ctx) { if (ctx.state.down) suspedal(ctx, false); },
    draw(ctx) {
        ctx.clear();
        const on = ctx.state.on || ctx.state.down;
        if (on) ctx.fillRect(24, 14, 80, 28, 1);
        ctx.print(52, 24, "SUS", on ? 0 : 1);
        ctx.print(4, 50, "HOLD: PEDAL  TAP: LATCH", 1);
    },
};
""" % (SUS_BTN_CC, SUS_BTN_CC)


def main():
    out, _, _ = outputs()
    if "--check" in sys.argv[1:]:
        bad = []
        for n, t in out.items():
            p = os.path.join(OUT, n)
            if not os.path.exists(p) or open(p).read() != t:
                bad.append(n)
        if bad:
            print("move/module out of date (run tools/gen_move_module.py): " + ", ".join(bad))
            return 1
        return 0
    os.makedirs(OUT, exist_ok=True)
    for n, t in out.items():
        with open(os.path.join(OUT, n), "w") as f:
            f.write(t)
    print("wrote " + ", ".join("move/module/" + n for n in out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
