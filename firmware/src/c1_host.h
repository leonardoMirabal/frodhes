/* SPDX-License-Identifier: GPL-3.0-only */
/* The host's twin of hal/fm1_core1.h (FR_C1 host and web builds): the same calls, core 1's job run on this thread,
 * so the pipeline's logic (frodhes.c app_audio) is tested sample for sample on the computer.
 *
 * The environment variable FRODHES_C1 (host only) sets how the twin behaves, comma-separated:
 *   order=0     the job runs at fr_c1_run, before core 0's voices (the default)
 *   order=1     the job runs at fr_c1_wait_us, after them: the two orders must render the same samples (anything the
 *               job and the voices both touch would show as a difference: tests/py/test_c1_pipeline.py)
 *   refuse=N    every Nth job is refused (never run, -2: core 0 does it, core 1 stays on)
 *   late=N      the Nth job is late (run, then -1: core 1 held for good, core 0 redoes it)
 *   busy=N      fr_c1_busy: N - 1 of every N looks say busy (C1_AHEAD: chunks merged into one job)
 *   off=1       core 1 never started (the one-core fallback from the first block)
 * The web build: order 0, nothing injected. */
#pragma once
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum { C1_OFF, C1_ON, C1_BAD, C1_LATE, C1_DEAF, C1_LOWV, C1_FAULT, C1_PARK, C1_DESYNC };
static struct {
    uint8_t st, order, cfg;
    uint32_t refuse, late_at, jobs, ref, holds, posted, busy, bq;
    void (*fn)(uint32_t);
    uint32_t arg;
} c1h;

static void c1h_config(void)
{
    const char *e;
    c1h.cfg = 1;
    c1h.st = C1_ON;
#ifndef DINGHY_WEB
    e = getenv("FRODHES_C1");
    while (e && *e) {
        if (!strncmp(e, "order=", 6)) c1h.order = (uint8_t)atoi(e + 6);
        else if (!strncmp(e, "refuse=", 7)) c1h.refuse = (uint32_t)atoi(e + 7);
        else if (!strncmp(e, "late=", 5)) c1h.late_at = (uint32_t)atoi(e + 5);
        else if (!strncmp(e, "busy=", 5)) c1h.busy = (uint32_t)atoi(e + 5);
        else if (!strncmp(e, "off=", 4) && atoi(e + 4)) c1h.st = C1_OFF;
        e = strchr(e, ',');
        if (e) e++;
    }
#else
    (void)e;
#endif
}

static int fr_c1_live(void)
{
    if (!c1h.cfg) c1h_config();
    return c1h.st == C1_ON;
}

static int fr_c1_run(void (*fn)(uint32_t), uint32_t arg)
{
    if (!fr_c1_live()) return 0;
    c1h.fn = fn; c1h.arg = arg; c1h.posted = 1;
    c1h.jobs++;
    if (c1h.refuse && c1h.jobs % c1h.refuse == 0u) { c1h.fn = 0; return 1; }   /* (refused: never runs) */
    if (!c1h.order) { fn(arg); c1h.fn = 0; }
    return 1;
}

static int fr_c1_busy(void)                     /* busy=N: N - 1 of every N looks find core 1 busy (C1_AHEAD merges) */
{
    return c1h.posted && c1h.busy && ++c1h.bq % c1h.busy != 0u;
}

static int fr_c1_wait_us(uint32_t us)
{
    (void)us;
    if (!c1h.posted) return -1;
    c1h.posted = 0;
    if (c1h.refuse && c1h.jobs % c1h.refuse == 0u) { c1h.ref++; return -2; }
    if (c1h.fn) { c1h.fn(c1h.arg); c1h.fn = 0; }
    if (c1h.late_at && c1h.jobs == c1h.late_at) { c1h.st = C1_LATE; c1h.holds++; return -1; }
    return 0;
}

static void fr_c1_block_end(void) {}
