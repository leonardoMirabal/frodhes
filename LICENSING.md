# Licensing

Frodhes is free software under the GNU General Public License, version 3 only (`GPL-3.0-only`, full text in
`LICENSE`). You may use, study, change and share it. If you give someone a firmware built from it, give them its
complete source under the same licence too.

## Who made what

| | |
| --- | --- |
| The platform: `firmware/hal/`, `firmware/loader/`, `crt0.S`, `app.ld`, `firmware/src/` (`usb.c`, `ota.c`, `recovery.c`, `lcd.c`, `gfx.c`, `libc.c`, `bootguard.h`, `uac_fir.h`), `tools/` | Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments ([Felucca](https://github.com/hugelton/Felucca)), with the changes of [SLOOP](https://github.com/isod89/sloop-fm1) (isod89) |
| The SMK-37 board layer: `firmware/hal/smk_input.h`, `fm1_flash_map.h`, the SMK-37 parts of `fm1_cc.h`, `fm1_flash.h`, `fm1_lcd_hw.h`, `lcd.c`, `ota.c`, `bootguard.h`, `firmware/loader/`, `tools/fm1pkg_make.py`, `tools/fm1_install.py`, `tools/build.py`; `firmware/src/smk_wheel.h`; `tests/ota_test.c`, `ldr_test.c`, `install_test.py`, `smk_wheel_test.c`, `smk_pkg_test.py` | Copyright (C) 2026 M. Geisler (genkigenki/makkuro), from [genkigenki/sloop-fm1](https://github.com/genkigenki/sloop-fm1) branch `smk37` (the reference port of the [fm1-to-smk37](https://github.com/genkigenki/fm1-to-smk37) kit) |
| The TRS MIDI IN `firmware/src/midi_uart.c` | Felucca's, as sloopDX carries it, with the ring read by content from Felucca [Salt] 0.9-salt14 by ChanceTheMaker (found by keremimo) |
| The update loader's screen: `firmware/loader/ldr_screen.c`, `tests/ldr_screen_test.c` | Copyright (C) 2026 zvenson (from OberVave, the Firmware Hub's look); the idea, a loader that draws, its 5 x 7 letters and the order of things from [Skiff](https://github.com/illuminus5/skiff) by illuminus5, GPL-3.0 |
| From OberVave: the storage (blobs, slots), `LCD_IDLE`, the SMK-37 wiring of `platform.c`, `store.h`, the pad event queue in `smk_input.h`, `tests/smk_flash_test.c`, `tests/run_tests.sh` | Copyright (C) 2026 zvenson |
| The wiring `platform.c`, the host mode `tests/`, `docs/API.md`, `docs/RULES.md` ([Dinghy](https://github.com/zvenson/dinghy)) | zvenson |
| The app `firmware/src/frodhes.c` (derived from Dinghy's app), the engine `firmware/src/rhodes.c` | Leonardo Mirabal |
| Key velocity: `firmware/src/keyvel.c`, `firmware/src/keyvel.h`, `firmware/hal/fm1_keyadc.h` and the converter scheduling in `firmware/hal/fm1_adc.h`, after [fm1-velocity](https://github.com/charlesvestal/fm1-velocity) (commit 8f62ce0, GPL-3.0-only): the method, the row drive and the curve, rewritten in integers for the panel scan | Charles Vestal (fm1-velocity); Leonardo Mirabal (this version) |

## Third-party material

| What | Licence | Where |
| --- | --- | --- |
| Terminus font 8x16 (`assets/fonts/ter-u16n.bdf`), rendered into `firmware/gen/felucca_font.h` by `tools/gen_font.py` | SIL OFL 1.1 | `assets/fonts/Terminus-LICENSE.txt` |
| Inter Tight (`assets/fonts/InterTight[wght].ttf`), rendered into `firmware/gen/aa_font.h` by `tools/gen_aa_font.py` (the screen's values and labels) | SIL OFL 1.1, Copyright 2022 The Inter Project Authors | `assets/fonts/OFL-InterTight.txt` |
| JieLi AC79 SDK: `uboot.boot`, `cfg_tool.bin`, `eq_cfg_hw.bin` are read from your SDK checkout at build time and placed in the package; no SDK files are in this tree. Every release attaches the licence text as `Apache-2.0.txt` | Apache-2.0 | `LICENSES/Apache-2.0.txt`; <https://gitee.com/Jieli-Tech/fw-AC79_AIoT_SDK> |

## Trademarks

"Felucca" and "Hügelton Instruments" are names of Hügelton Instruments; "M-VAVE", "FM-1", "SMK-37" and "MKE-P37"
belong to their owners. Frodhes is not made or endorsed by them. "Fender" and "Rhodes" are trademarks of their owners;
Frodhes is an independent emulation, not affiliated with or endorsed by them.
