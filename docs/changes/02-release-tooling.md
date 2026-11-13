## Tools

- A release stays a draft until every asset is on it (the firmware packages, the Move package, the 5 desktop plugin
  packages, the MPC plugin and every download the docs link): `tools/release_assets.sh`. Jobs that got no runner
  are re-run by themselves.
- The release comparison baselines moved to 0.9.11 (`tools/release_diff.py`, the quick level and dry-distortion
  references).
- The full-scan device limits were re-derived from the 0.9.10 and 0.9.11 device runs (each change with its reason).
- `tools/move_multi_cpu.py` judges the Move's overrun counter against its idle rate, not against zero.
