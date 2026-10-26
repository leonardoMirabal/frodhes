## Fixed
- SMK-37 (GitHub #14): MASTER (K1) is kept across power cycles. It goes into the settings record (an append-only
  field, the record's version unchanged), saved with the other settings 2 s after the last turn and only in silence,
  never once a detent; at power-on it is back before the first sound. A record from before, or one without the
  value, starts at the old level (724 of 1023). The FM-1 is unchanged.

## Tested
- Host: tests/py/test_smk_host.py (turned, saved, switched off and on: the same level; an old record: 724; K1
  turned every 500 ms for 5 s: no flash write while it turns, one settings save 2 s after the last detent),
  tests/smk/encoders.txt (K1's detents reach MASTER), make test-smk, make test; the FM-1 firmware is byte for byte
  the same (sha256 of the fwsc before and after).
