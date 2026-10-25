/* SPDX-License-Identifier: GPL-3.0-only */
/* The settings record's two sectors (firmware/src/set_store.c) on the flash chip model (tests/fl_host_model.h, as
 * tests/flash_split_test.c): a power cut at every call of a sliced save (the erase's and each program's slices), with
 * the cut operation's sector or page left as it was, done, or half done (random bits), from every start: a 0.9.4
 * record in A only, A newer, B newer, nothing valid. After the cut the load gives the old record or the new one,
 * never neither while one was valid, the new one only once its header was programmed; the next save works. Also the
 * first boot after the update (0.9.4's record read as it is, the first save to B, A kept for a downgrade), the
 * sequence's 32-bit wrap, a corrupted header / data / sequence, the sectors' allow-list. FM-1 and SMK-37 (one sector).
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

#define N 256u
static uint8_t R[8][N];                         /* records 0..7, each its own bytes */

static void reboot(void)                        /* power off and on: the flash stays, the RAM is new */
{
    m_reset(0x856014u, 1, 20000u, 1500u, 400u);
    fl_can_split = -1;
    memset(&fl_aop, 0, sizeof fl_aop);
    memset(&ss, 0, sizeof ss);
    set_at = SETTINGS_B; set_seq = 0;
}
static void put(uint32_t at, const uint8_t *r, int seq_on, uint32_t seq)   /* a sector as a save leaves it */
{
    uint32_t h[3] = {STORE_MAGIC, N, crc32(r, N)}, t[2] = {seq, ~seq};
    memset(m_mem + at, 0xFF, 4096u);
    memcpy(m_mem + at, h, 12u);
    memcpy(m_mem + at + 12u, r, N);
    if (seq_on) memcpy(m_mem + at + 12u + N, t, 8u);    /* (0.9.4's record: no sequence) */
}
static int load_is(void)                        /* the record the load gives: 0..7, -1 none, -2 rubbish */
{
    static uint8_t got[N];
    int i;
    memset(got, 0xEE, N);
    if (settings_load(got, N)) {
        for (i = 0; i < (int)N; i++) if (got[i] != 0xEE) return -2;   /* (a failed load leaves p as it was) */
        return -1;
    }
    for (i = 0; i < 8; i++) if (!memcmp(got, R[i], N)) return i;
    return -2;
}
/* a save of record r, cut after `cut` calls (0: none); mode: the cut operation's bytes 0 as before, 1 done, 2 half.
 * Returns the calls a whole save takes; *hdr = 1 once the header's program was started */
static uint32_t save_cut(int r, uint32_t cut, int mode, int *hdr)
{
    uint32_t calls = 0;
    int rc = settings_save_start(R[r], N);
    *hdr = 0;
    calls++;
    while (rc == 1 && (!cut || calls < cut)) {
        m_advance(400u * 24u);                  /* the rest of a main-loop pass */
        if (ss.step == 4u) *hdr = 1;
        rc = settings_save_poll();
        calls++;
    }
    if (ss.step == 4u) *hdr = 1;
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
    check("the save without a model violation", M.bad == 0);
    return calls;
}

