/* SPDX-License-Identifier: GPL-3.0-only */
/* A host model of the FM-1's flash path for tests/flash_split_test.c (fm1_flash.h with -DFL_HOST_MODEL): the
 * registers fm1_flash.h touches, the CPU's cli / sti, a 24 MHz timer and the SPI NOR part (a P25Q80H: 06 WREN, 05 / 35
 * RDSR, 9F RDID, 20 sector erase, 02 page program, 0B fast read, 75 suspend, 7A resume), with its rules checked: no
 * command but RDSR / suspend while an erase or a program runs, no XIP read (XIP on, an ISR) while one runs, only the
 * datasheet's commands while suspended, an erase given >= 200 us (a program 100 us) between a resume and the next
 * suspend. Time moves with every register access (the polls) and every SPI byte; with the interrupts on, the pending
 * ISRs run (the audio one every 5805 us, then the USB one every 1 ms: its wait is measured). */
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define RAMFN  __attribute__((noinline, used))
#define RAMINL static inline __attribute__((always_inline))

enum { R_SFC, R_SPICON, R_BUF, R_IOMAP, R_PDOUT, R_PDDIR, R_CACHE, R_T4, R_ENCH, R_ENCL, R_N };
static volatile uint32_t m_r[R_N];
static volatile uint8_t m_enc;
static volatile uint32_t *fl_m_reg(int r);

#define SFC_CON         (*fl_m_reg(R_SFC))
#define SFCENC_CON      m_enc
#define SFCENC_UNENC_H  m_r[R_ENCH]
#define SFCENC_UNENC_L  m_r[R_ENCL]
#define SPI0_CON        (*fl_m_reg(R_SPICON))
#define SPI0_BUF        (*fl_m_reg(R_BUF))
#define IOMAP_CON0      (*fl_m_reg(R_IOMAP))
#define PORTD_OUT       (*fl_m_reg(R_PDOUT))
#define PORTD_DIR       (*fl_m_reg(R_PDDIR))
#define CACHE_CON       (*fl_m_reg(R_CACHE))
#define T4_CNT          (*fl_m_reg(R_T4))
#define FL_CSYNC()      ((void)0)
#define FL_FLUSHINV(a)  ((void)(a))
#define FL_CLI()        m_cli()
#define FL_STI()        m_sti()

/* ---- the part ---- */
static struct {
    uint64_t now;                     /* ticks (24 / us) */
    uint32_t id;                      /* RDID */
    int can_suspend;                  /* 0: 75h / 7Ah ignored (a part without suspend) */
    int sh;                           /* the SH variant: one SUS bit (S15) for both, S10 = EP_FAIL (sticky here) */
    uint32_t erase_ticks, prog_ticks, sus_lat;
    int wel, busy, op;                /* op: 1 erase, 2 program */
    int64_t left;                     /* the operation's remaining active time */
    int suspended; uint64_t sus_at;   /* a suspend pending (busy, not yet suspended) at sus_at */
    uint64_t resumed_at; int resumed;
    uint32_t op_addr, op_n; uint8_t op_data[256];
    uint8_t cmd[300]; uint32_t ncmd, nrx; int cs;
    uint32_t suspends, resumes, short_runs, bad;  /* short_runs: a resume-to-suspend run under the progress minimum */
    int irq_off; uint64_t off_t0, off_max;        /* the longest cli .. sti */
    uint64_t isr_ticks;                           /* the audio ISR: every 5805 us, this long (from XIP) */
    uint64_t aud_next, usb_next, usb_t, usb_lat_max;   /* the USB ISR: every 1 ms, 20 us, after the audio ISR when both
                                                        * are pending; usb_lat_max = its longest wait (ticks) */
    int aud_pend, usb_pend, in_isr;
    uint32_t isrs;
    uint64_t isr_time;                            /* the ISRs' time in all (ticks) */
} M;
static uint8_t m_mem[0x100000];

