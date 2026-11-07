## Fixed

- USB audio on phones and tablets: some hosts heard short clicks several times a second while recording the FM-1.
  The FM-1's audio clock runs a little fast (about +450 ppm), so it used to send an extra frame in some packets.
  Most computers accept that; some mobile hosts click on every size change. Now every packet has the nominal size
  (44.1 kHz: nine of 44 frames and one of 45 every 10 ms; 48 kHz: 48 frames every 1 ms). A fine sample-rate
  converter absorbs the clock difference. A slow, smooth control loop keeps the stream locked to the host's clock,
  with no steps, so nothing can be heard. 44.1 and 48 kHz (build flag `FELUCCA_UAC_LOCK`).

## Tools

- `tools/hil_cpu.py`: USB item 22, the measured clock correction (0.1 ppm steps).
- `tools/gen_uac_fir_lock.py`: generates the converter's filter table (`firmware/src/uac_fir_lock.h`); `--check`
  confirms the table is current.

## Tested

- Host: `tests/py/test_uac_lock.py`.
  - Sines from 20 Hz to 19 kHz at -600, +451 and +600 ppm, both rates. Distortion plus noise is within 1 dB of the
    previous 48 kHz path at every frequency.
  - A ramp of the correction adds no zipper noise.
  - The stream model, 10 minutes at each of +451, +600, +176, 0, -400 and -600 ppm, render times up to 5.4 ms.
    Packets are only 44/45 (or 48) frames, with no underruns, overruns or missed packets.
- Device: to be measured (CPU at 48 kHz, recording on a phone or tablet and on a computer).
