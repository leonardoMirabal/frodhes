#!/usr/bin/env python3
"""v3 review scratch: tempo arithmetic in 24 MHz ticks and 32-bit integers (P6). Verifies the constants and the
overflow bounds used in IMPLEMENTATION-v3 section 9."""
TICK = 24_000_000
FS = 44100
# ticks -> samples: ((uint64)t * K) >> 32
K = round(FS / TICK * 2 ** 32)
err = max(abs(((t * K) >> 32) - round(t * FS / TICK)) for t in (1, 1000, 139320, 2 ** 24, 2 ** 31, 2 ** 32 - 1))
print("ticks->samples constant %d, max error %d sample(s) up to 2^32 ticks (rel %.1e)" % (K, err, K / 2 ** 32 / (FS / TICK) - 1))
# block period in ticks
print("256 frames = %.1f ticks" % (256 * TICK / FS))
# pulse (24 PPQN) in ticks: 6e8 / bpm10
for bpm10 in (400, 1200, 2400):
    q, r = divmod(600_000_000, bpm10)
    print("bpm10 %d: pulse %d ticks rem %d; beat_samples %d (26460000/bpm10); x768 units = %d (< 2^31: %s)" %
          (bpm10, q, r, 26_460_000 // bpm10, 26_460_000 // bpm10 * 768, 26_460_000 // bpm10 * 768 < 2 ** 31))
# tap tempo in 0.1 ms: span_t = ticks / 2400 (32-bit divide), bpm10 = 6e6 (n-1) / span_t
for ms in (250, 1500, 2000 * 3):
    span_t = ms * 10; n = 4
    print("tap span %d ms (3 intervals): bpm10 = %d; resolution %.3f BPM" % (ms, 6_000_000 * 3 // span_t, 6_000_000 * 3 / span_t ** 2 / 10 * 10))
print("max 6e6*(n-1) = %d < 2^32: %s; max span ticks 3 x 2 s = %d < 2^32: %s" % (6_000_000 * 3, 6_000_000 * 3 < 2 ** 32, 6 * TICK, 6 * TICK < 2 ** 32))
# LFO sync inc = 0xFFFFFFFF / samples ; drift per cycle
for samples in (2756, 66150 * 8):
    inc = 0xFFFFFFFF // samples
    print("sync %d samples: inc %d, cycle error %.2e cycles" % (samples, inc, (inc * samples - 2 ** 32) / 2 ** 32))
# MIDI clock: 24 pulses per beat at 40..240 BPM -> pulse 62.5..10.4 ms
print("pulse interval ms at 40/240 BPM: %.1f / %.1f ; accept window 8..80 ms" % (60000 / 40 / 24, 60000 / 240 / 24))
