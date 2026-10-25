/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* FM-1 SPI NOR at runtime: erase / program / read. Used only when
 * FELUCCA_FLASH is 1.
 *
 * Everything that executes while the SFC (XIP) is switched off lives in
 * .ram_text and touches only SFRs, the stack and RAM buffers: no .rodata, no
 * libcalls, no XIP-resident helpers. Call the fl_*_ram() entry points with
 * interrupts disabled. Same sequence as the AC79 SDK flash driver
 * (enter_spi_code, norflash_erase, norflash_wait_ok, exit_spi_code).
 */
#pragma once
#include <stdint.h>
#include "fm1_xip.h"

/* XIP (0x02xxxxxx) -> RAM (0x01C0xxxx) is beyond the 23-bit call range:
 * call RAM code through a pointer the compiler cannot fold back. */
static inline void *fl_far(void *p) { void *volatile q = p; return q; }
#define FL_FAR(fn) ((__typeof__(&fn))fl_far((void *)&fn))

#ifdef FL_HOST_MODEL
#define FL_OWN_BSS(name)
#include FL_HOST_MODEL                             /* (tests/flash_split_test.c: the registers, the CPU's instructions
                                                    * and the SPI part as a host model) */
#else
#define RAMFN  __attribute__((section(".ram_text"), noinline, used))
#define RAMINL static inline __attribute__((always_inline))
#define FL_OWN_BSS(name) __attribute__((section(".bss." #name)))   /* out of the compiler's global merge (its new
                                                   * variables moved the ISR's addressing: +84 B in app_audio) */

#define REG32(a) (*(volatile uint32_t *)(a))
#define SFC_CON         REG32(0x40200u)            /* bit31 busy; [11:8] read mode */
#define SFCENC_CON      (*(volatile uint8_t *)0x40300u)  /* b0 decrypt, b1 plain window */
#define SFCENC_UNENC_H  REG32(0x40308u)            /* WO, XIP (CPU) address */
#define SFCENC_UNENC_L  REG32(0x4030Cu)            /* WO, XIP (CPU) address */
#define SPI0_CON        REG32(0x11C00u)            /* b15 PND, b14 PCLR, b12 DIR=rx */
#define SPI0_BUF        REG32(0x11C08u)
#define IOMAP_CON0      REG32(0x5101Cu)            /* b5: 1 = SFC owns the flash pins */
#define PORTD_OUT       REG32(0x500C0u)            /* PD0 = flash CS# */
#define PORTD_DIR       REG32(0x500C8u)
#define CACHE_CON       REG32(0x1EEE008u)          /* b14 = cache idle */
#define T4_CNT          REG32(0x10804u)            /* TIMER4, 24 MHz free-running */
#define FL_CSYNC()      __asm__ volatile("csync" ::: "memory")
#define FL_FLUSHINV(a)  __asm__ volatile("flushinv [%0]" :: "r"(a) : "memory")
#define FL_CLI()        __asm__ volatile("cli" ::: "memory")
#define FL_STI()        __asm__ volatile("csync\n\tsti" ::: "memory")
#endif

#define FL_XIP(off)     FM1_XIP(off)              /* fm1_xip.h */
#include "fm1_flash_map.h"                  /* FL_IN, FL_STORE_OK, FL_RANGE_OK, FL_GUARD, FL_JEDEC_OK; per board */

typedef struct { uint32_t sfc_con, spi_con; } fl_saved_t;

RAMINL void fl_csync(void) { FL_CSYNC(); }
RAMINL void fl_cache_idle(void) { while (!(CACHE_CON & 0x4000u)) ; }
RAMINL void fl_delay(uint32_t n) { volatile uint32_t i = n; while (i--) ; }

RAMINL void fl_cs(int high)
{
    PORTD_DIR &= ~1u;
    if (high) PORTD_OUT |= 1u; else PORTD_OUT &= ~1u;
    fl_delay(10);
}
RAMINL int fl_spi_wait(void)
{
    uint32_t n = 50000000u;
    fl_csync();
    while (!(SPI0_CON & 0x8000u))
        if (--n == 0) break;
    SPI0_CON |= 0x4000u;
    return n ? 0 : -1;
}
RAMINL void fl_tx(uint8_t b)
{
    SPI0_CON |= 8u; SPI0_CON &= ~0x1000u;
    SPI0_BUF = b; fl_spi_wait();
    SPI0_CON &= ~8u;
}
RAMINL uint8_t fl_rx(void)
{
    uint8_t v;
    SPI0_CON |= 8u; SPI0_CON |= 0x1000u;
    SPI0_BUF = 0xFFu; fl_spi_wait();
    v = (uint8_t)SPI0_BUF;
    SPI0_CON &= ~8u;
    return v;
}
RAMINL void fl_addr(uint32_t a) { fl_tx((uint8_t)(a >> 16)); fl_tx((uint8_t)(a >> 8)); fl_tx((uint8_t)a); }
RAMINL uint8_t fl_rdsr(uint8_t cmd) { uint8_t v; fl_cs(0); fl_tx(cmd); v = fl_rx(); fl_cs(1); return v; }
RAMINL void fl_wren(void) { fl_cs(0); fl_tx(0x06); fl_cs(1); }

