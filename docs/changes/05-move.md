<!-- VERIFY after the Move gates (Gate M on the device, MIN tier, the 16-voice CPU and 3 tracks worst case): add the
Move Tested lines; keep "Changes" only if the Move package ships in this release (tools/release.py move section). -->
## Changes

- **On the Move**: the electric piano's new touch, upper register, sustain and damper (above), in the Move's
  higher-quality version, with 16 voices on every instrument.

## Tools

- Gate M `--update` regenerates the Move's reference hashes after an intended sound change, reusing the reference
  renders while they are current; a guard stops it when the engine changed but the references did not.
- `move_multi_cpu`: a verdict per track count against the Move's CPU limits (exit 1 on a miss), paced parameter writes
  and the slots restored afterwards; `move_hil d2` checks a slot's TUNE, MARIMBA's MUTE and the vibraphone motor.
- `gen_move_presets` checks the F20-F57 records against the parameter table.
- Gate M and Gate R no longer share a build file that another build can replace mid-run.
- `hil_scan`: steadier floors for MARIMBA's jack rows (NOISE contact floor, WAH DEPTH).

## Tested

- Host: MT0 (`tools/ci_move.sh`: Gate U, `make test-move`, Gate M, the Move pytest suites); Gate R 123/123 and
  Gate M 78/78 with the build isolation.
