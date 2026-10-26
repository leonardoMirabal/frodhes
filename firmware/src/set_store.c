/* SPDX-License-Identifier: GPL-3.0-only */
/* The store's record in two sectors, A / B (Frodhes: 0.9.5 the settings, 0.9.6 the settings and the user presets;
 * platform.c includes it after sector_load, and so do tests/set_ab_test.c on the flash chip model and tests/host.c).
 * A save never erases the record the load would pick: it writes the other sector, so a power cut in the ~100-150 ms
 * save loses that one change, never the whole setup or a preset.
 *
 *   +0    [STORE_MAGIC][n][crc32 of the data]      the header, programmed last: a cut save stays invalid
 *   +12   the record, n bytes                       the 256-byte settings, then ST_TAIL_N bytes (st_tail)
 *   +12+n [seq][~seq]                               programmed with the data, before the header
 *
 * The load takes the valid sector with the newer seq (serial order: the 32-bit wrap is fine; equal: A); a record of
 * another n is not this one (0.9.5's 256 bytes: settings_load_old reads them, once, for the migration; frodhes.c). A
 * valid record without a seq (0.9.4's, the tail erased) counts as seq 0. SETTINGS_B unset: B is A, one sector. Needs
 * crc32, area_load, st_read, fl_op_start / fl_op_poll, fm1_wdt_feed, flash_ok */
#ifndef SETTINGS_B
#define SETTINGS_B SETTINGS_AT
#endif
#ifndef ST_TAIL_N
#define ST_TAIL_N 0u                            /* the bytes after the settings (Frodhes: the user presets) */
#endif
#ifndef SET_SS_BSS
#define SET_SS_BSS __attribute__((section(".bss.ss_save")))   /* (out of the merge; the host test: none) */
#endif
#ifndef ST_SAVED
#define ST_SAVED() ((void)0)                    /* (tests/host.c: prints it) */
#define ST_WAIT() ((void)0)                     /* (tests/host.c: the time a held save takes goes by) */
#endif
#define SET_OTHER(at) ((at) == SETTINGS_AT ? SETTINGS_B : SETTINGS_AT)
#define ST_N (256u + ST_TAIL_N)                 /* the record's data: the settings, then the tail */
static uint32_t set_at = SETTINGS_B, set_seq;   /* the newest valid record's sector and seq (none: the first save to A) */
static uint8_t *st_tail;                        /* the tail's bytes in RAM (presets.c: pr_user; set before the load) */
static uint8_t st_rec[12u + ST_N + 8u] SET_SS_BSS;   /* [header][data][seq][~seq]: the load reads, the save writes it */
static uint32_t set_tail_seq(uint32_t at, uint32_t n)
{
    uint32_t s[2];
    return !st_read(at + 12u + n, s, sizeof s) && s[0] == ~s[1] ? s[0] : 0u;
}
/* the newer valid record of n bytes in a / b into st_rec + 12 (the newer claim first; its crc decides); 0: found */
static int rec_load(uint32_t a, uint32_t b, uint32_t n)
{
    uint32_t sa = set_tail_seq(a, n), sb = set_tail_seq(b, n), k, at = (int32_t)(sb - sa) > 0 ? b : a;
    for (k = 0; k < 2u; k++, at = at == a ? b : a)
        if (!area_load(at, SECTOR_MAX, STORE_MAGIC, st_rec + 12u, n)) {
            set_at = at;
            set_seq = at == b ? sb : sa;
            return 0;
        }
    return -1;
}
/* the record: the settings (n = 256) into p, the tail into st_tail. A load that fails leaves both as they were */
static int settings_load(void *p, uint32_t n)
{
    if (n != 256u || rec_load(SETTINGS_AT, SETTINGS_B, ST_N))
        return -1;
    memcpy(p, st_rec + 12u, n);
    if (ST_TAIL_N) memcpy(st_tail, st_rec + 12u + 256u, ST_TAIL_N);
    return 0;
}
#ifdef OLD_SET_A
/* the settings before 0.9.6 (OLD_SET_A / B, 256 bytes, no tail), once, at the first boot (frodhes.c): the next save
 * goes to the sector other than the one it came from (on the FM-1 the same pair: its newest copy stays) */
