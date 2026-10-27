# Frodhes

Rhodes physical-model firmware for the M-VAVE FM-1 (JieLi AC791N, pi32v2, no FPU). Built on Dinghy; `upstream`
remote = zvenson/dinghy. **`AGENTS.md` is the full operating manual (rules, procedures, checklists, gotchas);
`docs/ARCHITECTURE.md` explains the code.** Read both before changing anything. The key constraints:

- The app is `firmware/src/frodhes.c` (includes `platform.c`, then `rhodes.c`, `fx.c`, `params.c`, `tempo.c`,
  `arp.c`, `notes.c`, `seq.c`, `ui.c`, `presets.c`: one compilation unit).
- Don't edit `platform.c`, `hal/`, `loader/`, `usb.c` or the other platform files unless you have to, so upstream
  merges stay clean.
- Integers only: no float or double, and no 64-bit `/` or `%` (the link fails). 64-bit multiply is fine. Q15 levels,
  32-bit phases.
- The audio ISR (`app_audio` = `rh_render` + `fx_process`) gets 256 frames at 44.1 kHz = 5805 us. The voice
  shedder acts above 5000 us; the measured worst case is ~4.9 ms with every effect on. No flash, screen or poll
  calls from it. Measure anything new with `tools/hil_cpu.py`.
- Hardware register access belongs in `hal/` only. `tools/build.py` enforces this and bans ROM addresses
  0xFFC00000–0xFFCFFFFF, including as constants.
- No samples: never download, fit or use Rhodes sample libraries. GPL-3.0-only; keep upstream copyright headers.
- Flash writes (settings, presets, the pattern) only when silent (`rh_quiet() && fx_quiet()`).
- Settings record 256 B (`SET_VER 2`, append-only); presets 64 B with 48 slots, and `P_COUNT` is already 48.
- Package identity is `FM-1_958` for every version and dev build; the version is text only
  (`./build.sh --release X.Y.Z`).

## Build and test

- Host: `make test` (tests/scripts through tests/host.c), `make pytest`, `make host` (build/host/frodhes.wav and
  screen PNGs; no Docker needed).
- Firmware: `./build.sh` -> `build/frodhes.fwsc` (JieLi toolchain + 3 AC79 SDK files in `.local/`, Docker on macOS).
- Device: `echo y | python3 tools/fm1_install.py build/frodhes.fwsc`, then `tools/hil_scan.py`, `tools/hil_cpu.py`,
  `tools/hil_seq.py`, `tools/hil_jack.py` (sandbox off for CoreAudio). Test the FM-1 yourself over MIDI + USB audio;
  say what could not be tested.

## Every change: test and document (a standing rule)

No change is finished until both are done, and the commit says what was tested: host tests (add or update a
script), the FM-1 over MIDI + USB audio, then `docs/MANUAL.md`, `README.md`, the screenshots
(`tools/manual_shots.py`, `overview.png`) and the web emulator (`web/build.sh`, then the `gh-pages` worktree in
`build/pages`). Commit and push often. The full checklist is AGENTS.md section 4.

## Releases

Patch bumps (0.9.1, 0.9.2 ...), only when the user asks: full retest with the release build installed, point
README / MANUAL at the new file, write `docs/releases/vX.Y.Z.md`, commit, `git tag -a vX.Y.Z -m "Frodhes X.Y.Z"`,
`git push origin vX.Y.Z`; `.github/workflows/release.yml` builds and publishes. Confirm with the user before any
outward-facing action (release, publishing the emulator). Details: AGENTS.md section 9.
