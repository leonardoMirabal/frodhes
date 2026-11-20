/* SPDX-License-Identifier: GPL-3.0-only */
/* Probe: no state the app keeps or publishes depends on a text's width. The app as the plugin builds it (the
 * engine_unit.c switches), compiled with -DFR_OWN=0 (gfx.c, aatext.c: real widths) and with -DFR_OWN=1
 * (plugin/src/own_gfx.h: every text 0 px wide). The main loop runs for a few thousand passes; at each pass the probe
 * (called from the loop's yield) walks the pages, turns the knobs' parameters, and prints a hash of the settings, every
 * parameter and every page's texts, labels and values. tests/py/test_own_gfx.py compares the two texts. */
#define FRP_SLOT 0
#define DINGHY_HOST 1
#define DINGHY_WEB 1
#define DINGHY_PLUGIN 1
#define main(...) static __attribute__((unused)) probe_web_main(__VA_ARGS__)
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#define printf(...) ((void)0)
#define fprintf(...) ((void)0)
#define getenv(name) ((char *)0)
#include "../firmware/src/frodhes.c"
#undef main
#undef printf
#undef fprintf
#undef getenv

static int printf_(const char *f, ...) __attribute__((format(printf, 1, 2)));
#include <stdarg.h>
static int printf_(const char *f, ...) { va_list a; int r; va_start(a, f); r = vprintf(f, a); va_end(a); return r; }
static jmp_buf done;
static int pass;
static double move_now_ms(void) { return (double)pass; }
static uint64_t fnv(uint64_t h, const void *p, size_t n)
{
    const uint8_t *b = p;
    while (n--) h = (h ^ *b++) * 1099511628211ull;
    return h;
}
/* the settings (every parameter lives in them), the page, and each page's four cards: text, label, value, range */
static uint64_t snapshot(void)
{
    uint64_t h = 1469598103934665603ull;
    uint32_t pg, k;
    char t[32];
    h = fnv(h, &set, sizeof set);
    h = fnv(h, &ui_page, 1);
    h = fnv(h, &ui_vp, 1);
    for (pg = 0; pg < PG_N; pg++)
        for (k = 0; k < 4u; k++) {
            uint32_t s = slot_at(pg, k);
            int32_t hi = 0, v;
            const char *lab = slot_label(pg, k);
            if (s == S_NONE) continue;
            v = slot_val(s, &hi);
            memset(t, 0, sizeof t);
            slot_text(s, t);
            h = fnv(h, t, strlen(t) + 1u);
            h = fnv(h, lab, strlen(lab) + 1u);
            h = fnv(h, &v, sizeof v);
            h = fnv(h, &hi, sizeof hi);
        }
    return h;
}
#define PASSES 3000
static void move_yield(void)
{
    uint32_t pg, k;
    pass++;
    if (pass % 7 == 0) {                        /* a page, a voice page, and a turn of each of its four knobs */
        pg = (uint32_t)(pass / 7) % PG_N;
        ui_page = (uint8_t)pg;
        ui_vp = (uint8_t)((pass / 11) % 5);
        ui_draw_page_change();
        for (k = 0; k < 4u; k++) {
            uint32_t s = slot_at(pg, k);
            if (s != S_NONE && s != S_VERSION) slot_turn(s, (pass / 7) % 3 == 0 ? -9 : (pass / 7) % 3 == 1 ? 1 : 23);
        }
    }
    if (pass % 50 == 0) printf_("pass %d %016llx page %u\n", pass, (unsigned long long)snapshot(), (unsigned)ui_page);
    if (pass >= PASSES) longjmp(done, 1);
}
int main(void)
{
    if (!setjmp(done)) app_main();
    printf_("done %d %016llx\n", pass, (unsigned long long)snapshot());
    return 0;
}
