## Fixed
- Turning the volume no longer clicks on the jack (SMK-37 K1 in particular; USB was never affected).

## Tested
- Host: tests/py/test_master_click.py (the platform's MASTER pass on the app's output, single detents and fast spins
  at low, mid and full volume: the largest second difference within 1.5x of the same audio at a steady gain; 0.9.5's
  pass 5-28x).
