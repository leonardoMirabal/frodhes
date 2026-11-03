## New

- **Frodhes on the Move**, as a sound generator for Schwung (`frodhes-module.tar.gz`). The steps are in
  `docs/MOVE.md`, and appendix 16 of the manual summarises them.
  - **Install** through Schwung Manager in a web browser (`http://<move-ip>:7700`). Use Install Custom Module >
    From GitHub URL with `https://github.com/leonardoMirabal/frodhes`, or From Tarball with the downloaded
    `frodhes-module.tar.gz`. Update and remove it in the same place.
  - **The three engines**: the electric piano types, VIBES and MARIMBA, each with 16 voices and in its HQ version,
    with every effect.
  - **Frodhes' own full-screen pages** are the whole Move UI for Frodhes. Open them with Shift + a touch on the
    volume knob + the track's Track button, then a click of the jog wheel on the synth. The pages are in the FM-1's
    order, one card for each knob. The overview's bars show each value, and a large card opens on the knob you
    turn. The COMPRESSOR page has a gain-reduction meter. The knob lights show the values, and a double tap
    sets a knob back to its default. The list at the jog wheel holds the pages and a menu with SAVE TO USER and
    Schwung's module rows.
  - **The arrows and the pedal**: the Left arrow turns the effects off or on, and it keeps the compressor on. The
    Right arrow turns the sustain (SUS) on or off. Each arrow is lit while its function is on. A sustain pedal
    on CC 64 works as on the FM-1, and lifting it lets the notes go.
  - **The EQ page**, between COMPRESSOR and DRIVE/FILTER as on the FM-1: LOW, MID, FREQ and HIGH (CC 34-37), with
    the gains in dB and FREQ in Hz.
  - **The reverb at the full sample rate**: every REVERB TYPE runs at 44.1 kHz on the Move, so its tails keep
    their top octave. The controls, SUSTAIN and FREEZE are as on the FM-1.
  - **Pages for the delay and the chorus** (DELAY: DELAY, MIX, TIME, FEEDBK; CHORUS: CHORUS, DEPTH, SPEED, SYNC).
    The first page has the delay's mix on knob 8.
  - **The arpeggiator** (ARPEGGIO: MODE, RATE, OCT, GATE, HOLD; CC 52-55). It runs on the Move's clock or, when the
    Move is stopped, on its tempo. With HOLD on, the notes keep playing after the pads are let go, and a new chord
    replaces them. Each Frodhes has its own arpeggiator, saved with the set. A Frodhes loaded new starts with it
    OFF.
  - **73 presets**: F01-F19, the same as the FM-1's, then F20-F57, 38 presets for the Move only, then 16 user
    slots (U01-U16). SAVE TO USER saves into a user slot. The user slots are shared by every Frodhes on the Move.
    Program change works as on the FM-1 (0-15 F01-F16, 16-31 U01-U16, 32-34 F17-F19), and 35-72 loads F20-F57.
  - **The tempo and the clock come from the Move**: Frodhes follows the Move's transport, and every synced rate
    follows it too. To play in time with a computer, sync the Move to it.
  - **Pressure mappings** (MAPPINGS): channel pressure (AT CC, AT LO, AT HI) and the pressure of the most pressed
    pad (PA CC, PA LO, PA HI) each drive a control through its CC number. Both mappings are off by default. A
    mapping never sends the FM-1's developer and test CCs (2-4, 8-11, 41, 42, 114, 116, 119).
  - **Several Frodhes at once**: load it on every track that has a slot (four), each with its own sound. Up to
    three play with no dropouts. With four, very heavy playing with every effect on can drop audio.
  - **Levels**: the loudest peak is -0.5 dBFS. Keep Schwung's slot volume at 100 %. Turn the Move's Full Velocity
    setting off so that the pads play at every velocity.
