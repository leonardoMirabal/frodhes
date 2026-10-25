/* SPDX-License-Identifier: GPL-3.0-only */
/* The settings record in two sectors, A / B (Frodhes, 0.9.5; platform.c includes it after sector_load, and so does
 * tests/set_ab_test.c on the flash chip model). A save never erases the record the load would pick: it writes the
 * other sector, so a power cut in the ~100-150 ms save loses that one change, never the whole setup.
 *
 *   +0    [STORE_MAGIC][n][crc32 of the data]      the header, programmed last: a cut save stays invalid
 *   +12   the record, n bytes                       (0.9.4's layout to here: 0.9.4 still reads sector A)
 *   +12+n [seq][~seq]                               programmed with the data, before the header
 *
 * The load takes the valid sector with the newer seq (serial order: the 32-bit wrap is fine; equal: A). A valid
 * record without a seq (0.9.4's, the tail erased) counts as seq 0: the first boot after the update reads it as it
 * is, and the first save goes to B with seq 1. SETTINGS_B unset (Dinghy, the SMK-37: no free sector there):
 * B is A, one sector as before. Needs crc32, sector_load, st_read, fl_op_start / fl_op_poll, fm1_wdt_feed, flash_ok */
#ifndef SETTINGS_B
#define SETTINGS_B SETTINGS_AT
#endif
#define SET_OTHER(at) ((at) == SETTINGS_AT ? SETTINGS_B : SETTINGS_AT)
static uint32_t set_at = SETTINGS_B, set_seq;   /* the newest valid record's sector and seq (none: the first save to A) */
static uint32_t set_tail_seq(uint32_t at, uint32_t n)
{
    uint32_t s[2];
    return !st_read(at + 12u + n, s, sizeof s) && s[0] == ~s[1] ? s[0] : 0u;
}
static int settings_load(void *p, uint32_t n)
{
    uint32_t sa = set_tail_seq(SETTINGS_AT, n), sb = set_tail_seq(SETTINGS_B, n), k;
    uint32_t at = (int32_t)(sb - sa) > 0 ? SETTINGS_B : SETTINGS_AT;   /* (the newer claim first; its crc decides) */
    for (k = 0; k < 2u; k++, at = SET_OTHER(at))
        if (!sector_load(at, p, n)) {
            set_at = at;
            set_seq = at == SETTINGS_B ? sb : sa;
            return 0;
        }
    return -1;
}
/* the save without holding the main loop (fm1_flash.h fl_op_start / fl_op_poll): settings_save_start erases the
 * other sector; settings_save_poll, once a main-loop pass, runs at most a slice or two (<= ~0.6 ms), then the
 * record's two page programs (the data to the page's end; its last 12 bytes and the seq) and the header last. p (a
 * 256-byte record) must stay as it is until the poll returns 0. 1 while it runs, 0 done (or none), < 0 it failed
 * (that sector stays invalid, the other holds the last record; the next save takes the same one). Another save
 * finishes the step in progress first */
#ifndef SET_SS_BSS
#define SET_SS_BSS __attribute__((section(".bss.ss_save")))   /* (out of the merge; the host test: none) */
#endif
static struct { const uint8_t *p; uint32_t at, h[3], t[5]; uint8_t step; } ss SET_SS_BSS;
static int settings_save_poll(void)
{
    int rc;
    if (!ss.step) return 0;
    if ((rc = fl_op_poll()) == 1) return 1;
    if (rc < 0) { ss.step = 0; return rc; }
    if (ss.step == 4u) { ss.step = 0; set_at = ss.at; set_seq = ss.t[3]; return 0; }
    rc = ss.step == 1u ? fl_op_start(ss.at + 12u, ss.p, 244u)
       : ss.step == 2u ? fl_op_start(ss.at + 256u, (const uint8_t *)ss.t, 20u)
       : fl_op_start(ss.at, (const uint8_t *)ss.h, 12u);
    ss.step = rc < 0 ? 0 : ss.step + 1u;
    return rc < 0 ? rc : 1;
}
static int settings_save_start(const void *p, uint32_t n)
{
    while (settings_save_poll() == 1) ;         /* (one still running: to its end) */
    if (!flash_ok || n != 256u)
        return -1;
    ss.p = p;
    ss.at = SET_OTHER(set_at);
    ss.h[0] = STORE_MAGIC; ss.h[1] = n; ss.h[2] = crc32(p, n);
    memcpy(ss.t, ss.p + 244, 12u);
    ss.t[3] = set_seq + 1u; ss.t[4] = ~ss.t[3];
    fm1_wdt_feed();
    if (fl_op_start(ss.at, 0, 0) < 0)
        return -1;
    ss.step = 1;
    return 1;
}
static __attribute__((unused)) int settings_save(const void *p, uint32_t n)   /* the same, holding the loop */
{
    int rc = settings_save_start(p, n);
    while (rc == 1) rc = settings_save_poll();
    return rc;
}