static void m_bad(const char *why)
{
    if (M.bad++ < 8) printf("model: %s (t %llu us)\n", why, (unsigned long long)(M.now / 24u));
}
static void m_bad(const char *why);
static void m_isrs(void);
static void m_advance(uint64_t dt)
{
    while (dt--) {
        M.now++;
        if (M.isr_ticks && M.now >= M.aud_next) { M.aud_pend = 1; M.aud_next += 5805u * 24u; }
        if (M.isr_ticks && M.now >= M.usb_next) { if (!M.usb_pend) M.usb_t = M.now; M.usb_pend = 1; M.usb_next += 24000u; }
        if (!M.irq_off && !M.in_isr && (M.aud_pend || M.usb_pend)) m_isrs();
        if (!M.busy || M.suspended) continue;
        if (M.sus_at && M.now >= M.sus_at) {      /* the suspend takes */
            uint64_t run = M.now - M.resumed_at;
            if (M.resumed && run < (M.op == 1 ? 200u : 100u) * 24u) M.short_runs++;
            M.suspended = 1; M.sus_at = 0; M.wel = 0; M.suspends++;
            continue;
        }
        if (--M.left <= 0) {                      /* done */
            uint32_t i;
            if (M.op == 1) memset(m_mem + M.op_addr, 0xFF, 4096u);
            else for (i = 0; i < M.op_n; i++) m_mem[(M.op_addr & ~0xFFu) + ((M.op_addr + i) & 0xFFu)] &= M.op_data[i];
            M.busy = 0; M.wel = 0; M.sus_at = 0; M.op = 0;
        }
    }
}
static int m_running(void) { return M.busy && !M.suspended; }
static void m_xip_check(const char *what) { if (m_running()) m_bad(what); }
static void m_isrs(void)                          /* the pending ISRs, from XIP: the audio one first */
{
    uint64_t t0 = M.now;
    M.in_isr = 1;
    while (M.aud_pend || M.usb_pend) {
        if (!m_r[R_SFC]) m_bad("the interrupts on with XIP off");
        m_xip_check("an ISR ran (XIP) while the part was busy");
        if (M.aud_pend) { M.aud_pend = 0; m_advance(M.isr_ticks); }
        else {
            if (M.now - M.usb_t > M.usb_lat_max) M.usb_lat_max = M.now - M.usb_t;
            M.usb_pend = 0; m_advance(20u * 24u);
        }
        M.isrs++;
    }
    M.in_isr = 0;
    M.isr_time += M.now - t0;
}
static uint32_t m_addr(void) { return (uint32_t)M.cmd[1] << 16 | (uint32_t)M.cmd[2] << 8 | M.cmd[3]; }
static void m_exec(void)                          /* CS# high: the command ends */
{
    uint8_t c = M.cmd[0];
    if (!M.ncmd) return;
    if (m_running() && c != 0x05 && c != 0x35 && c != 0x75) { m_bad("a command while the part is busy"); return; }
    if (M.suspended && !(c == 0x05 || c == 0x35 || c == 0x0B || c == 0x9F || c == 0x7A)) {
        m_bad("a command the suspended part does not take"); return;
    }
    switch (c) {
    case 0x06: M.wel = 1; break;
    case 0x20:
        if (!M.wel || M.ncmd != 4) { m_bad("an erase without WREN or with a bad address"); break; }
        M.op = 1; M.op_addr = m_addr() & ~0xFFFu; M.busy = 1; M.left = M.erase_ticks; M.resumed = 0; M.resumed_at = M.now;
        break;
    case 0x02:
        if (!M.wel || M.ncmd < 5) { m_bad("a program without WREN or data"); break; }
        M.op = 2; M.op_addr = m_addr(); M.op_n = M.ncmd - 4u; memcpy(M.op_data, M.cmd + 4, M.op_n);
        M.busy = 1; M.left = M.prog_ticks; M.resumed = 0; M.resumed_at = M.now;
        break;
    case 0x75:
        if (M.can_suspend && m_running() && !M.sus_at) M.sus_at = M.now + M.sus_lat;
        break;
    case 0x7A:
        if (M.can_suspend && M.suspended) { M.suspended = 0; M.wel = 1; M.resumes++; M.resumed = 1; M.resumed_at = M.now; }
        break;
    }
}
static uint8_t m_rx(void)
{
    uint32_t k = M.nrx++;
    switch (M.cmd[0]) {
    case 0x05: return (uint8_t)((M.busy && !M.suspended) | (M.wel ? 2u : 0u));
    case 0x35: return (uint8_t)((M.suspended ? (M.op == 1 || M.sh ? 0x80u : 0x04u) : 0u) | (M.sh ? 0x04u : 0u));
                                                /* (Puya's note: an erase S15; the H's program S10, the SH's S15) */
    case 0x9F: return (uint8_t)(M.id >> (16u - 8u * (k < 3u ? k : 2u)));
    case 0x0B: return m_mem[(m_addr() + k) & 0xFFFFFu];
    }
    return 0xFF;
}
static void m_sync(void)
{
    int cs = (int)(m_r[R_PDOUT] & 1u);
    if (M.cs && !cs) { M.ncmd = 0; M.nrx = 0; }
    if (!M.cs && cs) m_exec();
    M.cs = cs;
    if (m_r[R_SPICON] & 0x4000u) m_r[R_SPICON] &= ~0xC000u;    /* PCLR */
    if (m_r[R_BUF] < 0x100u) {                                  /* a byte written: shift it */
        uint32_t b = m_r[R_BUF], v = 0xFFu;
        if (m_r[R_SFC]) m_bad("SPI traffic with XIP on");
        if (!cs) {
            if (m_r[R_SPICON] & 0x1000u) v = m_rx();
            else if (M.ncmd < sizeof M.cmd) M.cmd[M.ncmd++] = (uint8_t)b;
        }
        m_r[R_BUF] = 0x100u | v;
        m_r[R_SPICON] |= 0x8000u;
        m_advance(12);                                          /* ~0.5 us a byte */
    }
}
static volatile uint32_t *fl_m_reg(int r)
{
    m_sync();
    m_advance(2);
    if (r == R_T4) m_r[R_T4] = (uint32_t)M.now;
    if (r == R_CACHE) m_r[R_CACHE] = 0x4000u;
    return &m_r[r];
}
static void m_cli(void) { M.irq_off = 1; M.off_t0 = M.now; }
static void m_sti(void)
{
    m_sync();
    if (M.irq_off && M.now - M.off_t0 > M.off_max) M.off_max = M.now - M.off_t0;
    M.irq_off = 0;
    m_advance(1);                                               /* the pending ISRs run */
}
static void m_reset(uint32_t id, int can_suspend, uint32_t erase_us, uint32_t prog_us, uint32_t isr_us)
{
    memset((void *)&M, 0, sizeof M);
    memset((void *)m_r, 0, sizeof m_r);
    M.id = id; M.can_suspend = can_suspend;
    M.erase_ticks = erase_us * 24u; M.prog_ticks = prog_us * 24u; M.sus_lat = 20u * 24u;
    M.isr_ticks = (uint64_t)isr_us * 24u;
    M.aud_next = 1234u * 24u; M.usb_next = 321u * 24u;
    M.cs = 1;
    m_r[R_PDOUT] = 1u; m_r[R_BUF] = 0x100u;
    m_r[R_SFC] = 0x0200u;                                       /* XIP on, read mode 2 (not continuous) */
}
