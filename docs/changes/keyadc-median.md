## Tools

- `tools/hil_cpu.py --keyadc gate`: each engine's OFF / idle / STRESS reading is the median of 5 runs (was the worst of 3), with the limits unchanged (idle <= OFF + 10 us, STRESS <= OFF + 60 us). The worst of 3 read about +-80 us run to run on one build, more than the limits, so 0.9.6's and 0.9.7's first gates failed and their reruns passed. `tools/final/run_final_rev.sh` runs the gate once with 5 runs; the extra 5-run retry of a failing engine is gone.

## Tested

- `tests/py/test_hil_keyvel.py` (a new test: one run 80 us high passes, the same in 3 of 5 runs fails), `tests/py/test_final_keyadc.py` (the gate runs once with 5 runs; a failed gate stays failed); on the FM-1 (cd3a9d60) `hil_cpu.py --keyadc gate --worst --runs 5 --scenario pedal` PASS: idle -16 / -36 / -24 us, STRESS +36 / +48 / +52 us re OFF (Rhodes / VIBES / MARIMBA).
