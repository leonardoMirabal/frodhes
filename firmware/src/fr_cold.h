/* SPDX-License-Identifier: GPL-3.0-only */
/* FR_COLD: the cold code (the UI and its text, the menus, the presets, the settings and their store, the parameters'
 * apply code, the sequencer's, the tempo's, the arpeggiator's, the notes' and the key velocity's main-loop side) is
 * built for flash, not speed: clang's minsize (-Oz for that function), on the FM-1 / SMK-37 firmware only (pi32v2). It
 * frees ~2 KB of the app's XIP slot (2026-11-01). Never on a function the audio or timer interrupt can run, inlined or
 * called: their call tree from fm1_alnk0_irq / fm1_timer5_irq stays byte for byte as it was (tools/isr_diff.py); the
 * effects' files (fx.c, drive.c, drv_*.c, dly_tape.c, rev_*.c) carry none, as their set-up is inlined into the ISR.
 * It goes before "static" (FR_COLD static void f(...)), so the tools that find a function by its "static void f("
 * text still do. The host, web, test and Move builds: empty. */
#ifndef FR_COLD_H
#define FR_COLD_H
#if defined(__PI32V2__) && !defined(DINGHY_HOST)
#define FR_COLD __attribute__((minsize))
#else
#define FR_COLD
#endif
#endif
