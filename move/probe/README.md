# The probe module (development only)

A tiny sound generator for the first device session. It has no Frodhes code. It plays a sine per note (16
voices), and it logs what the host gives a sound generator: every MIDI byte with its source and time, the timing
of every render call, the `state` sizes the host accepts, the host clock calls, and which data folders exist and
keep a file across an update and a reboot. It is installed as two modules: **Frodhes Probe** (the host's MIDI
processing on) and **Frodhes Probe Raw** (`raw_midi`: unprocessed velocities and aftertouch). Two ids also
show whether two module folders get separate globals.

The logs go to `probe-<n>.log` and `probe-<n>-summary.txt` (rewritten every 2 s) in each module folder. Only a
worker thread writes files; the audio callbacks only fill a lock-free ring.

## Build and test (no device)

```
make move-probe        # build/move/probe/frodhes-probe{,-raw}/{dsp.so,module.json}  (Docker, arm64 Debian bookworm)
make test-move-probe   # dlopen both, drive them with a fake host, check the log, the state sizes, no allocation
```

## The device session (about 20 minutes with you)

The agent runs every command. What you do is in **bold**.

1. **Plug the Move into the Mac (USB-C). Open `http://move.local/development/ssh` and add the public key the
   agent shows you.** The agent checks `ssh ableton@move.local` and records the facts (`install.sh --go facts`).
2. **Say yes to installing the probe.** The agent runs `move/probe/install.sh --go install`.
3. **In the shadow UI, load "Frodhes Probe" as the sound generator of slot 1 and "Frodhes Probe Raw" in slot
   2.** The agent checks that both logs start.
4. **Play these, about 2 s apart, and say "done" after each line** (the log's timestamps separate them):
   1. pads: one pad soft, one pad hard, then all 32 pads one by one; hold one pad and press it harder
      (aftertouch);
   2. turn each of the 8 knobs a little, then touch each knob without turning it, then touch the jog wheel;
   3. press once each: Play, Record, Loop, Capture, Undo, Copy, Delete, Mute, the arrows, the 16 step buttons, a
      track button, Shift (the log shows which of them reach a sound generator: the sustain control, M1-13b);
   4. on the "Probe" page: turn **State** to each size, from 1K to 64K, and after each one save a module preset of
      slot 1 (this finds the largest `state` that saves and loads back).
5. **Clock: press Play on the Move, wait 4 bars, Stop, Play again** (the agent reads whether F8/FA/FB/FC reach the
   slot, or only the host's transport calls). Optional: **a USB-A keyboard on the Move: a few notes, the mod wheel,
   bend, the sustain pedal.** (Over Wi-Fi there is no DAW-over-USB part; it comes when a USB port is free: MIDI from
   the Mac on channel 1 and the DAW's clock.)
6. **On slot 1's "Probe" page, turn Burn (knob 2) up slowly while you hold a few pads** (0 to 2500 us of extra
   work per block). **Stop and turn it back to 0 at the first crackle**; the log shows where it was.
7. **Reboot the Move** (or power it off and on) and load the two slots again if the set does not restore them;
   the agent then runs `install.sh --go update` (the module folders replaced, as an update does) and reads which
   marker files survived.
8. The agent pulls every log (`install.sh --go pull`) and removes the probe (`install.sh --go remove`) unless
   you want it kept. **Unload the probe from both slots before an update or a removal.**

What the probe cannot answer: the Move's own engine load (only the time between our callbacks shows it), a real
module update from the store (step 7 simulates one), and buttons that only a module's own UI script receives
(`claims_ccs`): if step 4.3 shows no buttons, the sustain control needs the M6 UI script.
