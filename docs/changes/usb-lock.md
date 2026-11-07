## Fixed

- USB audio on phones and tablets: some hosts heard short clicks several times a second while recording the FM-1.
  The FM-1's audio clock runs a little fast (about +450 ppm), so it used to send an extra frame in some packets.
  Most computers accept that; some mobile hosts click on every size change. Now every packet has the nominal size
  (44.1 kHz: nine of 44 frames and one of 45 every 10 ms; 48 kHz: 48 frames every 1 ms). A fine sample-rate
  converter absorbs the clock difference. A slow, smooth control loop keeps the stream locked to the host's clock,
  with no steps, so nothing can be heard. 44.1 and 48 kHz (build flag `FELUCCA_UAC_LOCK`).
  The loop starts at the fill it finds and at the drift last measured. The stream follows the FM-1's time on a
  straight line from the first second: within 0.05 sample over 14 s on the host, and within 3 ppm of the drift.

## Tools

- `tools/hil_cpu.py`: the measured clock correction, from its own query (CC 116 = 22), shown when the firmware
  answers it. The full CC 116 reply stays at 55 messages, inside the 64-message MIDI out ring with room to spare
  (`tests/py/test_usb_health_reply.py`). The locked stream leaves out items 4 and 5: its packets never change size.
- `tools/hil_must.py`: when S3 fails, it keeps the whole take, with the level left on USB and on the jack.
- `tools/gen_uac_fir_lock.py`: generates the converter's filter table (`firmware/src/uac_fir_lock.h`); `--check`
  confirms the table is current.

## Tested

- Host: `tests/py/test_uac_lock.py`.
  - Sines from 20 Hz to 19 kHz at -600, +451 and +600 ppm, both rates. Distortion plus noise is within 1 dB of the
    previous 48 kHz path at every frequency.
  - A ramp of the correction adds no zipper noise.
  - The stream model, 10 minutes at each of +451, +600, +176, 0, -400 and -600 ppm, render times up to 5.4 ms.
    Packets are only 44/45 (or 48) frames, with no underruns, overruns or missed packets.
  - `test_start_no_transient`: the first 14 s after a start stay within 10 ppm of the drift and within 0.25 sample
    of a straight line (before this fix: a swing to the clamp, -977 then +802 ppm, 26 samples off the line).
  - `test_stream_exact_zero_for_zero_input`: with a silent render, every frame the host takes is exact zero.
- Device: to be measured (CPU at 48 kHz, recording on a phone or tablet and on a computer).
