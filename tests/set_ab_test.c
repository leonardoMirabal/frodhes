/* SPDX-License-Identifier: GPL-3.0-only */
/* The store's record in two sectors (firmware/src/set_store.c, 0.9.6: the settings and the user presets) on the flash
 * chip model (tests/fl_host_model.h, as tests/flash_split_test.c): a power cut at every call of a sliced save (the
 * erase's and each program's slices), with the cut operation's sector or page left as it was, done, or half done
 * (random bits), from every start: A newer, B newer, nothing valid, and the migration (0.9.5's settings at the old
 * place, no record yet). After the cut the load gives the old record or the new one, never neither while one was
 * valid, the new one only once its header was programmed; a cut migration leaves 0.9.5's settings readable, so the
 * next boot migrates again; the next save works and the copies alternate. Also the sequence's 32-bit wrap, a
 * corrupted header / data / sequence, the read-back, the allow-list. Every erase and program must land in the
 * store's sectors (the model's window: a write anywhere else, a stock region, fails the test) and nothing else of
 * the flash changes. FM-1 and SMK-37.
 * Build: cc -O1 -Wall -Wno-unused-function -Ifirmware/hal -o build/set_ab_test tests/set_ab_test.c */
#define FL_HOST_MODEL "../../tests/fl_host_model.h"
#include "../firmware/src/frodhes_store.h"
#include "fm1_flash.h"
#include <stdlib.h>

static int fails;
static void check(const char *what, int ok)
{
    if (!ok) { printf("FAIL  %s\n", what); fails++; }
}

/* ---- what platform.c gives set_store.c (crc32, area_load, sector_load copied verbatim: tests/py/test_set_ab.py
 * checks they match platform.c's) ---- */
static uint8_t flash_ok = 1;
static void fm1_wdt_feed(void) {}
static int st_read(uint32_t off, void *dst, uint32_t n)
{
    int r;
    uint64_t keep = M.off_max;
    m_cli(); r = fl_read_ram(off, dst, n); m_sti();
    M.off_max = keep;
    return r;
}
/* platform.c begin */
static uint32_t crc32(const uint8_t *p, uint32_t n)
{
    uint32_t c = 0xFFFFFFFFu, k;
    while (n--) {
        c ^= *p++;
        for (k = 0; k < 8u; k++)
            c = c & 1u ? (c >> 1) ^ 0xEDB88320u : c >> 1;
    }
    return ~c;
}
static int area_load(uint32_t at, uint32_t max, uint32_t magic, void *p, uint32_t n)   /* reads straight into p */
{
    uint32_t h[3];
    if (!flash_ok || n > max || st_read(at, h, sizeof h) || h[0] != magic || h[1] != n || st_read(at + sizeof h, p, n))
        return -1;
    return crc32(p, n) == h[2] ? 0 : -1;
}
/* platform.c end */
/* platform.c begin */
static int sector_load(uint32_t at, void *p, uint32_t n)
{
    static uint8_t buf[SECTOR_MAX];
    if (n > sizeof buf || area_load(at, SECTOR_MAX, STORE_MAGIC, buf, n))
        return -1;
    memcpy(p, buf, n);
    return 0;
}
/* platform.c end */
#define SET_SS_BSS
#include "../firmware/src/set_store.c"

#define N 256u                                  /* the settings */
#define RN (256u + ST_TAIL_N)                   /* the record: the settings, then the tail (the user presets) */
static uint8_t R[8][RN];                        /* records 0..7, each its own bytes */
static uint8_t tail[ST_TAIL_N];                 /* (presets.c pr_user) */
static uint8_t snap[0x100000];                  /* the flash before: nothing outside the store may change */

