## Fixed
- Recording over USB at 48 kHz: a loud, bright passage no longer reaches full scale. The 44.1 to 48 kHz resampler
  could overshoot a passage the limiter kept at its ceiling and clip one sample; above three quarters of full scale
  it now softens the overshoot instead. 44.1 kHz and the headphone jack are unchanged.

## Tested
- Host: tests/py/test_uac_fir.py (the firmware's resampler bit for bit against its reference with the knee; a loud
  9.5-11 kHz passage at the limiter's ceiling clips without it and not with it), Gate R (every render unchanged).
- FM-1: tools/hil_monkey.py at 48 kHz, 25 takes: 0 samples at full scale (the gate now fails on one);
  tools/hil_cpu.py --worst --usb 48000, 3 runs per engine: the whole audio interrupt at most 4016 us.