RAMINL int fl_enter(fl_saved_t *s)
{
    uint32_t mode;
    fl_cache_idle();
    s->sfc_con = SFC_CON;
    s->spi_con = SPI0_CON;
    mode = (s->sfc_con >> 8) & 0xFu;
    if (mode == 6u || mode == 7u)                 /* continuous-read XIP: not handled here */
        return -1;
    fl_csync();
    while ((int32_t)SFC_CON < 0) ;                /* SFC idle */
    SFC_CON = 0;                                  /* XIP OFF from here on */
    IOMAP_CON0 &= ~0x20u;                         /* pins -> SPI0 / GPIO */
    SPI0_CON = 0;
    SPI0_CON = 0x1029u;                           /* SDK default */
    fl_csync();
    return 0;
}
RAMINL void fl_exit(const fl_saved_t *s)
{
    SPI0_CON = s->spi_con;
    IOMAP_CON0 |= 0x20u;
    fl_csync();
    while ((int32_t)SFC_CON < 0) ;
    SFC_CON = s->sfc_con;                         /* XIP back on */
    fl_csync();
}
RAMINL void fl_inval(uint32_t off, uint32_t len)
{
    uint32_t a = FL_XIP(off) & ~31u, e = FL_XIP(off) + len;
    fl_cache_idle();
    for (; a < e; a += 32u) {
        fl_csync();
        FL_FLUSHINV(a);
        fl_csync();
        fl_cache_idle();
    }
}
RAMINL int fl_wait_ready(uint32_t max_us, uint32_t *took_us)
{
    uint32_t t0 = T4_CNT, dt;
    for (;;) {
        dt = (T4_CNT - t0) / 24u;
        if (!(fl_rdsr(0x05) & 1u)) { *took_us = dt; return 0; }
        if (dt > max_us) { *took_us = dt; return -4; }
    }
}

/* ---- entry points: IRQs OFF, args/buffers in RAM ---- */

static RAMFN uint32_t fl_jedec_ram(void)                 /* expect 0x856014 (FL_JEDEC_OK) */
{
    fl_saved_t s; uint32_t id;
    if (fl_enter(&s)) return 0;
    fl_cs(0); fl_tx(0x9F);
    id = (uint32_t)fl_rx() << 16; id |= (uint32_t)fl_rx() << 8; id |= fl_rx();
    fl_cs(1);
    fl_exit(&s);
    return id;
}

static RAMFN uint32_t fl_status_ram(void)                /* SR1 | SR2 << 8 */
{
    fl_saved_t s; uint32_t v;
    if (fl_enter(&s)) return 0xFFFFFFFFu;
    v = fl_rdsr(0x05) | ((uint32_t)fl_rdsr(0x35) << 8);
    fl_exit(&s);
    return v;
}

static RAMFN int fl_erase4k_ram(uint32_t off, uint32_t *took_us)
{
    fl_saved_t s; int rc;
    if (!FL_GUARD(off, 0x1000u) || (off & 0xFFFu))
        return -1;
    if (fl_enter(&s)) return -2;
    fl_wren();
    if (!(fl_rdsr(0x05) & 2u)) rc = -3;           /* WEL did not set */
    else {
        fl_cs(0); fl_tx(0x20); fl_addr(off); fl_cs(1);
        fl_inval(off, 4096u);
        rc = fl_wait_ready(500000u, took_us);     /* generic NOR max 400 ms */
    }
    fl_exit(&s);
    return rc;
}