static int settings_load_old(void *p, uint32_t n)
{
    if (n != 256u || rec_load(OLD_SET_A, OLD_SET_B, n))
        return -1;
    memcpy(p, st_rec + 12u, n);
    return 0;
}
#endif
/* the save without holding the main loop (fm1_flash.h fl_op_start / fl_op_poll): settings_save_start copies the
 * record (p: the 256-byte settings, and the tail) and erases the other sector; settings_save_poll, once a main-loop
 * pass, runs at most a slice or two (<= ~0.6 ms), then the data and the seq a page at a time, the header last. 1 while
 * it runs, 0 done (or none), < 0 it failed (that sector stays invalid, the other holds the last record; the next save
 * takes the same one). Another save finishes the step in progress first */
static struct { uint32_t at, off; uint8_t step; } ss SET_SS_BSS;
static int settings_save_poll(void)
{
    int rc;
    if (!ss.step) return 0;
    if ((rc = fl_op_poll()) == 1) return 1;
    if (rc < 0) { ss.step = 0; return rc; }
    if (ss.step == 3u) { ss.step = 0; set_at = ss.at; memcpy(&set_seq, st_rec + 12u + ST_N, 4u); ST_SAVED(); return 0; }
    if (ss.off < 12u + ST_N + 8u) {             /* the data and the seq, to each page's end */
        uint32_t k = 256u - ((ss.at + ss.off) & 255u);
        if (k > 12u + ST_N + 8u - ss.off) k = 12u + ST_N + 8u - ss.off;
        rc = fl_op_start(ss.at + ss.off, st_rec + ss.off, k);
        ss.off += k;
        ss.step = 2;
    } else {
        rc = fl_op_start(ss.at, st_rec, 12u);   /* the header last: a cut save stays invalid */
        ss.step = 3;
    }
    if (rc < 0) { ss.step = 0; return rc; }
    return 1;
}
static int settings_save_start(const void *p, uint32_t n)
{
    uint32_t h[3], t[2];
    while (settings_save_poll() == 1) ;         /* (one still running: to its end) */
    if (!flash_ok || n != 256u)
        return -1;
    memcpy(st_rec + 12u, p, n);
    if (ST_TAIL_N) memcpy(st_rec + 12u + 256u, st_tail, ST_TAIL_N);
    h[0] = STORE_MAGIC; h[1] = ST_N; h[2] = crc32(st_rec + 12u, ST_N);
    t[0] = set_seq + 1u; t[1] = ~t[0];
    memcpy(st_rec, h, 12u);
    memcpy(st_rec + 12u + ST_N, t, 8u);
    ss.at = SET_OTHER(set_at);
    ss.off = 12u;
    fm1_wdt_feed();
    if (fl_op_start(ss.at, 0, 0) < 0)
        return -1;
    ss.step = 1;
    return 1;
}
/* the record last saved, read back (presets.c, after a preset save): 0 = as written */
static __attribute__((unused)) int settings_check(void)
{
    uint8_t b[64];
    uint32_t o, k;
    for (o = 0; o < 12u + ST_N + 8u; o += k) {
        k = 12u + ST_N + 8u - o < sizeof b ? 12u + ST_N + 8u - o : sizeof b;
        if (st_read(set_at + o, b, k) || memcmp(b, st_rec + o, k)) return -1;
    }
    return 0;
}
static __attribute__((unused)) int settings_save(const void *p, uint32_t n)   /* the same, holding the loop */
{
    int rc = settings_save_start(p, n);
    while (rc == 1) { ST_WAIT(); rc = settings_save_poll(); }
    return rc;
}
