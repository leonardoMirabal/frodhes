/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes: a code-generation hint for the pi32v2 compiler, kept in hal/ (tools/build.py: no inline asm in src/).
 * HAL_KEEP_REG(v): v is "changed" here by an empty asm (no instruction), so the compiler keeps it in one 32-bit
 * register instead of widening it to a 64-bit pair (fx.c's phaser: one 32 x 32 -> 64 multiply each). */
#ifndef FM1_KEEP_H
#define FM1_KEEP_H
#define HAL_KEEP_REG(v) __asm__("" : "+r"(v))
#endif
