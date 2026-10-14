# Frodhes

Rhodes physical-model firmware for the M-VAVE FM-1 (JieLi AC791N, pi32v2, no FPU). Built on Dinghy; `upstream` remote = zvenson/dinghy.

- The app is `firmware/src/frodhes.c` and the engine is `firmware/src/rhodes.c`. It's a single compilation unit: frodhes.c includes platform.c, then rhodes.c.
- Don't edit `platform.c`, `hal/`, `loader/`, or the other platform files unless you have to, so upstream merges stay clean.
- Integers only: no float or double, and no 64-bit `/` or `%`. 64-bit multiply is fine. Use Q15 levels and 32-bit phases.
- The audio ISR (`rh_render`) gets 256 frames at 44.1 kHz. Aim for well under 2.9 ms. No flash, screen, or poll calls from it.
- Hardware register access belongs in `hal/` only. `tools/build.py` enforces this, plus bans ROM addresses 0xFFC00000–0xFFCFFFFF, including as constants.
- To test on the host, run `tests/run_host.sh`. It writes `build/host/frodhes.wav` and screen PNGs and works on macOS without Docker.
- To build firmware, run `./build.sh` to get `build/frodhes.fwsc`. It needs the JieLi toolchain, the AC79 SDK, and Docker on macOS.
- Package identity is `FM-1_95N`.
