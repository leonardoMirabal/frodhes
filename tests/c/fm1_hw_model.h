/* SPDX-License-Identifier: GPL-3.0-only */
/* The FM-1's port A / B, its 74HC595 chain and its key matrix as the scan tick sees them (tests/c/keyadc_test.c's
 * model, shared with tests/c/keyvel_e2e_test.c). Include after hal/fm1_input.h (as the python tests copy it: every
 * register access a call of fake_reg()). fake_reg() steps the model on every access: it clocks the 595 on SRCLK /
 * RCLK edges, gives PA / PB IN from the latched column, the pressed keys and each row's pin state (a row driven low
 * and released reads low until the pull-up has lifted it, FAKE_RC_TICKS), advances a fake TIMER4 (24 per us, one
 * per access) and counts every rule broken (bad.*). FM1_MODEL_ON_ACCESS(a), if defined, sees every access too. */
#pragma once
/* ---- the hardware model */
#define REG(a) mem[(a) >> 2]
#define PA_(r) (0x50000u + (r))
#define PB_(r) (0x50040u + (r))
#define PH_(r) (0x501C0u + (r))
#define FAKE_RC_TICKS 48u                       /* 2 us: a row released from 0 V reads low this long */
static uint32_t mem[0x60000u / 4u];
static uint32_t now;                            /* fake TIMER4 (24 per us): every access takes a tick */
static uint32_t sr, latch, prev_out_a, prev_dir_a, inited;
static uint32_t rel_low_at[16];                 /* PA pin released from a low drive at this time (+1; 0 never) */
static uint8_t pressed[FM1_NCOL];               /* packed row bits (FM1_KEYMAP rows) per column */
static uint16_t force[FM1_NCOL][2];             /* the pill's driven reading per key on PA7 / PA8 */
static struct {
    uint32_t read_driven, die_off, pa6_driven, led_conv, ticks;
} bad;

static int active_col(void)
{
    int c, n = -1;
    for (c = 0; c < (int)FM1_NCOL; c++)
        if (!(latch >> c & 1u)) {
            if (n >= 0) return -2;
            n = c;
        }
    return n;
}
static uint32_t led_lines(void)                 /* the 4 LED lines, packed as fm1_led (bit 1..4) */
{
    uint32_t r, v = 0;
    for (r = 1; r < 5u; r++) {
        uint32_t a = FM1_LED_PORT[r] == FM1_PA ? PA_(FM1_OUT) : PH_(FM1_OUT);
        if (REG(a) >> FM1_LED_BIT[r] & 1u) v |= 1u << r;
    }
    return v;
}
/* the level of row pin (PA bit pb, matrix row r): 1 high */
static uint32_t pin_level(uint32_t pb, uint32_t r)
{
    int c = active_col();
    if (!(REG(PA_(FM1_DIR)) >> pb & 1u))
        return REG(PA_(FM1_OUT)) >> pb & 1u;    /* an output */
    if (c >= 0 && (pressed[c] >> r & 1u))
        return 0;
    if (rel_low_at[pb] && now - (rel_low_at[pb] - 1u) < FAKE_RC_TICKS)
        return 0;
    return 1;
}
static void model_step(void)
{
    uint32_t o = REG(PA_(FM1_OUT)), d = REG(PA_(FM1_DIR)), pb;
    if ((o & ~prev_out_a) & (1u << 3))          /* SRCLK rising: SER in */
        sr = ((sr << 1) | (o >> 4 & 1u)) & 0xFFFFu;
    if ((o & ~prev_out_a) & (1u << 1))          /* RCLK rising: the outputs */
        latch = sr;
    for (pb = 7; pb <= 8u; pb++)                /* a row released from a low drive */
        if ((d & ~prev_dir_a) >> pb & 1u && !(prev_out_a >> pb & 1u))
            rel_low_at[pb] = now + 1u;
    prev_out_a = o;
    prev_dir_a = d;
    if (inited) {
        if ((REG(PA_(FM1_DIE)) & 0x1E1u) != 0x1E1u || !(REG(PB_(FM1_DIE)) >> 7 & 1u))
            bad.die_off++;
        if (!(d >> 6 & 1u))
            bad.pa6_driven++;
    }
}
static volatile uint32_t *fake_reg(uint32_t a)
{
    model_step();
    now++;
    if (a == 0x10804u) {
        REG(a) = now;
    } else if (a == PA_(FM1_IN)) {
        static const uint8_t PIN[6] = {0, 5, 6, 7, 8, 0};
        uint32_t r, v = 0xFFFFFFFFu;
        for (r = 0; r < 5u; r++) {
            if (inited && (PIN[r] == 7u || PIN[r] == 8u) && !(REG(PA_(FM1_DIR)) >> PIN[r] & 1u))
                bad.read_driven++;
            if (!pin_level(PIN[r], r)) v &= ~(1u << PIN[r]);
        }
        REG(a) = v;
    } else if (a == PB_(FM1_IN)) {
        int c = active_col();
        REG(a) = (c >= 0 && (pressed[c] >> 5 & 1u)) ? ~(1u << 7) : 0xFFFFFFFFu;
    }
#ifdef FM1_MODEL_ON_ACCESS
    FM1_MODEL_ON_ACCESS(a);                     /* (keyvel_e2e_test.c: the converter) */
#endif
    return &mem[a >> 2];
}


static uint32_t pin_state(uint32_t pb)          /* 0 input, 1 driven low, 2 driven high; | HD0 << 2 | HD << 3 */
{
    uint32_t s = !(REG(PA_(FM1_DIR)) >> pb & 1u) ? 1u + (REG(PA_(FM1_OUT)) >> pb & 1u) : 0u;
    return s | (REG(PA_(FM1_HD0)) >> pb & 1u) << 2 | (REG(PA_(FM1_HD)) >> pb & 1u) << 3;
}
