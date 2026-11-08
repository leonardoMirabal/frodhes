#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The desktop plugin's parameter map (plugin/gen/params_map.h) from the app's own table.

The ids, ranges, defaults and CCs are PARAMS[] in firmware/src/params.c, read through the app itself
(plugin/tests/param_dump.c, built here with the plugin's engine flags): the enum ids are append-only, so they are the
plugin's stable parameter ids (CLAP ids; clap-wrapper derives the VST3 / AU ids from them). The value texts are the
FM-1's own (params.c param_text, the enums' full names), with the unit spelled out for a host's generic view. What
is typed in here is only what a host shows and the panel does not: the long names and the groups (LONG below); a new
parameter in params.c without one fails the generator, and tests/py/test_plugin_params.py fails until it is added.

The plugin's own parameters (not the app's) have ids from 0x10000 (append-only too): ENGINE, the sound path (CLASSIC,
the FM-1's integer engine and voice count, or HQ, the Move's float paths and 16 voices; HQ is the default); TUNE (the
app's setting, -50..+50 cents); PRESET (the factory presets F01..F57: a host's generic view can browse them); SUSTAIN
(the latch, as a CC 64) and FX BYPASS (the FX button).

  tools/gen_plugin_params.py            write plugin/gen/params_map.h
  tools/gen_plugin_params.py --check    exit 1 if the committed file is not what the sources give
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "plugin", "gen", "params_map.h")
BUILD = os.path.join(ROOT, "build", "plugin", "gen")
# the plugin's engine flags (plugin/cmake/slots.cmake FRP_ENGINE_DEFS; the Move's: 16 voices, the HQ paths)
DEFS = ["-DFR_VOICES=16u", "-DFR_HQ=1", "-DFR_BYP_KEEP_CMP=1", "-DFR_REV_HQ=1"]

# id name -> (long name, group). The VOICE parameters mean other things on the bar engine (eng_pages.h ENG_BARS_C):
# both names, the Rhodes' first.
LONG = {
    "P_TYPE": ("Type", "Voice"),
    "P_DRIVE": ("Drive amount", "Drive"),
    "P_TW_SW": ("Tremolo / wah", "Tremolo"),
    "P_TW_DEPTH": ("Tremolo depth", "Tremolo"),
    "P_TW_RATE": ("Tremolo rate", "Tremolo"),
    "P_TW_SYNC": ("Tremolo sync", "Tremolo"),
    "P_TW_MODE": ("Tremolo mode", "Tremolo"),
    "P_TW_SHAPE": ("Tremolo shape", "Tremolo"),
    "P_CP_SW": ("Chorus", "Chorus"),
    "P_CP_DEPTH": ("Chorus depth", "Chorus"),
    "P_CP_SPEED": ("Chorus speed", "Chorus"),
    "P_CP_SYNC": ("Chorus sync", "Chorus"),
    "P_CP_FDBK": ("Chorus feedback", "Chorus"),
    "P_DL_SW": ("Delay type", "Delay"),
    "P_DL_DEPTH": ("Delay mix", "Delay"),
    "P_DL_TIME": ("Delay time", "Delay"),
    "P_DL_SYNC": ("Delay sync", "Delay"),
    "P_DL_FDBK": ("Delay feedback", "Delay"),
    "P_DL_TONE": ("Delay tone", "Delay"),
    "P_RV_DEPTH": ("Reverb mix", "Reverb"),
    "P_TONE": ("Tone / strike", "Voice"),
    "P_BARK": ("Bark / mallet", "Voice"),
    "P_BELL": ("Bell / tube", "Voice"),
    "P_HAMMER": ("Hammer / weight", "Voice"),
    "P_DECAY": ("Decay / ring", "Voice"),
    "P_RELEASE": ("Release / damper", "Voice"),
    "P_NOISE": ("Noise", "Voice"),
    "P_SPREAD": ("Stereo spread", "Voice"),
    "P_PH_SW": ("Phaser", "Phaser"),
    "P_PH_DEPTH": ("Phaser depth", "Phaser"),
    "P_PH_SPEED": ("Phaser speed", "Phaser"),
    "P_PH_SYNC": ("Phaser sync", "Phaser"),
    "P_PH_FDBK": ("Phaser feedback", "Phaser"),
    "P_CMP_SW": ("Compressor", "Compressor"),
    "P_CMP_AMT": ("Compressor threshold", "Compressor"),
    "P_CMP_ATK": ("Compressor attack", "Compressor"),
    "P_CMP_REL": ("Compressor release", "Compressor"),
    "P_LEVEL": ("Level", "Voice"),
    "P_CMP_RATIO": ("Compressor ratio", "Compressor"),
    "P_CMP_GAIN": ("Compressor make-up", "Compressor"),
    "P_FL_TYPE": ("Filter type", "Filter"),
    "P_FL_FREQ": ("Filter cutoff", "Filter"),
    "P_FL_RESO": ("Filter resonance", "Filter"),
    "P_FL_LFO": ("Filter LFO amount", "Filter"),
    "P_LFO_SHAPE": ("Filter LFO shape", "Filter"),
    "P_LFO_RATE": ("Filter LFO rate", "Filter"),
    "P_LFO_SYNC": ("Filter LFO sync", "Filter"),
    "P_LFO_RETRIG": ("Filter LFO retrigger", "Filter"),
    "P_DRV_TONE": ("Drive tone", "Drive"),
    "P_DRV_MIX": ("Drive mix", "Drive"),
    "P_DRV_SW": ("Drive type", "Drive"),
    "P_RV_DECAY": ("Reverb decay", "Reverb"),
    "P_RV_TONE": ("Reverb tone", "Reverb"),
    "P_RV_PREDLY": ("Reverb pre-delay", "Reverb"),
    "P_MOT_ON": ("Motor", "Motor"),
    "P_MOT_DEPTH": ("Motor depth", "Motor"),
    "P_MOT_RATE": ("Motor speed", "Motor"),
    "P_MOT_SHAPE": ("Motor shape", "Motor"),
    "P_MOT_SYNC": ("Motor sync", "Motor"),
    "P_RV_TYPE": ("Reverb type", "Reverb"),
    "P_RV_SIZE": ("Reverb size", "Reverb"),
    "P_RV_MOD": ("Reverb modulation", "Reverb"),
    "P_RV_LOCUT": ("Reverb low cut", "Reverb"),
    "P_EQ_LO": ("EQ low", "EQ"),
    "P_EQ_MID": ("EQ mid", "EQ"),
    "P_EQ_FREQ": ("EQ mid frequency", "EQ"),
    "P_EQ_HI": ("EQ high", "EQ"),
}
# the formats (params.c F_*), in its enum's order
FMTS = ["F_R10", "F_ENUM", "F_HZ", "F_MS", "F_DIV", "F_BIP", "F_DB", "F_THR", "F_GAIN", "F_FHZ", "F_HZH", "F_PDLY",
        "F_RSIZE", "F_LCUT", "F_EQDB", "F_EQHZ"]
