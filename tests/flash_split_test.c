/* SPDX-License-Identifier: GPL-3.0-only */
/* The split flash erase / program (firmware/hal/fm1_flash.h fl_split_op) on a host model of the part
 * (tests/fl_host_model.h): every erase and page program the app makes (the settings, the presets, the pattern) holds
 * the interrupts off for at most FL_SLICE_US + 100 us, keeps the USB ISR's wait (a 1 ms event, served after a silent
 * 400 us audio ISR every 5805 us) <= 700 us (500 / 50 us slices: 901, missed packets on the FM-1), never runs the
 * part while XIP is on or an ISR runs, gives an
 * erase >= 200 us (a program >= 100 us) between a resume and the next suspend, and leaves the right bytes. Erase
 * times 1..30 ms and program times 0.1..3 ms in odd steps (a suspend that meets the operation's end), a part that
 * ignores the suspend and one not known to suspend (the old blocking erase), and the allow-list. The same save
 * beside the main loop (fl_op_start / fl_op_poll): no call longer than two slices, the loop's XIP only while suspended,
 * a blocking save meanwhile finishes it first. Build:
 *   cc -O1 -Wall -Wno-unused-function -Ifirmware/hal -o build/flash_split_test tests/flash_split_test.c */
#define FL_HOST_MODEL "../../tests/fl_host_model.h"
#include "../firmware/src/frodhes_store.h"
#include "fm1_flash.h"

static int fails;
static void check(const char *what, int ok)
{
    if (!ok) { printf("FAIL  %s\n", what); fails++; }
}
#define OFF_LIMIT_US (FL_SLICE_US + 100u)

static void fresh(uint32_t id, int can, uint32_t erase_us, uint32_t prog_us, uint32_t isr_us)
{
    m_reset(id, can, erase_us, prog_us, isr_us);
    fl_can_split = -1;
    fl_off_max_us = fl_erase_max_us = fl_wall_max_us = 0;
    fl_sus_seen = 0;
}

static int rd(uint32_t at, uint8_t *d, uint32_t n)   /* as platform.c st_read: the interrupts off */
{
    int r;
    uint64_t keep = M.off_max;
    m_cli(); r = fl_read_ram(at, d, n); m_sti();
    M.off_max = keep;                                   /* (a read is not a flash write) */
    return r;
}
static int erased(uint32_t at)
{
    uint32_t i;
    for (i = 0; i < 4096u; i++) if (m_mem[at + i] != 0xFFu) return 0;
    return 1;
}

