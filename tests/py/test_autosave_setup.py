# SPDX-License-Identifier: GPL-3.0-only
"""Auto save keeps the system setup only (0.9.5; the user, 2026-10-25: "if I'm performing I always want to get back
to the original state"). The settings record (params.c settings_record, frodhes.c settings_poll) is written ~2 s
after a SETUP change (the MIDI page, CLOCK, KEY VEL, TOUCH, TUNE, USB VOL, the DISPLAY page, ASSIGN, the preset
number ...), once silent. The SOUND (every knob, effect and engine parameter) is never auto-saved: after a power
cycle the FM-1 plays the preset last loaded or saved exactly as it is stored. The tempo comes back as last set by
hand (TAP, the TEMPO page), never as an external MIDI clock left it; the clock writes nothing."""
import struct

from hostrun import cc, run

import test_persistence as tp

GO_MIDI = ["50 btn 7", "60 btn 7", "70 btn 7"]           # GLO three times: TEMPO, SETUP, MIDI
KNOBS = [cc(100 + 300 * i, n, v) for i, (n, v) in enumerate(
    [(81, 90), (91, 40), (74, 20), (88, 127), (79, 0), (80, 64), (73, 100), (93, 70), (95, 80), (85, 30)])]


def pvals(s):
    return {k: v for k, v in s.items() if k.startswith("p.")}


def test_knob_changes_write_nothing():
    """ten sound changes (CCs) and knob turns on PERFORM, a preset-less tweak session of 12 s: no settings write,
    and the setup's "dirty" never comes up"""
    lines = KNOBS + [f"{3500 + 100 * i} turn {2 + i % 4} {1 if i % 2 else -3}" for i in range(16)]
    lines += ["50 dump", "5000 dump", "12000 dump"]
    r = run("as-knobs", lines, 12500, flash=tp.flash_file("as-knobs"))
    assert not r.saves, r.saves
    assert r.state(5000)["dirty"] == "0" and r.state(12000)["dirty"] == "0"
    assert pvals(r.state(12000)) != pvals(r.state(50))             # (the knobs did move the sound)


def test_setup_change_saves_2s_later():
    """the MIDI page's CHANNEL turned (K1): the record is written 2 s later, holding it; a knob turned meanwhile
    neither delays it nor ends up in the record"""
    fl = tp.flash_file("as-setup")
    r = run("as-setup", GO_MIDI + ["500 turn 2 1", cc(1500, 81, 99), "600 dump"], 4000, flash=fl)
    assert r.state(600)["dirty"] == "1" and r.state(600)["card0"] == "1"
    assert len(r.saves) == 1 and 2500 <= r.saves[0] <= 2650, r.saves
    rec = tp.settings_record(tp.read_img(fl))
    assert rec[7] == 1, rec[7]                                       # settings_t midi_ch
    f01 = run("as-f01", ["100 dump"], 200).state(100)
    assert rec[tp.O_PANEL + 1] == int(f01["p.1"]), (rec[tp.O_PANEL + 1], f01["p.1"])   # DRIVE: F01's, not 99


def test_power_cycle_brings_back_the_loaded_preset_and_the_setup():
    """load F05, tweak it hard (ten CCs), change the setup on its pages (MIDI CHANNEL 3, COLOR, TOUCH HEAVY), let it
    save, switch off: on again F05 plays exactly as stored (the whole parameter array
    equals a fresh load of F05), the setup is all back"""
    ref = run("as-ref", ["100 midi 0xC0 4 0", "700 dump"], 800).state(700)
    fl = tp.flash_file("as-pc")
    lines = ["100 midi 0xC0 4 0"] + [l.replace(l.split()[0], str(int(l.split()[0]) + 600), 1) for l in KNOBS]
    lines += ["4000 btn 7", "4050 btn 7", "4100 btn 7", "4200 turn 2 1", "4250 turn 2 1", "4300 turn 2 1",   # MIDI CH 3
              "4400 btn 7", "4500 turn 2 1",                         # DISPLAY: COLOR one step
              "4600 btn 7", "4700 btn 7", "4800 turn 5 1",           # TEMPO, SETUP: TOUCH HEAVY
              "8000 dump"]
    r1 = run("as-pc-1", lines, 9000, flash=fl)
    s1 = r1.state(8000)
    assert r1.saves and pvals(s1) != pvals(ref)                      # (tweaked)
    r2 = tp.boot("as-pc-2", fl, end=400)
    assert not r2.fails, r2.fails
    s2 = r2.state(100)
    assert pvals(s2) == pvals(ref), {k: (ref[k], s2[k]) for k in pvals(ref) if s2[k] != ref[k]}
    assert s2["header"] == ref["header"] and s2["preset"] == "4", (s2["header"], s2["preset"])
    for k in ("card.18.0", "card.21.0", "card.17.3"):                # CHANNEL, COLOR, TOUCH
        assert s2[k] == s1[k], (k, s1[k], s2[k])
    assert (s2["card.18.0"], s2["card.17.3"]) == ("3", "HEAVY")


