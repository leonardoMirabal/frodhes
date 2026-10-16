# Frodhes

Rhodes physical-model firmware for the M-VAVE FM-1 (JieLi AC791N, pi32v2, no FPU). Built on Dinghy; `upstream` remote = zvenson/dinghy.

- The app is `firmware/src/frodhes.c` and the engine is `firmware/src/rhodes.c`. It's a single compilation unit: frodhes.c includes platform.c, then rhodes.c.
- Don't edit `platform.c`, `hal/`, `loader/`, or the other platform files unless you have to, so upstream merges stay clean.
- Integers only: no float or double, and no 64-bit `/` or `%`. 64-bit multiply is fine. Use Q15 levels and 32-bit phases.
- The audio ISR (`rh_render`) gets 256 frames at 44.1 kHz. Aim for well under 2.9 ms. No flash, screen, or poll calls from it.
- Hardware register access belongs in `hal/` only. `tools/build.py` enforces this, plus bans ROM addresses 0xFFC00000–0xFFCFFFFF, including as constants.
- To test on the host, run `tests/run_host.sh`. It writes `build/host/frodhes.wav` and screen PNGs and works on macOS without Docker.
- To build firmware, run `./build.sh` to get `build/frodhes.fwsc`. It needs the JieLi toolchain, the AC79 SDK, and Docker on macOS.
- Package identity is `FM-1_958` for every version and dev build; the version is text only (`./build.sh --release X.Y.Z`).

## Every change: test and document (a standing rule)

No change is finished until both are done, and the commit says what was tested:

1. **Test.** `make test` (host scripts; add or update one for new behaviour) and `make pytest`. Then on the FM-1:
   build, install (`tools/fm1_install.py`), and check over MIDI + USB audio (`tools/hil_scan.py`, `tools/hil_cpu.py`,
   `tools/hil_seq.py`, or a script for the new feature). Say plainly what could not be tested (e.g. no TRS source,
   the screen itself) and ask the user to check that part.
2. **Document.** Update `docs/MANUAL.md` (the page, buttons, MIDI CCs, contents), `README.md` (the page table and the
   description), regenerate the screenshots (`tools/manual_shots.py`, and `overview.png`), and rebuild and publish the
   web emulator (`web/build.sh`, then the `gh-pages` worktree in `build/pages`).

## Releases (automatic from a tag)

Version numbers are patch bumps (0.9.1, 0.9.2 ...); the package identity stays `FM-1_958`. To release: run the
tests above (host + FM-1, with `./build.sh --release X.Y.Z` installed and checked on the device), point README.md /
MANUAL.md at the new file name, write `docs/releases/vX.Y.Z.md`, commit, then `git tag -a vX.Y.Z -m "Frodhes X.Y.Z"`
and `git push origin vX.Y.Z`. `.github/workflows/release.yml` builds the firmware from the tag and publishes the
GitHub release with the `.fwsc` and those notes.

