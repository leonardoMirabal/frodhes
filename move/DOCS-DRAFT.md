# Drafts for M7-01 (not published)

Drafts of the Move's part of `docs/MANUAL.md` and `README.md`, to paste at M7-01 when the Move module ships
(private plan 05, U8: nothing is released before everything works). Until then the public page is `docs/MOVE.md`.
Every claim here is the host build's; the device checks (D5) come first.

## MANUAL.md: a new section after "15. Appendix: the SMK-37" (16. Appendix: the Move)

> ## 16. Appendix: the Move
>
> Frodhes also runs on the Move, a standalone groove box, as a sound generator of Schwung, the community module
> host on it: the same instruments, effects, arpeggiator, chords, scales and sequencer as on the FM-1, with
> 16 voices on every instrument. Install and the details: [MOVE.md](MOVE.md).
>
> Everything in this manual applies, with these differences:
>
> - **The pages**: one page holds eight controls, one for each of the Move's eight knobs, two of the FM-1's pages
>   side by side (DRIVE/FILTER, TREM/PHASER, CHORUS/DELAY, ...). The names are shortened to five letters (THRESH is
>   THRSH, DORIAN is DORN); the values and ranges are the FM-1's.
> - **Frodhes' own screen** (PAGES on the first page): the eight knobs as eight cards, each with its name, value
>   and bar; touch or turn a knob and its card fills the screen, as the FM-1 shows one card large; the overview
>   comes back about a second after you let go. The jog wheel turns the pages; a click lists them.
>   ![The DRIVE/FILTER page on the Move](manual/img/move/move-drive-filter.png?v=22df2253)
> - **Sustain**: the Move has no pedal jack. The Capture button is the pedal on the PEDAL screen and on Frodhes'
>   screen (hold it, or tap to latch, as REC on the FM-1); SUS on the first page latches; a pedal on CC 64 (USB)
>   works with half pedalling.
> - **FX** on the first page is the FX button: all effects off or on (CC 111).
> - **Not on the Move**: PERFORM, ASSIGN and DISPLAY (the Move has its own knobs and screen), KEY VEL, TOUCH,
>   USB VOL, TRS, COLOR, BRIGHT and LEDS.
> - The sound is saved with the Move's set.

## MANUAL.md section 12 (MIDI), a note under the CC table

> On the Move the same CCs reach Frodhes through Schwung's track (MOVE.md, "MIDI").

## README.md: a line under the emulator link, and a row in the downloads table

> **Also on the Move**: Frodhes runs as a sound generator on the Move (Schwung), with 16 voices and a screen of its
> own: [docs/MOVE.md](docs/MOVE.md).

| File | For |
| --- | --- |
| `frodhes-module.tar.gz` | the Move (a Schwung sound generator): see [MOVE.md](docs/MOVE.md) |

(The asset name is the fixed one of M7-02; the README links it as `releases/latest/download/frodhes-module.tar.gz`.)