static void reboot(void)                        /* power off and on: the flash stays, the RAM is new */
{
    m_reset(0x856014u, 1, 20000u, 1500u, 400u);
    M.w_lo = SETTINGS_AT; M.w_hi = SETTINGS_B + 0x1000u;   /* (the model fails any write elsewhere) */
    fl_can_split = -1;
    memset(&fl_aop, 0, sizeof fl_aop);
    memset(&ss, 0, sizeof ss);
    set_at = SETTINGS_B; set_seq = 0;
    st_tail = tail;
}
/* a sector as a save leaves it: n = RN (0.9.6's record) or N (0.9.5's settings, before the store) */
static void put(uint32_t at, const uint8_t *r, uint32_t n, int seq_on, uint32_t seq)
{
    uint32_t h[3] = {STORE_MAGIC, n, crc32(r, n)}, t[2] = {seq, ~seq};
    memset(m_mem + at, 0xFF, 4096u);
    memcpy(m_mem + at, h, 12u);
    memcpy(m_mem + at + 12u, r, n);
    if (seq_on) memcpy(m_mem + at + 12u + n, t, 8u);    /* (0.9.4's record: no sequence) */
}
static int load_is(void)                        /* the record the load gives: 0..7, -1 none, -2 rubbish */
{
    static uint8_t got[N];
    int i;
    memset(got, 0xEE, N);
    memset(tail, 0xEE, ST_TAIL_N);
    if (settings_load(got, N)) {
        for (i = 0; i < (int)N; i++) if (got[i] != 0xEE) return -2;   /* (a failed load leaves p as it was) */
        for (i = 0; i < (int)ST_TAIL_N; i++) if (tail[i] != 0xEE) return -2;
        return -1;
    }
    for (i = 0; i < 8; i++) if (!memcmp(got, R[i], N) && !memcmp(tail, R[i] + N, ST_TAIL_N)) return i;
    return -2;
}
static int old_is(void)                         /* 0.9.5's settings (the migration reads them): 0..7, -1 none */
{
    static uint8_t got[N];
    int i;
    if (settings_load_old(got, N)) return -1;
    for (i = 0; i < 8; i++) if (!memcmp(got, R[i], N)) return i;
    return -2;
}
static int old_peek(void)                       /* the same, the store's state kept (settings_load_old sets where the
                                                 * next save goes: the firmware calls it only when no record loads) */
{
    uint32_t a = set_at, q = set_seq;
    int r = old_is();
    set_at = a; set_seq = q;
    return r;
}
/* a save of record r, cut after `cut` calls (0: none); mode: the cut operation's bytes 0 as before, 1 done, 2 half.
 * Returns the calls a whole save takes; *hdr = 1 once the header's program was started */
static uint32_t save_cut(int r, uint32_t cut, int mode, int *hdr)
{
    uint32_t calls = 0;
    int rc;
    memcpy(tail, R[r] + N, ST_TAIL_N);
    rc = settings_save_start(R[r], N);
    *hdr = 0;
    calls++;
    while (rc == 1 && (!cut || calls < cut)) {
        m_advance(400u * 24u);                  /* the rest of a main-loop pass */
        if (ss.step == 3u) *hdr = 1;
        rc = settings_save_poll();
        calls++;
    }
    if (ss.step == 3u) *hdr = 1;
    if (cut && M.busy) {                        /* the power goes mid-operation */
        uint32_t i;
        if (M.op == 1) {
            for (i = 0; i < 4096u; i++)
                m_mem[M.op_addr + i] = mode == 1 ? 0xFFu : mode == 2 ? (uint8_t)(m_mem[M.op_addr + i] | rand()) : m_mem[M.op_addr + i];
        } else
            for (i = 0; i < M.op_n; i++) {
                uint8_t *b = &m_mem[(M.op_addr & ~0xFFu) + ((M.op_addr + i) & 0xFFu)];
                *b &= mode == 1 ? M.op_data[i] : mode == 2 ? (uint8_t)(M.op_data[i] | rand()) : 0xFFu;
            }
    }
    check("the save without a model violation (and nothing written outside the store)", M.bad == 0);
    if (!cut) check("the read-back (settings_check) after a whole save", settings_check() == 0);
    return calls;
}
static void outside_same(const char *what)     /* nothing of the flash but the store's sectors changed */
{
    check(what, !memcmp(m_mem, snap, SETTINGS_AT) &&
          !memcmp(m_mem + SETTINGS_B + 0x1000u, snap + SETTINGS_B + 0x1000u, sizeof snap - SETTINGS_B - 0x1000u));
}

