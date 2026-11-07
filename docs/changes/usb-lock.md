## Fixed

- **USB audio on phones and tablets**: some hosts heard short clicks several times a second while recording the
  FM-1. Its audio clock runs a little fast (about +450 ppm), so some packets carried an extra frame, and these hosts
  click on every size change. Every packet now has its nominal size (44.1 kHz: 44 frames, one in ten 45; 48 kHz: 48
  frames), and a fine sample-rate converter, slowly and smoothly steered, keeps the stream locked to the host's clock
  with nothing audible. 44.1 and 48 kHz.

## Tools

- `tools/hil_cpu.py` shows the measured clock correction (CC 116 query 22) when the firmware answers it.
- `tools/hil_must.py` keeps the whole take, with the levels on USB and on the jack, when S3 fails.
- `tools/gen_uac_fir_lock.py` generates the converter's filter table (`firmware/src/uac_fir_lock.h`); `--check`
  confirms it is current.

## Tested

- Host: `tests/py/test_uac_lock.py`: sines from 20 Hz to 19 kHz at -600 to +600 ppm on both rates (distortion plus
  noise within 1 dB of the previous 48 kHz path), no zipper noise from a correction ramp, 10 minutes of the stream
  model at six drifts with render times up to 5.4 ms (only 44/45 or 48 frame packets, no underrun, overrun or missed
  packet), no start transient, exact zero out for a silent render, and the
  same stream bit for bit when the audio interrupt writes a whole block straight into the converter (less CPU at
  48 kHz).
- Device (FM-1, release build): on a computer the USB counters stay at zero (no missed packet, underrun or overrun)
  and the glitch takes pass at 44.1 and 48 kHz. Worst audio interrupt with every effect on and 10 voices at 48 kHz:
  4452 us (Rhodes 4444, VIBES 4412, MARIMBA 4452), the converter's share 216-220 us; T1, crackle (44.1 and 48 kHz) and
  the CPU runs pass.
