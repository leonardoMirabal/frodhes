## Tools
- `tools/fm1_install.py`: "FM-1 not found" now suggests naming the port (`--port NAME`). An FM-1 running Frodhes
  (its port is called "Frodhes" since 0.9.5) is found without `--port`; the installer of 0.9.4 and before does not
  know that name, so update with this one.
- The device tools send on MIDI channel 8 by default (`FRODHES_MIDI_CH=1..16` to change it), so they work with the
  FM-1's MIDI CHANNEL on 8 or on ALL.
- `tools/hil_crackle.py` (the USB repeat check of the monkey and the crackle test): 4 or 5 equal frames where the
  music itself turns slowly are no longer counted as a repeated USB packet; the 44.1 kHz monkey takes that failed on
  them now pass, and injected copies are still found.
- `tools/hil_monkey.py`: at 48 kHz one sample at full scale fails the take (`--old-48k` for firmware before this
  one); a passing take with notes that did not sound is kept (`silent-<seed>.npz`) to look at.
- `tools/release.py` never pushes `main` (it is protected): the release goes in through a pull request, then
  `tools/release.py <version> --after-merge` tags it and checks the published files.
- CI: the emulator's job runs in Playwright's own image, the generators run once, and the host tests run in five
  jobs at once (`make pytest SHARD=K/N`, `tools/pytest_shard.py`).
