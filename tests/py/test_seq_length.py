# SPDX-License-Identifier: GPL-3.0-only
"""The sequencer records each note as long as it sounded (issue #34): live recording over MIDI notes of known lengths,
then the loop playing on its own, read from MIDI out (the sequencer echoes what it plays; notes received over MIDI are
not echoed, so MIDI out is the playback alone). Each recorded note must play back as long as it was held, within a
tick and a main-loop pass (TOL), whatever QUANT does to its start:

- short, long and overlapping notes; a note held across the loop's end; a note held longer than the loop (it plays the
  whole loop, never the remainder past one loop: the length was taken modulo the loop);
- QUANT 1/16 with every note off the grid: the start snaps, the length stays;
- the sustain pedal (CC 64 and REC held): a note let go under the pedal rings on, and is recorded until the pedal
  lets it go, or until the same note is struck again (the pedal's re-strike), never longer than the loop; a note let go
  before the pedal went down keeps its own length;
- chord mode with STRUM: every chord note ends with its key (a strummed note starts later, so it is shorter by its
  delay).

1 bar at 120 BPM: a 2 s loop of 384 ticks (5.2 ms each). The host's clock is deterministic: the same numbers every run."""
from hostrun import cc, run

LOOP = 2000
T0 = 1000                                       # PLAY: the loop's tick 0
TOL = 12                                        # ms: two ticks and a main-loop pass
Q16 = 125                                       # ms: 1/16 at 120 BPM


def take(tag, notes, quant=0, extra=(), rec_off=None, passes=5):
    """REC armed, PLAY at T0; notes: (pitch, on, off) in ms from T0 over MIDI; extra: more script lines (times from T0);
    REC off at rec_off (from T0); returns the playback after REC off: [(pitch, on, off)] from MIDI out, times from
    the start of the first pass after REC off"""
    rec_off = rec_off if rec_off is not None else max(off for _, _, off in notes) + 100
    first = (rec_off // LOOP + 1) * LOOP
    lines = ["40 btn 11", cc(50, 61, 1), cc(51, 62, quant), cc(60, 63, 127), cc(T0, 60, 127)]
    for n, on, off in notes:
        lines += [f"{T0 + on} midi 0x90 {n} 100", f"{T0 + off} midi 0x80 {n} 0"]
    lines += [f"{T0 + t} {s}" for t, s in extra]
    lines += [cc(T0 + rec_off, 63, 0)]
    r = run(tag, lines, end_ms=T0 + first + passes * LOOP)
    held, got = {}, []
    for t, s, n, v in r.midi_out:
        t -= T0 + first
        if s == 0x90 and v and t >= 0:
            held[n] = t
        elif (s == 0x80 or (s == 0x90 and not v)) and n in held:
            got.append((n, held.pop(n), t))
    return got


def first_of(got, n, k=0):
    """the k-th playback of pitch n in the first pass"""
    hits = [g for g in got if g[0] == n and g[1] < LOOP]
    assert len(hits) > k, f"pitch {n}: {k + 1} notes expected in the first pass, got {hits}"
    return hits[k]


def check(got, n, start, gate, k=0, why=""):
    _, on, off = first_of(got, n, k)
    assert abs(on - start) <= TOL, f"{why} pitch {n}: starts at {on} ms, expected {start}"
    assert abs((off - on) - gate) <= TOL, f"{why} pitch {n}: plays {off - on} ms, held {gate} ms"


PLAIN = [(60, 100, 160),                        # short
         (62, 250, 1150),                       # long
         (64, 500, 900), (65, 700, 1300),       # overlapping
         (67, 1700, 2400),                      # held across the loop's end
         (69, 1200, 3800)]                      # held longer than the loop


def test_lengths_plain():
    got = take("seql_plain", PLAIN)
    for n, on, off in PLAIN[:5]:
        check(got, n, on, off - on, why="QUANT OFF")
    _, on, off = first_of(got, 69)              # longer than the loop: the whole loop (less a tick)
    assert abs(on - 1200) <= TOL and LOOP - 2 * TOL <= off - on <= LOOP, f"held 2600 ms: plays {off - on} ms"


def test_lengths_every_pass():
    """the same lengths on every pass (the note-offs around the loop's end)"""
    got = take("seql_passes", PLAIN[:5], passes=4)
    for n, on, off in PLAIN[:5]:
        hits = [g for g in got if g[0] == n and g[1] < 3 * LOOP]
        assert len(hits) == 3, f"pitch {n}: {hits}"
        for _, a, b in hits:
            assert abs((b - a) - (off - on)) <= TOL, f"pitch {n}: plays {b - a} ms, held {off - on} ms"


def test_lengths_quantized():
    """QUANT 1/16: the starts on the grid, the lengths as held"""
    notes = [(60, 110, 170), (62, 260, 1160), (64, 515, 905), (65, 690, 1310), (67, 1740, 2390)]
    got = take("seql_q16", notes, quant=3)
    for n, on, off in notes:
        check(got, n, (on + Q16 // 2) // Q16 * Q16 % LOOP, off - on, why="QUANT 1/16")


def test_lengths_sustain_cc64():
    """CC 64: 60 let go under the pedal rings to the pedal's release (900 ms); 62 struck twice under it: the first to
    the re-strike, the second to the release; 64 let go before the pedal went down: its own 100 ms; 65 held past the
    release: its key's length"""
    notes = [(64, 0, 100), (60, 300, 400), (62, 450, 500), (62, 800, 850), (65, 1000, 1500)]
    extra = [(200, "midi 0xB0 64 127"), (1200, "midi 0xB0 64 0")]
    got = take("seql_sus", notes, extra=extra)
    check(got, 64, 0, 100, why="before the pedal")
    check(got, 60, 300, 900, why="sustained")
    check(got, 62, 450, 350, why="re-struck under the pedal")
    check(got, 62, 800, 400, k=1, why="re-struck under the pedal")
    check(got, 65, 1000, 500, why="held past the pedal")


def test_lengths_sustain_rec_button():
    """REC held as the sustain (button 13): the same as CC 64"""
    notes = [(60, 300, 400)]
    extra = [(200, "press 13"), (1100, "release 13")]
    got = take("seql_rec", notes, extra=extra, rec_off=1200)
    check(got, 60, 300, 800, why="REC held")


def test_lengths_sustain_longer_than_loop():
    """a pedal held over a whole loop: the note plays the whole loop, never the remainder"""
    notes = [(60, 300, 400)]
    extra = [(200, "midi 0xB0 64 127"), (2900, "midi 0xB0 64 0")]
    got = take("seql_suslong", notes, extra=extra, rec_off=3000)
    _, on, off = first_of(got, 60)
    assert LOOP - 2 * TOL <= off - on <= LOOP, f"sustained 2600 ms: plays {off - on} ms"


def test_lengths_chord_strum():
    """chord mode (MAJ) with STRUM 4 (20 ms a note): C E G, each ending with the key"""
    notes = [(60, 300, 800)]
    extra = [(-500, "midi 0xB0 56 3"), (-490, "midi 0xB0 59 4")]
    got = take("seql_chord", notes, extra=extra)
    for i, n in enumerate((60, 64, 67)):
        check(got, n, 300 + 20 * i, 500 - 20 * i, why="strummed chord")