def clock(t0, t1, bpm):
    """MIDI clock pulses (24 a beat) from t0 to t1 ms"""
    out, t, step = [], float(t0), 60000.0 / bpm / 24.0
    while t < t1:
        out.append(f"{int(t)} midi 0xF8 0 0")
        t += step
    return out


def test_external_clock_writes_nothing_and_the_hand_tempo_comes_back():
    """CLOCK EXT (the default): 60 s of MIDI clock, 100 BPM then 140 BPM, with a Start and a Stop: not one settings
    write. A tempo set by hand before (TEMPO page BPM 108) is saved; after the clock and a power cycle the tempo is
    that 108, not the clock's 140"""
    fl = tp.flash_file("as-clock")
    lines = ["50 btn 7", "100 turn 2 -12", "200 dump"]               # TEMPO: BPM 108, saved ~2 s later
    lines += ["4000 midi 0xFA 0 0"] + clock(4000, 34000, 100) + clock(34000, 64000, 140) + ["64000 midi 0xFC 0 0"]
    lines += ["63000 dump", "66000 dump"]
    r = run("as-clock", lines, 68000, flash=fl)
    assert r.state(200)["bpm"] == "108.0"
    assert len(r.saves) == 1 and r.saves[0] < 4000, r.saves
    assert r.state(63000)["bpm"].startswith("14"), r.state(63000)["bpm"]   # (it did follow the clock)
    assert r.state(66000)["dirty"] == "0"
    s = tp.boot("as-clock-2", fl, end=300).state(100)
    assert s["bpm"] == "108.0", s["bpm"]
    assert struct.unpack_from("<H", tp.settings_record(tp.read_img(fl)), tp.O_BPM)[0] == 1080


def test_tap_tempo_is_kept():
    """TAP (PLAY, three taps 500 ms apart: 120 BPM from 108): a tempo set by hand, saved and back after a power cycle"""
    fl = tp.flash_file("as-tap")
    run("as-tap-1", ["50 btn 7", "100 turn 2 -12", "3000 btn 12", "3500 btn 12", "4000 btn 12", "4100 dump"], 7000,
        flash=fl)
    s = tp.boot("as-tap-2", fl, end=300).state(100)
    assert s["bpm"] == "120.0", s["bpm"]


def test_save_and_undo_set_what_comes_back():
    """SAVE to U01 after a tweak: the saved sound comes back; a tweak after the save does not"""
    fl = tp.flash_file("as-save")
    lines = [cc(100, 81, 77), "200 btn 9", "300 btn 9", cc(3000, 81, 12), "6000 dump"]
    r1 = run("as-save-1", lines, 6500, flash=fl)
    assert r1.state(6000)["p.1"] == "12" and r1.bank_saves
    s = tp.boot("as-save-2", fl, end=300).state(100)
    assert s["header"].startswith("U01") and s["p.1"] == "77", (s["header"], s["p.1"])


def test_no_settings_write_while_a_clock_runs():
    """a setup change (MIDI CHANNEL) made while a 120 BPM MIDI clock runs (CLOCK EXT): no settings write while the
    pulses come (frodhes.c settings_poll: tempo_flash_ok(), an erase would hold the pulses back); written once, just
    after the clock stops (250 ms without a pulse). Without tempo_flash_ok it writes 2 s after the change, mid-clock"""
    beat = 500.0 / 24                                               # ms a pulse at 120 BPM
    ps = [f"{int(200 + i * beat)} midi 0xF8 0 0" for i in range(24 * 16)]   # 8 s of clock
    last = int(200 + (24 * 16 - 1) * beat)
    fl = tp.flash_file("as-clock")
    r = run("as-clock", ps + GO_MIDI + ["500 turn 2 1", "600 dump"], last + 3000, flash=fl)
    assert r.state(600)["dirty"] == "1", r.state(600)["dirty"]
    assert len(r.saves) == 1 and last + 250 <= r.saves[0] <= last + 600, (r.saves, last)
    assert tp.settings_record(tp.read_img(fl))[7] == 1                # settings_t midi_ch: the change, kept