int main(void)
{
    static uint8_t rec[268], back[268];
    uint32_t e, p, i, took, worst_off = 0, worst_block = 0, n_sus = 0, worst_usb = 0;
    char msg[160];
    int rc;
    for (i = 0; i < sizeof rec; i++) rec[i] = (uint8_t)(i * 37u + 5u);

    /* the settings save as platform.c area_save makes it: erase, the data (two pages), the header */
    for (e = 1000u; e <= 30000u; e += 997u) {
        for (p = 100u; p <= 3000u; p += 731u) {
            uint64_t t0;
            fresh(0x856014u, 1, e, p, 400u);         /* the audio ISR when silent (the FM-1: 384-388 us) */
            memset(m_mem + SETTINGS_AT, 0x5A, 4096u);
            t0 = M.now;
            rc = fl_erase4k(SETTINGS_AT, &took);
            snprintf(msg, sizeof msg, "erase %u us: rc %d, erased", e, rc);
            check(msg, rc == 0 && erased(SETTINGS_AT) && !M.busy);
            rc = fl_write(SETTINGS_AT + 12u, rec + 12, 256u);
            if (!rc) rc = fl_write(SETTINGS_AT, rec, 12u);
            snprintf(msg, sizeof msg, "erase %u us program %u us: the writes rc %d", e, p, rc);
            check(msg, rc == 0 && !M.busy);
            check("read back through 0B", rd(SETTINGS_AT, back, sizeof back) == 0 && !memcmp(back, rec, sizeof rec));
            snprintf(msg, sizeof msg, "erase %u program %u: the interrupts off %llu us (model) / %u us (fl_off_max_us)",
                     e, p, (unsigned long long)(M.off_max / 24u), fl_off_max_us);
            check(msg, M.off_max / 24u <= OFF_LIMIT_US && fl_off_max_us <= OFF_LIMIT_US);
            snprintf(msg, sizeof msg, "erase %u program %u: %u model violations, %u runs under the minimum", e, p,
                     M.bad, M.short_runs);
            check(msg, M.bad == 0 && M.short_runs == 0);
            if (M.off_max / 24u > worst_off) worst_off = (uint32_t)(M.off_max / 24u);
            if ((M.now - t0) / 24u > worst_block) worst_block = (uint32_t)((M.now - t0) / 24u);
            if (M.usb_lat_max / 24u > worst_usb) worst_usb = (uint32_t)(M.usb_lat_max / 24u);
            n_sus += M.suspends;
            if (M.suspends) check("the suspend bits seen", fl_sus_seen != 0);
            snprintf(msg, sizeof msg, "erase %u us: its own time %u us, start to end %u us", e, fl_erase_max_us,
                     fl_wall_max_us);
            check(msg, fl_erase_max_us + 50u >= e && fl_erase_max_us <= e + 600u && fl_wall_max_us >= e);
            check("resumes match the suspends", M.resumes == M.suspends);
        }
    }
    check("the long erases were suspended", n_sus > 0);
    /* the USB ISR's wait: a slice, then the silent audio ISR first (the FM-1's 6e598d0, 500 / 50 us: ~930 us here,
     * missed 12 / overruns 7 on the device); the design keeps it a third of a USB frame clear */
    snprintf(msg, sizeof msg, "the USB ISR's longest wait %u us (<= 700)", worst_usb);
    check(msg, worst_usb <= 700u);
    printf("split: worst interrupts-off %u us (limit %u), the USB ISR's worst wait %u us, worst save %u ms, %u suspends\n",
           worst_off, OFF_LIMIT_US, worst_usb, worst_block / 1000u, n_sus);

    /* the same save beside the main loop (fm1_flash.h fl_op_start / fl_op_poll, as platform.c settings_save_poll
     * makes it: the next step as one ends): no call holds the loop longer than two slices, the loop runs from XIP between
     * the calls only while the part is suspended or done, and the bytes are right. Main-loop passes of 50 us .. 3 ms */
    {
        static const uint32_t PASS_US[] = {50u, 400u, 1300u, 3000u};
        uint32_t q, worst_call = 0, calls_max = 0;
        for (q = 0; q < sizeof PASS_US / sizeof PASS_US[0]; q++)
            for (e = 1000u; e <= 30000u; e += 2311u) {
                uint32_t step = 0, calls = 0;
                fresh(0x856014u, 1, e, 1700u, 400u);
                memset(m_mem + SETTINGS_AT, 0x5A, 4096u);
                for (;;) {
                    uint64_t c0 = M.now, i0 = M.isr_time;
                    int r;
                    if (step == 0) { r = fl_op_start(SETTINGS_AT, 0, 0); step = 1; }
                    else if ((r = fl_op_poll()) == 0) {        /* (as platform.c settings_save_poll) */
                        if (step == 1) r = fl_op_start(SETTINGS_AT + 12u, rec + 12, 244u);
                        else if (step == 2) r = fl_op_start(SETTINGS_AT + 256u, rec + 256, 12u);
                        else if (step == 3) r = fl_op_start(SETTINGS_AT, rec, 12u);
                        else break;
                        step++;
                    }
                    calls++;
                    c0 = (M.now - c0) - (M.isr_time - i0);    /* (the ISRs that ran meanwhile: not the call's) */
                    if (c0 / 24u > worst_call) worst_call = (uint32_t)(c0 / 24u);
                    if (r < 0) { check("an async step failed", 0); break; }
                    m_xip_check("the main loop ran (XIP) while the part was busy");
                    m_advance((uint64_t)PASS_US[q] * 24u);       /* the rest of the main loop's pass */
                    if (calls > 200000u) { check("the async save ends", 0); break; }
                }
                if (calls > calls_max) calls_max = calls;
                snprintf(msg, sizeof msg, "async, pass %u us, erase %u us: no violation, the bytes, done", PASS_US[q], e);
                check(msg, M.bad == 0 && M.short_runs == 0 && !M.busy && !fl_aop.busy &&
                      rd(SETTINGS_AT, back, sizeof back) == 0 && !memcmp(back, rec, sizeof rec));
                snprintf(msg, sizeof msg, "async, pass %u us, erase %u us: interrupts off %llu us", PASS_US[q], e,
                         (unsigned long long)(M.off_max / 24u));
                check(msg, M.off_max / 24u <= OFF_LIMIT_US);
            }
        snprintf(msg, sizeof msg, "async: the longest call %u us (<= 2 x %u: a step's end, the next's start)",
                 worst_call, OFF_LIMIT_US);
        check(msg, worst_call <= 2u * OFF_LIMIT_US);
        printf("async: the longest call (the main loop held, the ISRs not counted) %u us, up to %u calls a save\n",
               worst_call, calls_max);

        /* a blocking save (a preset, the pattern) while an erase is suspended: that one finishes first */
        fresh(0x856014u, 1, 20000u, 1500u, 400u);
        memset(m_mem + SETTINGS_AT, 0x5A, 4096u);
        memset(m_mem + SQ_A, 0x5A, 4096u);
        rc = fl_op_start(SETTINGS_AT, 0, 0);
        check("async: the erase suspended after its first slice", rc == 1 && M.suspended);
        rc = fl_erase4k(SQ_A, &took);
        check("a blocking erase meanwhile: both erased, no violation", rc == 0 && !fl_aop.busy && erased(SETTINGS_AT) &&
              erased(SQ_A) && M.bad == 0);
        /* an async erase that fails (a part that never finishes: -4 at the blocking end) is finished by another's
         * start: its result is kept for its owner's next poll, once (it does not look done) */
        fresh(0x856014u, 1, 2000000u, 1500u, 400u);
        memset(m_mem + SETTINGS_AT, 0x5A, 4096u);
        rc = fl_op_start(SETTINGS_AT, 0, 0);
        {
            int rp = fl_op_start(SQ_A, rec, 16u), r1, r2;   /* (finishes the erase first) */
            while (rp == 1) rp = fl_op_poll();
            r1 = fl_op_poll(); r2 = fl_op_poll();
            check("a failed async erase finished by another's start: its error for the owner, once",
                  rc == 1 && r1 < 0 && r2 == 0);
        }
        /* a part that does not suspend: all in fl_op_start, as before */
        fresh(0x856014u, 0, 20000u, 1500u, 400u);
        memset(m_mem + SETTINGS_AT, 0x5A, 4096u);
        rc = fl_op_start(SETTINGS_AT, 0, 0);
        check("async, no suspend: done in fl_op_start", rc == 0 && erased(SETTINGS_AT) && !fl_aop.busy && M.bad == 0);
    }

    /* the SH variant with its EP_FAIL (S10) set from an earlier fault: the suspends still wait for S15 */
    fresh(0x856014u, 1, 20000u, 2500u, 150u);
    M.sh = 1;
    memset(m_mem + SETTINGS_AT, 0x5A, 4096u);
    rc = fl_erase4k(SETTINGS_AT, &took);
    if (!rc) rc = fl_write(SETTINGS_AT + 12u, rec + 12, 256u);
    if (!rc) rc = fl_write(SETTINGS_AT, rec, 12u);
    check("SH variant, S10 set: rc 0, no violation, the bytes", rc == 0 && M.bad == 0 && M.short_runs == 0 &&
          rd(SETTINGS_AT, back, sizeof back) == 0 && !memcmp(back, rec, sizeof rec));
    check("SH variant: the interrupts off within the limit", M.off_max / 24u <= OFF_LIMIT_US && M.suspends > 0);

    /* a part that ignores 75h: the old blocking erase, still correct */
    fresh(0x856014u, 0, 24000u, 2000u, 150u);
    memset(m_mem + SETTINGS_AT, 0x5A, 4096u);
    rc = fl_erase4k(SETTINGS_AT, &took);
    check("no suspend on the part: the erase completes blocking", rc == 0 && erased(SETTINGS_AT) && M.bad == 0 &&
          M.off_max / 24u >= 24000u && fl_erase_max_us >= 23900u);
    printf("no suspend: interrupts off %llu us (blocking, as before)\n", (unsigned long long)(M.off_max / 24u));

    /* a part not known to suspend (another JEDEC id): no 75h sent, blocking */
    fresh(0xEB6014u, 1, 24000u, 2000u, 150u);
    memset(m_mem + SETTINGS_AT, 0x5A, 4096u);
    rc = fl_erase4k(SETTINGS_AT, &took);
    check("another part: blocking, never suspended", rc == 0 && erased(SETTINGS_AT) && M.suspends == 0 && M.bad == 0);

    /* the allow-list: outside the store refused, nothing sent */
    fresh(0x856014u, 1, 24000u, 2000u, 150u);
    check("erase outside the allow-list refused", fl_erase4k(0x10000u, &took) == -1 && !M.busy);
    check("program outside the allow-list refused", fl_write(0x10000u, rec, 16u) == -1 && !M.busy);
    check("erase not sector aligned refused", fl_erase4k(SETTINGS_AT + 0x100u, &took) == -1);

    printf(fails ? "flash split: %d FAILED\n" : "flash split: all ok\n", fails);
    return fails != 0;
}
