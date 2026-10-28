## New

- Frodhes on the Move, as a sound generator of Schwung (`frodhes-module.tar.gz`): the electric piano types, VIBES and MARIMBA with every effect, 16 voices on every instrument, the HQ version of the three engines, Schwung's knob pages (eight controls a page, the compressor's gain-reduction meter on COMPRESSOR), a screen of Frodhes' own (PAGES), the sustain on or off (SUS; the Capture button on Frodhes' screen), the presets and program changes, and the pads' pressure mapped to a CC (off by default). The tempo follows the Move's clock; the sequencer, the arpeggiator, the chords and the scales are left to the Move. Install and the details: `docs/MOVE.md`; the manual's appendix 16.

## Tested

- Host: `make test`, `tests/py/test_move_doc.py` (the Move's docs against the code: the pages, the package and its files, the install paths, the voices, the dropped CCs, the sustain button, the screen's timing), `tests/py/test_move_ui.py` (every screen drawn in its two colours, the docs' pictures and their links).
