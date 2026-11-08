## Changes

- Electric piano decay: held notes in the middle and upper register ring longer, closer to a real tine piano. The
  fundamental's T60 (v40 / v64, host) is now about 19-20 s from C3 to A4 (was 10-11 s), 14 s at C5 (was 8 s),
  4.2 s at A6 (was 2.2 s); C#2-D#2 a little shorter (23-25 s, was 27 s). DECAY still scales every key the same way.

## Tested

- Host: the fundamental's T60 at 31 keys, v40 and v64, against the new curve: within the per-key spread (jitter)
  everywhere. `make test`, the Rhodes and generator pytest files (Gate B: the C engine against the golden model on
  the new tables), Gate R re-saved (only Rhodes renders moved; every VIBES / MARIMBA render byte-identical),
  Gate H re-saved (the HQ Rhodes on the Move uses the same tables).
- `tests/py/test_rhodes_hq.py`: the live-contact check accepts a contact that lands on the neighbouring rate step
  (one case, key 86 v90).
