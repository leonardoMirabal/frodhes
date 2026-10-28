## Fixed

- DELAY with the chorus, the phaser or the tremolo's pan on: the echoes' level no longer flickers. The send keeps the stereo power when those effects make left and right differ, but it measured the difference only in whole steps, so its make-up jumped between none and +3 dB from one 5.8 ms block to the next (since 0.9.3). It now follows the measured difference smoothly; with left and right equal the send is unchanged.

## Tested

- Host: `tests/py/test_dly_send.py` (with the chorus, the phaser and the pan, under a held chord: the send's make-up takes hundreds of values, not two, and its block-to-block steps stay under 0.22 dB of spread; it fails without the fix; with left and right equal the make-up stays exactly 1), Gate R re-saved (the moved renders listed in the commit).
