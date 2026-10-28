## New

- On the Move, Frodhes plays in every slot: load it on as many tracks as Schwung has slots (four), each with its own sound and knobs. The user presets (U01-U16) are one set for all of them. Up to three play with no dropouts; a fourth works, but very heavy playing with every effect on in all four, or loading a set with several of them, can drop audio.

## Changes

- The Move package has two files of code: `dsp.so` loads a copy of `engine.so` for each slot (in Schwung's data folder, `module_data/frodhes/inst/<k>`), copied again when `engine.so` changes. The settings and user presets stay in `module_data/frodhes/store.bin`: nothing to migrate.
- The ONE SLOT ONLY notice is gone: every slot plays.

## Tested

- Host: `make test-move` (`move/tests/test_multi.c`: two instances play on their own, the shared presets reach a running instance and survive its save, a re-pick unloads the old copy and reuses its index, a changed `engine.so` is copied again, a copy that cannot load is silent and says NO ENGINE), `make gate-p` (the loader and the engine under ASan + UBSan, the no-syscall and no-allocation guards), Gate M, Gate S, `tools/ci_move.sh`, `tests/py/test_move_instances.py` (four slots, four copies, all playing).
- Move (`tools/move_multi_cpu.py`, 16 voices and every effect heavy in each slot, 30 s, the click detector on each tap and on the USB-C Main Out): two and three instances 0 glitches on the taps and on Main Out, each instance's worst callback 575 us (two) and 530 us (three); four instances 0 on the taps, 2 on Main Out. The user presets in `store.bin` unchanged across the runs.
