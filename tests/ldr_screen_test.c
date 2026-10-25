/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 illuminus5 (Skiff's test), zvenson (from OberVave) */
/* Host test of the update loader's screen (firmware/loader/ldr_screen.c) through src/lcd.c, on a fake ST7789: the
 * window and RAMWR commands into a 240 x 240 frame. UPDATE MODE after boot with the name, the 20 segments and the
 * hub's address, the waiting light running to and fro, WRITING at 50 % (10 segments, "50%"), a second write starting
 * over, UPDATE FAILED; every letter in the loader's font, only the screen's colours, nothing outside the panel.
 * With a DIR, the screens as PPM files.   tests/ldr_screen_test [DIR] */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint16_t fb[240][240];                   /* RGB565 as the panel stores it */
static uint32_t win[4], cx, cy, cmd, outside;
static const uint8_t *pend;
static uint32_t pend_n;
static void fm1_lcd_hw_init(void) {}
static void fm1_lcd_baud(uint32_t b) { (void)b; }
static void fm1_lcd_backlight(uint32_t on) { (void)on; }
static void fm1_lcd_deselect(void) {}
static void fm1_lcd_send_cmd(uint8_t c)
{
    cmd = c;
    if (c == 0x2C) {
        cx = win[0];
        cy = win[2];
    }
}
static void fm1_lcd_send_data(const void *p, uint32_t n) { pend = p, pend_n = n; }
static void fm1_lcd_wait(void)                  /* the DMA's bytes land here, as the panel takes them */
{
    uint32_t i;
    if (!pend_n)
        return;                                 /* (a command's own byte) */
    if (cmd == 0x2A || cmd == 0x2B)
        for (i = 0; i < 2u; i++)
            win[(cmd == 0x2B) * 2u + i] = (uint32_t)pend[2u * i] << 8 | pend[2u * i + 1u];
    else if (cmd == 0x2C)
        for (i = 0; i + 1u < pend_n; i += 2u) {
            if (cx > 239u || cy > 239u || win[1] > 239u || win[3] > 239u)
                outside++;
            else
                fb[cy][cx] = (uint16_t)(pend[i] << 8 | pend[i + 1u]);
            if (++cx > win[1]) {
                cx = win[0];
                cy++;
            }
        }
    pend_n = 0;
}
#define fm1_irq_off() ((void)0)
#define fm1_irq_on() ((void)0)
#include "../firmware/src/lcd.c"
#include "../firmware/loader/ldr_screen.c"

static int bad;
static void check(const char *what, int ok)
{
    printf("ldr_screen: %-86s %s\n", what, ok ? "ok" : "FAIL");
    bad += !ok;
}
static uint32_t count(uint32_t x0, uint32_t y0, uint32_t x1, uint32_t y1, uint16_t c)   /* pixels of colour c */
{
    uint32_t x, y, n = 0;
    for (y = y0; y < y1; y++)
        for (x = x0; x < x1; x++)
            n += fb[y][x] == c;
    return n;
}
static void ppm(const char *dir, const char *name)
{
    char path[512];
    FILE *f;
    uint32_t x, y;
    if (!dir)
        return;
    snprintf(path, sizeof path, "%s/%s.ppm", dir, name);
    if (!(f = fopen(path, "wb")))
        return;
    fprintf(f, "P6 240 240 255\n");
    for (y = 0; y < 240u; y++)
        for (x = 0; x < 240u; x++) {
            uint16_t v = fb[y][x];
            fputc((v >> 11) * 255 / 31, f), fputc((v >> 5 & 63) * 255 / 63, f), fputc((v & 31) * 255 / 31, f);
        }
    fclose(f);
}

static uint32_t lit_segs(uint16_t c)                 /* segments in colour c (their middle pixel) */
{
    uint32_t i, n = 0;
    for (i = 0; i < LDR_SEGS; i++)
        n += fb[LDR_SEG_Y + LDR_SEG_H / 2u][LDR_SEG_X + i * (LDR_SEG_W + 2u) + LDR_SEG_W / 2u] == c;
    return n;
}
static uint32_t lit_at(void)                         /* the one lit segment, or 99 */
{
    uint32_t i;
    for (i = 0; i < LDR_SEGS; i++)
        if (fb[LDR_SEG_Y + 5u][LDR_SEG_X + i * (LDR_SEG_W + 2u) + 4u] == LDR_COLOUR)
            return i;
    return 99u;
}
static int palette_only(void)
{
    uint32_t x, y;
    for (y = 0; y < 240u; y++)
        for (x = 0; x < 240u; x++) {
            uint16_t c = fb[y][x];
            if (c != LDR_BG && c != LDR_INK && c != LDR_SUB && c != LDR_DIM && c != LDR_HUB && c != LDR_COLOUR && c != LDR_TAIL)
                return 0;
        }
    return 1;
}

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : NULL;
    uint32_t total = 0x93000u - 0x4000u, ms, seen = 0, i, back = 0, last = 0;   /* the app area, as ldr_core.c reports it */
    ldr_screen();
    ppm(dir, "ldr_update_mode");
    check("UPDATE MODE: the name in its colour, the status, the foot line (or none), 20 segments off",
          count(0, 20, 240, 76, LDR_COLOUR) > 300u && count(0, 92, 240, 112, LDR_INK) > 200u &&
          (LDR_FOOT[0] ? count(0, 220, 240, 230, LDR_HUB) > 80u : count(0, 220, 240, 230, LDR_HUB) == 0u) && lit_segs(LDR_DIM) == LDR_SEGS);
    check("only the screen's colours, the background the hub's", palette_only() && count(0, 0, 240, 10, LDR_BG) == 2400u);
    for (ms = 0; ms < 60u * 60u; ms += 60u) {        /* an hour of steps: the light goes to and fro, one segment lit */
        ldr_anim(ms);
        i = lit_at();
        if (i == 99u || lit_segs(LDR_COLOUR) != 1u)
            break;
        seen |= 1u << i;
        back += i < last;
        last = i;
        if (ms == 600u)
            ppm(dir, "ldr_waiting");
    }
    check("the waiting light: always one segment, every one reached, there and back again",
          ms >= 3600u && seen == (1u << LDR_SEGS) - 1u && back > 0u);
    check("its afterglow: one segment halfway, next to the light", lit_segs(LDR_TAIL) == 1u);
    ldr_bar(total / 2u, total);
    ppm(dir, "ldr_writing");
    check("WRITING at 50 %: 10 segments lit, the rest off, \"50%\" under them, the light stopped",
          lit_segs(LDR_COLOUR) == 10u && lit_segs(LDR_DIM) == 10u && count(0, 170, 240, 192, LDR_INK) > 100u && !ldr_idle);
    ldr_anim(99999u);
    check("no waiting light once it writes", lit_segs(LDR_COLOUR) == 10u);
    ldr_bar(total, total);
    check("done: all 20 lit", lit_segs(LDR_COLOUR) == LDR_SEGS);
    ldr_bar(total / 4u, total);
    check("a second write starts over (25 %: 5 segments)", lit_segs(LDR_COLOUR) == 5u);
    ldr_failed();
    ppm(dir, "ldr_failed");
    check("UPDATE FAILED: the segments off, no percent, the way out",
          lit_segs(LDR_DIM) == LDR_SEGS && count(0, 170, 240, 192, LDR_INK) == 0u && count(0, 92, 240, 112, LDR_INK) > 200u);
    check("every letter in the loader's font, nothing outside the panel, only the screen's colours",
          !ldr_missing && !outside && palette_only());
    printf("%s\n", bad ? "LDR SCREEN TEST FAILED" : "ldr_screen test passed");
    return bad != 0;
}