# the plugin's own parameters: (id, C name, long name, group, values, default)
# (the PRESET texts come from the app: dump()'s factory names)
OWN = [(0x10000, "FRP_ID_ENGINE", "Engine", "Engine", ["CLASSIC", "HQ"], 1),
       (0x10001, "FRP_ID_TUNE", "Tune", "Voice", [f"{c:+d} c" if c else "0 c" for c in range(-50, 51)], 50),
       (0x10002, "FRP_ID_PRESET", "Preset", "Preset", None, 0),
       (0x10003, "FRP_ID_SUSTAIN", "Sustain", "Performance", ["OFF", "ON"], 0),
       (0x10004, "FRP_ID_FX_BYPASS", "Effects bypass", "Performance", ["OFF", "ON"], 0)]


def param_enum():
    """the P_* names in id order, from params.c's enum"""
    src = open(os.path.join(ROOT, "firmware", "src", "params.c")).read()
    body = src.split("enum { P_TYPE", 1)[1].split("P_COUNT };", 1)[0]
    names = ["P_TYPE"]
    for ln in body.splitlines():
        ln = ln.split("/*", 1)[0]
        names += [w.strip() for w in ln.split(",") if w.strip().startswith("P_")]
    return names


def dump():
    os.makedirs(BUILD, exist_ok=True)
    exe = os.path.join(BUILD, "param_dump")
    tmp = f"{exe}.{os.getpid()}"
    subprocess.run([os.environ.get("CC", "cc"), "-O1", "-w", "-DDINGHY_HOST", '-DAPP_VER="dev"', *DEFS,
                    "-I" + os.path.join(ROOT, "firmware", "src"), "-I" + os.path.join(ROOT, "firmware", "gen"),
                    "-o", tmp, os.path.join(ROOT, "plugin", "tests", "param_dump.c"), "-lm"], check=True)
    os.replace(tmp, exe)
    out = subprocess.run([exe], check=True, capture_output=True, text=True).stdout
    n, params, text, fact = 0, [], {}, []
    for ln in out.splitlines():
        p = ln.split(" ", 3)
        if p[0] == "N":
            n = int(p[1])
        elif p[0] == "P":
            q = ln.split()
            params.append(dict(id=int(q[1]), lab=q[2], hi=int(q[3]), deff=int(q[4]), fmt=int(q[5]), cc=int(q[6])))
        elif p[0] == "T":
            text.setdefault(int(p[1]), []).append(p[3] if len(p) > 3 else "")
        elif p[0] == "F":
            q = ln.split(" ", 2)
            assert int(q[1]) == len(fact)
            fact.append(f"F{len(fact) + 1:02d} {q[2] if len(q) > 2 else ''}".strip())
    assert n == len(params) and all(len(text[d["id"]]) == d["hi"] + 1 for d in params) and len(fact) == 57
    return params, text, fact