static RAMFN int fl_prog_ram(uint32_t off, const uint8_t *src, uint32_t n, uint32_t *took_us)
{
    fl_saved_t s; int rc; uint32_t i;
    if (!FL_GUARD(off, n) ||
        n == 0 || n > 256u || ((off & 0xFFu) + n) > 256u) return -1;    /* one page, no wrap */
    if (fl_enter(&s)) return -2;
    fl_wren();
    if (!(fl_rdsr(0x05) & 2u)) rc = -3;
    else {
        fl_cs(0); fl_tx(0x02); fl_addr(off);
        for (i = 0; i < n; i++) fl_tx(src[i]);
        fl_cs(1);
        fl_inval(off, n);
        rc = fl_wait_ready(10000u, took_us);      /* generic NOR max 3 ms */
    }
    fl_exit(&s);
    return rc;
}

static RAMFN int fl_read_ram(uint32_t off, uint8_t *dst, uint32_t n)   /* 0x0B fast read */
{
    fl_saved_t s; uint32_t i;
    if (fl_enter(&s)) return -2;
    fl_cs(0); fl_tx(0x0B); fl_addr(off); fl_tx(0xFF);
    for (i = 0; i < n; i++) dst[i] = fl_rx();
    fl_cs(1);
    fl_exit(&s);
    return 0;
}

#ifndef FELUCCA_LOADER
/* ---- the split erase / program (the app only; the update loader runs with the interrupts off anyway) ----
 * A 4 KB erase is ~8-24 ms and a page program up to ~3 ms with XIP off, so with the interrupts off that long no USB
 * audio packet goes out. The FM-1's P25Q80H (JEDEC 0x856014) suspends either (75h; resume 7Ah; datasheet 10.36-10.38):
 * suspend latency <= 30 us, then a suspend bit in SR2 (35h): the datasheet's SR table and its text disagree on which
 * (bit 7 / bit 2). Puya's P25Q40H -> SH migration note: an erase suspend sets bit 7 on both variants; a program
 * suspend bit 2 on the H, bit 7 on the SH (whose bit 2 is EP_FAIL). So an erase waits for bit 7, a program for either
 * bit that was clear before the suspend (fl_sus_seen keeps what the part set, for the CC 116 reply). While suspended every
 * read command works anywhere but the sector / page in progress, so the ISRs can run from XIP. Progress needs >= 200
 * us (erase) / 100 us (program) between a resume and the next suspend. So: FL_SLICE_US of the operation with the
 * interrupts off, suspend, XIP back on, the interrupts on for FL_GAP_US, off, XIP off, resume, repeat. No suspend bit
 * within FL_SUS_US (another chip): the old blocking wait. private/flash/P25Q80H-suspend-notes.md */
#define FL_SUSPEND_OK(id) ((id) == 0x856014u)     /* the parts known to suspend */
#define FL_SLICE_US   250u                        /* the operation's time per slice (interrupts off; >= 200 us for an
                                                   * erase to progress, counted from the interrupts going off) */
#define FL_GAP_US     750u                        /* the interrupts on between slices, the ISRs' time included. The
                                                   * FM-1, 6e598d0 (500 / 50 us): missed 12 / overruns 7 while an erase
                                                   * ran: a USB event in a slice waited the slice and then the audio ISR
                                                   * (~400 us when silent, run first in the gap), ~1 ms, a whole USB
                                                   * frame. Now a slice and the audio ISR stay well under one frame,
                                                   * and every frame has interrupts on for most of it */
#define FL_SUS_US     200u                        /* the suspend's latency, at most (datasheet: 30 us) */
#define FL_SLICES_MAX 800u                        /* then the rest blocking (200 ms of erase; the H's max 12 ms, the
                                                   * SH's 30) */
#define FL_SUS_ERASE  0x80u                       /* SR2: S15 */
#define FL_SUS_PROG   0x84u                       /* SR2: S15 / S10 (see above) */
static uint8_t fl_sus_seen;                       /* the SR2 suspend bits seen set (bit 7 erase / program, bit 2 ..) */

/* run the operation in progress until slice_us after t0 (the interrupts went off; 0: to its end), then suspend it.
 * 0 done, 1 suspended, -4 timeout */
RAMINL int fl_slice(uint32_t t0, uint32_t slice_us, uint8_t sus, uint32_t *took_us)
{
    uint32_t dt;
    int rc;
    if (!slice_us) {
        rc = fl_wait_ready(500000u, &dt);
        *took_us += dt;
        return rc;
    }
    for (;;) {
        dt = (T4_CNT - t0) / 24u;
        if (!(fl_rdsr(0x05) & 1u)) { *took_us += dt; return 0; }
        if (dt >= slice_us) break;
    }
    *took_us += dt;
    sus &= (uint8_t)~fl_rdsr(0x35);               /* a bit already set is not this suspend (the SH's EP_FAIL is S10) */
    fl_cs(0); fl_tx(0x75); fl_cs(1);
    t0 = T4_CNT;
    for (;;) {
        uint8_t s2 = fl_rdsr(0x35);
        if (s2 & sus) { fl_sus_seen |= (uint8_t)(s2 & sus); *took_us += (T4_CNT - t0) / 24u; return 1; }
        if (!(fl_rdsr(0x05) & 1u))                /* finished before the suspend took (or suspended just now) */
            return (fl_rdsr(0x35) & sus) ? 1 : 0;
        if ((T4_CNT - t0) / 24u > FL_SUS_US) break;
    }
    *took_us += (T4_CNT - t0) / 24u;
    rc = fl_wait_ready(500000u, &dt);             /* no suspend on this part: to the end, as before */
    *took_us += dt;
    return rc;
}

