## Fixed
- The watchdog resets the FM-1 when the sound stops, not only when the whole program stops: it is fed only while the
  audio interrupt runs, the clock moves and (two cores) the second core finishes its jobs. A freeze with the main
  loop still running now ends in a reset after about 8 s instead of hanging until a power cycle. The crash screen's
  wait is bounded too.

## Tested
- Host: `tests/py/test_wd_poll.py` runs the feeding code against a model: with everything alive it feeds for 60 s;
  a stopped audio interrupt stops it after 3 s, a stopped clock after its pass limit, the second core's check at once.
- Not yet tested on the FM-1.