def with_unit(fmt, t):
    """the panel's text with its unit for a host ("4.2K" -> "4.2 kHz", "1.5S" -> "1.5 s", "-20" -> "-20 dB")"""
    f = FMTS[fmt]
    if f in ("F_HZ", "F_HZH"):
        return t + " Hz"
    if f in ("F_FHZ", "F_EQHZ", "F_LCUT"):
        if t == "OFF":
            return t
        return t[:-1] + " kHz" if t.endswith("K") else t + " Hz"
    if f == "F_MS":
        return t[:-1] + " s" if t.endswith("S") else t + " ms"
    if f == "F_PDLY":
        return t + " ms"
    if f in ("F_DB", "F_GAIN", "F_EQDB", "F_THR"):
        return t + " dB"
    return t


def c_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render():
    names = param_enum()
    params, text, fact = dump()
    if len(names) != len(params):
        raise SystemExit(f"gen_plugin_params: params.c's enum has {len(names)} ids, PARAMS[] {len(params)}")
    missing = [n for n in names if n not in LONG]
    if missing:
        raise SystemExit("gen_plugin_params: no long name / group for " + ", ".join(missing) +
                         " (add it to LONG in tools/gen_plugin_params.py)")
    stale = [n for n in LONG if n not in names]
    if stale:
        raise SystemExit("gen_plugin_params: LONG names ids params.c does not have: " + ", ".join(stale))
    rows, texts = [], []
    for d, nm in zip(params, names):
        lng, grp = LONG[nm]
        t0 = len(texts)
        texts += [with_unit(d["fmt"], t) for t in text[d["id"]]]
        stepped = FMTS[d["fmt"]] in ("F_ENUM", "F_DIV")
        rows.append(f"    {{{d['id']}u, {c_str(lng)}, {c_str(grp)}, {d['hi']}u, {d['deff']}u, {d['cc']}u, "
                    f"{1 if stepped else 0}u, {t0}u}},   /* {nm} ({d['lab']}) */")
    own = []
    for oid, cname, lng, grp, vals, deff in OWN:
        vals = vals or fact
        t0 = len(texts)
        texts += vals
        enum = 0 if cname == "FRP_ID_TUNE" else 1     # (TUNE is an amount, the others are choices)
        own.append(f"    {{{oid:#x}u, {c_str(lng)}, {c_str(grp)}, {len(vals) - 1}u, {deff}u, 0u, {enum}u, {t0}u}},   /* {cname} */")
    lines = [
        "/* SPDX-License-Identifier: GPL-3.0-only */",
        "/* generated by tools/gen_plugin_params.py from firmware/src/params.c (through plugin/tests/param_dump.c): do",
        " * not edit. The plugin's parameters: the app's PARAMS[] (id = the params.c enum id, append-only: the stable",
        " * CLAP id), then the plugin's own (ids from 0x10000). Values are the app's 0..hi; text = FRP_TEXT[text0 + v]. */",
        "#ifndef FRODHES_PLUGIN_PARAMS_MAP_H",
        "#define FRODHES_PLUGIN_PARAMS_MAP_H",
        "#include <stdint.h>",
        "",
        "typedef struct {",
        "    uint32_t id;",
        "    const char *name, *module;   /* the host's name and group */",
        "    uint8_t hi, def, cc, stepped; /* 0..hi; the default; the FM-1's MIDI CC (0: none); an enum */",
        "    uint16_t text0;              /* the first of its hi + 1 texts in FRP_TEXT */",
        "} frp_param_t;",
        "",
        f"#define FRP_NPARAM_APP {len(params)}u   /* params.c P_COUNT */",
        f"#define FRP_NPARAM {len(params) + len(OWN)}u",
        *(f"#define {cname} {oid:#x}u" for oid, cname, *_ in OWN),
        f"#define FRP_NOWN {len(OWN)}u",
        "#define FRP_ENGINE_CLASSIC 0u",
        "#define FRP_ENGINE_HQ 1u",
        "",
        "static const frp_param_t FRP_PARAMS[FRP_NPARAM] = {",
        *rows,
        *own,
        "};",
        "",
        f"static const char *const FRP_TEXT[{len(texts)}] = {{",
    ]
    for i in range(0, len(texts), 8):
        lines.append("    " + " ".join(c_str(t) + "," for t in texts[i:i + 8]))
    lines += ["};", "#endif", ""]
    return "\n".join(lines)


def main():
    out = render()
    if "--check" in sys.argv[1:]:
        cur = open(OUT).read() if os.path.exists(OUT) else ""
        if cur != out:
            print(f"gen_plugin_params --check: {os.path.relpath(OUT, ROOT)} is not what params.c gives "
                  "(run tools/gen_plugin_params.py)")
            return 1
        print(f"gen_plugin_params --check: {os.path.relpath(OUT, ROOT)} is current")
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(out)
    print(f"gen_plugin_params: wrote {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