/* start an erase (len 4096, cmd 0x20) or a page program (src, n): the first slice */
static RAMFN int fl_start_ram(uint32_t off, const uint8_t *src, uint32_t n, uint32_t slice_us, uint32_t *took_us)
{
    fl_saved_t s; int rc; uint32_t i, t0 = T4_CNT;
    if (src ? (!FL_GUARD(off, n) || n == 0 || n > 256u || ((off & 0xFFu) + n) > 256u)
            : (!FL_GUARD(off, 0x1000u) || (off & 0xFFFu)))
        return -1;
    if (fl_enter(&s)) return -2;
    fl_wren();
    if (!(fl_rdsr(0x05) & 2u)) rc = -3;           /* WEL did not set */
    else {
        fl_cs(0); fl_tx(src ? 0x02 : 0x20); fl_addr(off);
        if (src) for (i = 0; i < n; i++) fl_tx(src[i]);
        fl_cs(1);
        fl_inval(off, src ? n : 4096u);
        rc = fl_slice(t0, slice_us, src ? FL_SUS_PROG : FL_SUS_ERASE, took_us);
    }
    fl_exit(&s);
    return rc;
}

/* resume a suspended operation for another slice (0: to its end); the cache dropped again once it is done */
static RAMFN int fl_resume_ram(uint32_t off, uint32_t len, uint8_t sus, uint32_t slice_us, uint32_t *took_us)
{
    fl_saved_t s; int rc; uint32_t t0 = T4_CNT;
    if (fl_enter(&s)) return -2;
    if (fl_rdsr(0x35) & sus) {
        fl_cs(0); fl_tx(0x7A); fl_cs(1);
    }
    rc = fl_slice(t0, slice_us, sus, took_us);
    if (rc == 0) fl_inval(off, len);
    fl_exit(&s);
    return rc;
}
#endif

/* ---- XIP side (normal .text) ---- */

/* Once at boot, before any XIP read of the data region: map the data area
 * as an unencrypted XIP window (as the SDK's IOCTL_SET_VM_INFO does). */
static void fl_plain_window_init(void)
{
    SFCENC_CON &= (uint8_t)~2u;
    SFCENC_UNENC_L = FL_XIP(FL_PLAIN_LO);         /* FM-1 0x0208F000 (0x93000); SMK 0x02099000 (0x9D000) */
    SFCENC_UNENC_H = 0x07FFFFFFu;
    SFCENC_CON |= 2u;
    FL_CLI();
    fl_inval(FL_PLAIN_LO, 0x100000u - FL_PLAIN_LO);   /* drop any decrypted lines */
    FL_STI();
}

static inline uint32_t irq_save(void) { FL_CLI(); return 0; }
static inline void irq_restore(uint32_t f) { (void)f; FL_STI(); }

#ifndef FELUCCA_LOADER
static uint32_t fl_off_max_us;                    /* the longest stretch a flash write held the interrupts off (us;
                                                   * the app's CC 116 reply reads and clears it) */
static uint32_t fl_erase_max_us, fl_wall_max_us;  /* the longest erase's own time (the part busy, us) and the longest
                                                   * erase or program from start to end (us: the main loop held;
                                                   * fl_split_op's, not fl_op_start / fl_op_poll's) */
