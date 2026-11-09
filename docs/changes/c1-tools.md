## Tools
- `tools/fm1_install.py` and `tools/hil_c1_soak.py --location`: pick one FM-1 by its USB location when two are
  connected; the USB audio device is found by location too, and two FM-1 audio devices without one are refused.
- `tools/hil_c1_soak.py --no-audio` (MIDI only); `tools/hil_c1_burn.py` finds the two-core load limit by adding a
  set amount of work to either core (development builds, CC 117 / 118).