int main(void)
{
    uint32_t i, cut, full = 0, cuts = 0;
    int st, mode, hdr;
    char msg[200];
    for (i = 0; i < 8u * RN; i++) R[i / RN][i % RN] = (uint8_t)(i * 131u + 7u + i / RN);
    for (i = 0; i < sizeof snap; i++) m_mem[i] = (uint8_t)(i * 7u ^ i >> 9);   /* (the stock's data, say) */
    srand(1);

    /* the migration: 0.9.5's settings at the old place (0.9.4's: no sequence), no record; the first save of the
     * record; on the FM-1 the old place is the record's A / B, on the SMK-37 0x9D000 (never written now) */
    {
        static const uint8_t B_WAS[] = {0xFF, 0x5A, 0x00};
        for (st = 0; st < 3; st++) {
            reboot();
            memset(m_mem + SETTINGS_AT, 0xFF, 0x2000u);
            put(OLD_SET_A, R[0], N, 0, 0);
            if (OLD_SET_B != OLD_SET_A) memset(m_mem + OLD_SET_B, B_WAS[st], 4096u);
            memcpy(snap, m_mem, sizeof snap);
            check("migration: no record yet", load_is() == -1);
            snprintf(msg, sizeof msg, "migration (B 0x%02X): 0.9.5's settings load, seq 0", B_WAS[st]);
            check(msg, old_is() == 0 && set_at == OLD_SET_A && set_seq == 0);
            save_cut(1, 0, 0, &hdr);
            reboot();
            check("migration: the record loads after a reboot, seq 1", load_is() == 1 && set_seq == 1u);
            if (OLD_SET_A == SETTINGS_AT)
                check("migration (FM-1): the record went to B, A still 0.9.5's settings", set_at == SETTINGS_B &&
                      old_peek() == 0);
            else
                check("migration (SMK-37): the record in A, 0x9D000 as it was", set_at == SETTINGS_AT && old_peek() == 0);
            save_cut(2, 0, 0, &hdr);
            reboot();
            check("migration: the second save, seq 2, the other sector", load_is() == 2 && set_seq == 2u &&
                  set_at == (OLD_SET_A == SETTINGS_AT ? SETTINGS_AT : SETTINGS_B));
            if (OLD_SET_A == SETTINGS_AT)
                check("0.9.5's reader (256 bytes at A / B) finds nothing: its defaults (a downgrade)", old_peek() == -1);
            else
                check("SMK-37: 0.9.5's settings at 0x9D000 still as they were", old_peek() == 0);
            outside_same("migration: nothing outside the store's sectors changed");
        }
    }

    /* a power cut at every call of a save, every start, three ways of leaving the cut operation. Starts 0..2: the
     * record in A, B, none; 3, 4: the migration (0.9.5's settings at the old place, newest in A / in B, no record) */
    for (st = 0; st < 5; st++) {
        int old = st == 2 ? -1 : st >= 3 ? -1 : 0;
        uint32_t calls;
        for (cut = 1;; cut++) {
            for (mode = 0; mode < 3; mode++) {
                int got;
                reboot();
                memset(m_mem + SETTINGS_AT, 0xFF, 0x2000u);
                if (st == 0) { put(SETTINGS_B, R[3], RN, 1, 4u); put(SETTINGS_AT, R[0], RN, 1, 5u); }
                if (st == 1) { put(SETTINGS_AT, R[3], RN, 1, 6u); put(SETTINGS_B, R[0], RN, 1, 7u); }
                if (st == 3) { put(OLD_SET_B, R[3], N, 1, 4u); put(OLD_SET_A, R[5], N, 1, 5u); }
                if (st == 4) { put(OLD_SET_A, R[3], N, 1, 6u); put(OLD_SET_B, R[5], N, 1, 7u); }
                memcpy(snap, m_mem, sizeof snap);
                got = load_is();
                check("before the save: the newest record", got == old);
                if (st >= 3) check("before the save: 0.9.5's newest settings", old_is() == 5);
                calls = save_cut(1, cut, mode, &hdr);
                reboot();
                got = load_is();
                snprintf(msg, sizeof msg, "start %d, cut at call %u, mode %d: the old record (%d) or the new, got %d",
                         st, cut, mode, old, got);
                check(msg, got == old || got == 1);
                snprintf(msg, sizeof msg, "start %d, cut at call %u, mode %d: the new one only after its header",
                         st, cut, mode);
                check(msg, got != 1 || hdr);
                if (st >= 3) {
                    snprintf(msg, sizeof msg, "start %d, cut at call %u, mode %d: no record, 0.9.5's settings still "
                             "there (the next boot migrates again)", st, cut, mode);
                    check(msg, got == 1 || old_peek() == 5);
                    if (OLD_SET_B != OLD_SET_A || OLD_SET_A != SETTINGS_AT)
                        check("the migration's cut: 0.9.5's newest sector as it was",
                              !memcmp(m_mem + (st == 3 ? OLD_SET_A : OLD_SET_B), snap + (st == 3 ? OLD_SET_A : OLD_SET_B), 4096u));
                }
                outside_same("the cut: nothing outside the store's sectors changed");
                cuts++;
                save_cut(2, 0, 0, &hdr);        /* the next save, after the cut: whole */
                reboot();
                snprintf(msg, sizeof msg, "start %d, cut at call %u, mode %d: the next save loads", st, cut, mode);
                check(msg, load_is() == 2);
                save_cut(4, 0, 0, &hdr);        /* and the one after it: the sectors alternate */
                reboot();
                check("and the next one", load_is() == 4);
            }
            if (calls < cut) { full = calls; break; }   /* (a cut past the save's end: it was whole) */
        }
    }
    printf("set_ab: %u cuts, a save %u calls\n", cuts, full);

    /* the copies alternate: A, B, A, B, each save leaves the one before readable */
    reboot();
    memset(m_mem + SETTINGS_AT, 0xFF, 0x2000u);
    for (i = 0; i < 6u; i++) {
        uint32_t was = set_at;
        save_cut((int)(i & 7u), 0, 0, &hdr);
        check("the copies alternate", set_at != was && set_at == (i & 1u ? SETTINGS_B : SETTINGS_AT));
        if (i) {
            static uint8_t got[RN];
            check("the copy before stays valid", !area_load(was, SECTOR_MAX, STORE_MAGIC, got, RN) &&
                  !memcmp(got, R[i - 1u], RN));
        }
        reboot();
        check("a save plus a power cycle loads it", load_is() == (int)i);
    }

    /* the sequence's wrap: 0 is newer than 0xFFFFFFFF */
    reboot();
    put(SETTINGS_AT, R[0], RN, 1, 0xFFFFFFFFu);
    put(SETTINGS_B, R[3], RN, 1, 0xFFFFFFFEu);
    check("wrap: 0xFFFFFFFF over 0xFFFFFFFE", load_is() == 0 && set_at == SETTINGS_AT);
    save_cut(1, 0, 0, &hdr);
    reboot();
    check("wrap: the save after 0xFFFFFFFF is seq 0, to B, and newer", load_is() == 1 && set_seq == 0u &&
          set_at == SETTINGS_B);
    save_cut(2, 0, 0, &hdr);
    reboot();
    check("wrap: then seq 1, to A", load_is() == 2 && set_seq == 1u && set_at == SETTINGS_AT);
    put(SETTINGS_AT, R[5], RN, 1, 7u);
    put(SETTINGS_B, R[6], RN, 1, 7u);
    reboot();
    check("the same seq in both: A", load_is() == 5);

    /* a corrupted newer record: the older one */
    {
        static const uint32_t AT[] = {0u, 4u, 8u, 9u, 12u, 140u, 267u, 268u, 1000u, 12u + RN - 1u, 12u + RN,
                                      12u + RN + 3u, 12u + RN + 4u, 12u + RN + 7u};
        for (i = 0; i < sizeof AT / sizeof AT[0]; i++) {
            reboot();
            put(SETTINGS_AT, R[0], RN, 1, 4u);
            put(SETTINGS_B, R[3], RN, 1, 9u);
            m_mem[SETTINGS_B + AT[i]] ^= 0x10u;     /* (in the sequence: B's seq 0, so A's 4 wins too) */
            snprintf(msg, sizeof msg, "a bit flipped at +%u of the newer record's sector: the other one", AT[i]);
            check(msg, load_is() == 0);
            save_cut(1, 0, 0, &hdr);
            reboot();
            check("and the next save loads", load_is() == 1);
        }
        reboot();
        put(SETTINGS_AT, R[0], RN, 1, 9u);
        put(SETTINGS_B, R[3], RN, 1, 4u);
        m_mem[SETTINGS_AT + 12u + RN + 2u] ^= 1u;
        check("the newer one's sequence corrupted (seq 0): the other one, its data good", load_is() == 3);
        reboot();
        put(SETTINGS_AT, R[0], RN, 1, 4u);
        put(SETTINGS_B, R[3], RN, 1, 9u);
        m_mem[SETTINGS_AT + 20u] ^= 1u;
        m_mem[SETTINGS_B + 2u] ^= 1u;
        check("both corrupted: none, the record as it was", load_is() == -1);
        save_cut(1, 0, 0, &hdr);
        reboot();
        check("then a save loads", load_is() == 1);
        save_cut(2, 0, 0, &hdr);
        m_mem[set_at + 500u] ^= 4u;
        check("the read-back sees a bad copy", settings_check() != 0);
    }

    /* the allow-list: the record's sectors; never the places before 0.9.6, nor the stock's regions */
    {
        uint32_t took;
        reboot();
        M.w_hi = 0;                             /* (the driver's own net decides here) */
        check("the record A, B writable", FL_GUARD(SETTINGS_AT, 0x1000u) && FL_GUARD(SETTINGS_B, 0x1000u));
        check("before A refused", fl_erase4k(SETTINGS_AT - 0x1000u, &took) == -1 && !M.busy);
        check("0.9.5's preset bank and pattern refused", fl_erase4k(OLD_PR_A, &took) == -1 &&
              fl_erase4k(OLD_PR_B, &took) == -1 && fl_erase4k(OLD_SQ_A, &took) == -1 && fl_erase4k(OLD_SQ_B, &took) == -1);
#if BOARD_SMK37
        check("SMK-37: the stock VM (0.9.5's settings, owner tag, staging) and USR refused",
              fl_erase4k(0x9D000u, &took) == -1 && fl_erase4k(OLD_OWNER_AT, &took) == -1 &&
              fl_erase4k(0xBB000u, &took) == -1 && fl_erase4k(0xF4000u, &took) == -1 && SETTINGS_AT == 0xEB000u);
#else
        check("FM-1: the store 0xFC000..0xFEFFF, 0xFF000 refused", SETTINGS_AT == 0xFC000u && SETTINGS_B == 0xFD000u &&
              fl_erase4k(0xFF000u, &took) == -1 && !M.busy);
#endif
    }

    printf(fails ? "set_ab: %d FAILED\n" : "set_ab: all ok\n", fails);
    return fails != 0;
}