static int8_t fl_can_split = -1;                  /* the part suspends (FL_SUSPEND_OK), read once */
static void fl_off_note(uint32_t t0)
{
    uint32_t d = (T4_CNT - t0) / 24u;
    if (d > fl_off_max_us) fl_off_max_us = d;
}
/* one erase (src 0) or page program, in slices: the interrupts never off longer than ~FL_SLICE_US + 50 us.
 * fl_op_start sends it and runs its first slice; each fl_op_poll one more once FL_GAP_US have passed since the last
 * one ended (else it returns at once), so the main loop can run between the slices: the MIDI in, the keys (0.9.5:
 * the settings' auto save held the main loop ~100 ms in one blocking call, a MIDI note 2 s after a Program Change
 * came ~100 ms late; FM-1, 2026-10-25). Between the calls the operation is suspended (or done): the part reads
 * anywhere but its sector or page (the caller reads nothing there) and takes no other erase or program, so
 * fl_op_start finishes one in progress first. 1 still running, 0 done, < 0 an error. fl_split_op is the two in a
 * loop (blocking): the main loop waits ~(FL_SLICE_US + FL_GAP_US) / FL_SLICE_US x the part's time, ~85 ms for a
 * 20 ms erase, ~125 ms for the SH's 30 ms worst (the saves run in silence only) */
static struct { uint32_t off, t, k, took; uint16_t prog; uint8_t busy; int8_t err; } fl_aop FL_OWN_BSS(fl_aop);   /* (err: the
                                                   * result of one fl_op_start finished for another, for its owner's next poll) */
static int fl_op_poll(void)
{
    uint32_t f, t;
    int rc;
    if (!fl_aop.busy) { rc = fl_aop.err; fl_aop.err = 0; return rc; }   /* (done; or another's start finished it) */
    if ((T4_CNT - fl_aop.t) / 24u < FL_GAP_US) return 1;   /* the gap: the ISRs, the main loop */
    f = irq_save(); t = T4_CNT;
    rc = FL_FAR(fl_resume_ram)(fl_aop.off, fl_aop.prog ? fl_aop.prog : 4096u, fl_aop.prog ? FL_SUS_PROG : FL_SUS_ERASE,
                               fl_aop.k++ < FL_SLICES_MAX ? FL_SLICE_US : 0u, &fl_aop.took);
    fl_off_note(t); irq_restore(f);
    fl_aop.t = T4_CNT;
    fl_aop.busy = rc == 1;
    return rc;
}
static int fl_op_start(uint32_t off, const uint8_t *src, uint32_t n)   /* src: a page program, 0: an erase */
{
    uint32_t f, t;
    int rc;
    if (fl_aop.busy) {                            /* (one in progress: to its end first, its result kept for its owner) */
        while ((rc = fl_op_poll()) == 1) ;
        if (rc < 0) fl_aop.err = (int8_t)rc;
    }
    if (fl_can_split < 0) {
        f = irq_save();
        fl_can_split = (int8_t)(FL_SUSPEND_OK(FL_FAR(fl_jedec_ram)()) != 0);
        irq_restore(f);
    }
    fl_aop.off = off; fl_aop.prog = (uint16_t)(src ? n : 0u); fl_aop.k = 0; fl_aop.took = 0;
    f = irq_save(); t = T4_CNT;
    rc = FL_FAR(fl_start_ram)(off, src, n, fl_can_split ? FL_SLICE_US : 0u, &fl_aop.took);
    fl_off_note(t); irq_restore(f);
    fl_aop.t = T4_CNT;
    fl_aop.busy = rc == 1;
    return rc;
}
static int fl_split_op(uint32_t off, const uint8_t *src, uint32_t n, uint32_t *took_us)
{
    uint32_t t_start = T4_CNT;
    int rc = fl_op_start(off, src, n);
    while (rc == 1) rc = fl_op_poll();
    *took_us = fl_aop.took;
    t_start = (T4_CNT - t_start) / 24u;
    if (t_start > fl_wall_max_us) fl_wall_max_us = t_start;
    if (!src && *took_us > fl_erase_max_us) fl_erase_max_us = *took_us;
    return rc;
}

static int fl_erase4k(uint32_t off, uint32_t *took_us) { return fl_split_op(off, 0, 0, took_us); }

static int fl_write(uint32_t off, const uint8_t *src_ram, uint32_t n)
{
    uint32_t took;
    while (n) {
        uint32_t chunk = 256u - (off & 0xFFu);
        int rc;
        if (chunk > n) chunk = n;
        rc = fl_split_op(off, src_ram, chunk, &took);
        if (rc) return rc;
        off += chunk; src_ram += chunk; n -= chunk;
    }
    return 0;
}
#endif

/* Read back through XIP (needs fl_plain_window_init) and compare. */
static int fl_verify_xip(uint32_t off, const uint8_t *ref, uint32_t n)
{
    const volatile uint8_t *p = (const volatile uint8_t *)FL_XIP(off);
    uint32_t i;
    for (i = 0; i < n; i++) if (p[i] != ref[i]) return -(int)(i + 1);
    return 0;
}
