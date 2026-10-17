# Frodhes on the M-Vave SMK-37

Frodhes also builds for the M-Vave SMK-37, a 37-key controller with the same chip as the FM-1. The FM-1 build is
unchanged; everything for the SMK-37 is behind `BOARD_SMK37`. On the SMK-37 it is the same electric piano: the same
engine, instrument types, effects, pages and screen, with 37 velocity keys, 16 pads, 4 faders, two wheels and the
pedal jack on top.

## Supported models

- **SMK-37 Pro**: the default build (package identity `SMK-37 Pro_470`, the same for every version, as the FM-1's
  `FM-1_958`).
- **SMK-37 Elite**: `MODEL=elite` (identity `SMK-37 Elite_470`).
- **MKE-P37**: `MODEL=mke` (identity `MKE-P37_470`).
- **Starrykey 37 Play**: uses the default build.

All of them are the same hardware. Each build carries its model's name, so M-Vave's updater still takes that unit's
stock package later: install the package for your model.

## Controls

The FM-1's controls keep their Frodhes meaning, so the README's page table and the manual apply; the screen keeps the
FM-1's names for the pages and cards. The SMK-37 adds the rest.

| SMK-37 | Frodhes |
| --- | --- |
| Keys F3..G5 (the middle 27) | the FM-1's 27 keys |
| Keys C3..E3 and G#5..C6 (the outer 10) | the notes below and above them: all 37 keys play, C3..C6 at OCTAVE 0, through the scale and chord modes |
| Velocity | the hammer's: each key strikes at its own velocity (the unit's factory curve), so soft notes are mellow and hard ones bark. VELOCITY (the main page's K4): NORMAL, SOFT (light playing louder), HARD (it takes more), FIXED (KEY VEL 96, as the FM-1). The notes go out over USB-MIDI with their velocity |
| Pads 1-4 (top row) | the TYPE: STAGE 77, SUITCASE, EARLY FELT, BELL DYNO |
| Pads 5 / 6 | the previous / next preset (as PRESETS) |
| Pads 7 / 8 | DRIVE on / off (its amount, remembered), CHORD mode on / off |
| Pads 9-14 (bottom row) | COMP, TREM/WAH, PHASER, CHORUS, DELAY, REVERB on / off (as the effect buttons on their pages; REVERB: its amount, remembered) |
| Pad 15 | the arpeggiator on / off |
| Pad 16 | SUSTAIN: held, the dampers stay off; a tap latches, a second tap lets go (as REC) |
| Pad lights | off dark orange, on light blue, active bright: the TYPE and the effects that are on light up bright while notes play |
| K1..K4 | KNOB 1..4 (the page's four cards) |
| K5 | MASTER (volume) |
| K6 / K7 / K8 | SELECT (the pages) / ALGORITHM (the TYPE) / PRESETS (the sounds) |
| PATCH | HOME: PERFORM; again, the main page |
| PARA | EDIT: VOICE |
| NOTE REPEAT / CHORD / SCALE | LFO / ENV / SEL: TREM/WAH, PHASER, DELAY (on their page: on / off) |
| GLOBE | GLO: TEMPO, SETUP, MIDI |
| FX | all effects off / on |
| ARP / SEQ | ARP / SEQ |
| SEQ PLAY | PLAY: tap tempo (on SEQUENCER: start / stop; on ARPEGGIO: HOLD) |
| SEQ REC | REC: the sustain pedal (hold, or tap to latch) |
| BT | SAVE |
| OCTAVE- / OCTAVE+ | OCT- / OCT+ |
| < / > | the previous / next page |
| FADER BANK | what the faders set: A the piano (TONE, BARK, PHASER, COMP THRESH), B the effects (DRIVE, CHORUS, DELAY, REVERB). Lit on bank B |
| Faders 1..4 | the bank's four values; a fader takes over only once it moves (also after a bank change). An effect's amount up from 0 switches it on, COMP THRESH at 0 is off. The footer says the value |
| Pitch wheel | bends +-2 semitones: every tine that sounds is retuned |
| Mod wheel | the TREM/WAH depth (switched on as TREM when it moves) |
| Pedal jack | a sustain pedal, either polarity: its position at power-on is "up", so plug it in before switching on. Not yet tried with a pedal |
| KNOB BANK held | the panel check: the flash part, the calibration, the ADC, faders, wheels, pedal, buttons, keys and pads |
| OCTAVE- held at power-on | the USB rescue (as OCT- on the FM-1) |

The SMK-37 has no key lights; the pads light instead. Its TRS jack is a MIDI output and this build does not use it;
there is no TRS MIDI IN, so MIDI comes in over USB only (the MIDI page has no TRS IN card).

The engine keeps the FM-1's 8 voices. The audio block's time is on the SETUP page (AUDIO / MAX) and
`tools/hil_cpu.py` reads it over USB-MIDI; above 5.0 ms the quietest released voice fades out, as on the FM-1.

## Build

See the README for the toolchain.

```sh
BOARD=smk37 ./build.sh                 # build/frodhes-smk37.fwsc (SMK-37 Pro, Starrykey 37 Play)
BOARD=smk37 MODEL=elite ./build.sh     # build/frodhes-smk37-elite.fwsc
BOARD=smk37 MODEL=mke ./build.sh       # build/frodhes-smk37-mke.fwsc
./build.sh                             # the FM-1 build again before tests/run_tests.sh (the tests use its files)
sh tests/run_tests.sh                  # the SMK-37 host tests: the mapping (tests/smk/*.txt), flash map, update path
BOARD=smk37 tests/run_host.sh          # Frodhes for the SMK-37 on this computer: build/host/frodhes-smk37.wav, screens
```

## Install

Connect the keyboard by USB and run, with the package for your model:

```sh
python3 tools/fm1_install.py build/frodhes-smk37.fwsc
```

`fm1_install.py` needs `mido` and `python-rtmidi` (`pip3 install mido python-rtmidi`). It checks that the package is
for the connected keyboard. The SMK-37 web installer at <https://genkigenki.github.io/smk37/> takes the packages too.

## Going back to the stock firmware

Install M-Vave's firmware package for your model with M-Vave's updater, M-UPGRADE. It works directly, because the
installed package keeps the keyboard's model name.

## Storage and safety

| Flash | Frodhes on the SMK-37 |
| --- | --- |
| `0x04000-0x9CFFF` | the app (249 KiB of the 605 KiB app slot); only the update loader writes it |
| `0x9D000` | the settings (the FM-1 keeps them at `0xFC000`, the SMK-37's protected stock area) |
| `0x9E000-0xA1FFF` | the user presets (banks A, B) and the sequencer's pattern (A, B); on the FM-1 at `0x97000`, the SMK-37's app area |
| `0xAF000` | the owner tag: at the first start, the stock firmware's settings there and any other firmware's data in these sectors are cleared once |
| `0xBB000-0xBFFFF` | the update's staging area |
| `0xC2000-0xEAFFF` | USRFLASH, the stock key and knob mapping: never written, so the stock firmware finds its mapping intact |

- Back up first: have M-Vave's stock package for your model and M-UPGRADE ready before you install.
- Keep the USB cable connected until the install has finished and the keyboard has restarted.
- The firmware never writes the keyboard's head, calibration, Bluetooth pairing, USRFLASH, stock resources or key
  areas; the flash map refuses them at compile time and again in the flash driver.
- Installing other firmware is at your own risk.
