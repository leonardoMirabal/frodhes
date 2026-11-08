## Changes

- Electric piano decay: held notes in the middle and upper register ring longer, closer to a real tine piano. The
  fundamental's T60 (v40 / v64, host) is now about 19-20 s from C3 to A4 (was 10-11 s), 14 s at C5 (was 8 s),
  4.2 s at A6 (was 2.2 s); C#2-D#2 a little shorter (23-25 s, was 27 s). DECAY still scales every key the same way.
- Electric piano damper: letting go of a key stops the note faster and sooner. The release T60 is 0.25 s in the bass,
  0.18 s in the middle, 0.14 s and 0.12 s in the treble (was 0.5 / 0.4 / 0.3 / 0.2 s), and the felt lands within a few
  ms (its settle is 2.2-5.1 ms by register, was 3.6-7.3 ms): a note falls 10 dB in 25-45 ms (was 55-95 ms). RELEASE
  still scales it x0.25 to x4 (127: about 1 s in the bass, 0.5 s in the treble). Release velocity, the pedal and the
  half pedal work as before, on the new times.

## Tested

- Host: the fundamental's T60 at 31 keys, v40 and v64, against the new curve: within the per-key spread (jitter)
  everywhere. `make test`, the Rhodes and generator pytest files (Gate B: the C engine against the golden model on
  the new tables), Gate R re-saved (only Rhodes renders moved; every VIBES / MARIMBA render byte-identical),
  Gate H re-saved (the HQ Rhodes on the Move uses the same tables).
- Host: the fundamental after a 1 s hold at 9 keys, v40 and v110: release T60 on target (0.250 / 0.181 / 0.141 /
  0.120 s), damping starts 1-5 ms after the key-up, no burst at the key-up, never louder after the key-up (also
  `tests/py/test_release_never_louder.py` on every engine). `tests/scripts/release_vel.txt` checks 100 ms after the
  key-up (was 200 ms, now near silence) and `seq.txt` just after the loop's note; the golden model (Gate B) uses the
  same per-register settle as the engine.
- `tests/py/test_rhodes_hq.py`: the live-contact check accepts a contact that lands on the neighbouring rate step
  (one case, key 86 v90).