int main(void)
{
    uint32_t i, cut, full = 0, cuts = 0;
    int st, mode, hdr;
    char msg[200];
    for (i = 0; i < 8u * N; i++) R[i / N][i % N] = (uint8_t)(i * 131u + 7u + i / N);
    srand(1);

    /* the first boot after the update: 0.9.4's record in A (no sequence), B as Felucca or the factory left it */
    {
        static const uint8_t B_WAS[] = {0xFF, 0x5A, 0x00};
        for (st = 0; st < 3; st++) {
            reboot();
            put(SETTINGS_AT, R[0], 0, 0);
            memset(m_mem + SETTINGS_B, B_WAS[st], 4096u);
            if (SETTINGS_B == SETTINGS_AT) put(SETTINGS_AT, R[0], 0, 0);
            snprintf(msg, sizeof msg, "migration (B 0x%02X): 0.9.4's record loads, seq 0, in A", B_WAS[st]);
            check(msg, load_is() == 0 && set_at == SETTINGS_AT && set_seq == 0);
            save_cut(1, 0, 0, &hdr);
            reboot();
            check("migration: the first save loads after a reboot, seq 1", load_is() == 1 && set_seq == 1u &&
                  set_at == SETTINGS_B);
            if (SETTINGS_B != SETTINGS_AT) {
                static uint8_t old[N];
                check("migration: the first save went to B, A still 0.9.4's record (a downgrade reads it)",
                      !area_load(SETTINGS_AT, SECTOR_MAX, STORE_MAGIC, old, N) && !memcmp(old, R[0], N));
            }
            save_cut(2, 0, 0, &hdr);
            reboot();
            check("migration: the second save, seq 2", load_is() == 2 && set_seq == 2u && set_at == SETTINGS_AT);
            {
                static uint8_t old[N];
                check("0.9.4's reader (area_load at A) takes the new format", SETTINGS_B != SETTINGS_AT ?
                      !area_load(SETTINGS_AT, SECTOR_MAX, STORE_MAGIC, old, N) && !memcmp(old, R[2], N) : 1);
            }
        }
    }

    /* a power cut at every call of a save, every start, three ways of leaving the cut operation */
    for (st = 0; st < 4; st++) {
        int old = st == 3 ? -1 : 0;
        uint32_t calls;
        /* the whole save's length from this start */
        for (cut = 1;; cut++) {
            for (mode = 0; mode < 3; mode++) {
                int got;
                reboot();
                memset(m_mem + SETTINGS_AT, 0xFF, 4096u);
                memset(m_mem + SETTINGS_B, 0xFF, 4096u);
                if (st == 0) put(SETTINGS_AT, R[0], 0, 0);
                if (st == 1) { put(SETTINGS_B, R[3], 1, 4u); put(SETTINGS_AT, R[0], 1, 5u); }
                if (st == 2) { put(SETTINGS_AT, R[3], 1, 6u); put(SETTINGS_B, R[0], 1, 7u); }
                if (SETTINGS_B == SETTINGS_AT && (st == 1 || st == 2)) put(SETTINGS_AT, R[0], 1, 5u);
                got = load_is();
                check("before the save: the newest record", got == old);
                calls = save_cut(1, cut, mode, &hdr);
                reboot();
                got = load_is();
                if (SETTINGS_B == SETTINGS_AT && old == 0) {   /* (one sector: the cut save loses it, as before) */
                    check("one sector: the old one, the new one or none", got == 0 || got == 1 || got == -1);
                } else {
                    snprintf(msg, sizeof msg, "start %d, cut at call %u, mode %d: the old record (%d) or the new, got %d",
                             st, cut, mode, old, got);
                    check(msg, got == old || got == 1);
                    snprintf(msg, sizeof msg, "start %d, cut at call %u, mode %d: the new one only after its header",
                             st, cut, mode);
                    check(msg, got != 1 || hdr);
                }
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

    /* the sequence's wrap: 0 is newer than 0xFFFFFFFF */
    if (SETTINGS_B != SETTINGS_AT) {
        reboot();
        put(SETTINGS_AT, R[0], 1, 0xFFFFFFFFu);
        put(SETTINGS_B, R[3], 1, 0xFFFFFFFEu);
        check("wrap: 0xFFFFFFFF over 0xFFFFFFFE", load_is() == 0 && set_at == SETTINGS_AT);
        save_cut(1, 0, 0, &hdr);
        reboot();
        check("wrap: the save after 0xFFFFFFFF is seq 0, to B, and newer", load_is() == 1 && set_seq == 0u &&
              set_at == SETTINGS_B);
        save_cut(2, 0, 0, &hdr);
        reboot();
        check("wrap: then seq 1, to A", load_is() == 2 && set_seq == 1u && set_at == SETTINGS_AT);
        put(SETTINGS_AT, R[5], 1, 7u);
        put(SETTINGS_B, R[6], 1, 7u);
        reboot();
        check("the same seq in both: A", load_is() == 5);

        /* a corrupted newer record: the older one */
        {
            static const uint32_t AT[] = {0u, 4u, 8u, 9u, 12u, 140u, 267u, 268u, 271u, 272u};
            for (i = 0; i < sizeof AT / sizeof AT[0]; i++) {
                reboot();
                put(SETTINGS_AT, R[0], 1, 4u);
                put(SETTINGS_B, R[3], 1, 9u);
                m_mem[SETTINGS_B + AT[i]] ^= 0x10u;     /* (in the sequence: B's seq 0, so A's 4 wins too) */
                snprintf(msg, sizeof msg, "a bit flipped at +%u of the newer record's sector: the other one", AT[i]);
                check(msg, load_is() == 0);
                save_cut(1, 0, 0, &hdr);
                reboot();
                check("and the next save loads", load_is() == 1);
            }
            reboot();
            put(SETTINGS_AT, R[0], 1, 9u);
            put(SETTINGS_B, R[3], 1, 4u);
            m_mem[SETTINGS_AT + 270u] ^= 1u;
            check("the newer one's sequence corrupted (seq 0): the other one, its data good", load_is() == 3);
            reboot();
            put(SETTINGS_AT, R[0], 1, 4u);
            put(SETTINGS_B, R[3], 1, 9u);
            m_mem[SETTINGS_AT + 20u] ^= 1u;
            m_mem[SETTINGS_B + 2u] ^= 1u;
            check("both corrupted: none, the record as it was", load_is() == -1);
            save_cut(1, 0, 0, &hdr);
            reboot();
            check("then a save loads", load_is() == 1);
        }

        /* the allow-list: both sectors, nothing next to them */
        {
            uint32_t took;
            reboot();
            check("settings A, B writable", FL_GUARD(SETTINGS_AT, 0x1000u) && FL_GUARD(SETTINGS_B, 0x1000u));
            check("0xFB000, 0xFE000 refused", fl_erase4k(SETTINGS_AT - 0x1000u, &took) == -1 &&
                  fl_erase4k(SETTINGS_B + 0x1000u, &took) == -1 && !M.busy);
            check("B outside the update loader's reach (< 0xFC000)", SETTINGS_B >= 0xFC000u && SETTINGS_B == 0xFD000u);
        }
    } else
        check("SMK-37: one sector", SETTINGS_B == 0x9D000u);

    printf(fails ? "set_ab: %d FAILED\n" : "set_ab: all ok\n", fails);
    return fails != 0;
}
