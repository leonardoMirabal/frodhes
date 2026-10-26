/* SPDX-License-Identifier: GPL-3.0-only */
/* tests/py/test_web_compiles.py: the names web/web_hal.c takes from Emscripten, so the host compiler can check
 * that the emulator's sources still compile (a firmware change that web_hal.c does not follow fails the host tests) */
#pragma once
#define EMSCRIPTEN_KEEPALIVE
void emscripten_sleep(unsigned int ms);
double emscripten_get_now(void);
