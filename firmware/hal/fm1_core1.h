/* SPDX-License-Identifier: GPL-3.0-only */
/* The AC79's second core (core 1) for Frodhes' FR_C1 builds: the supply raised, core 1 started into a RAM idle loop
 * at boot, one job at a time through a mailbox with a check word, refused jobs done by core 0 while core 1 stays on,
 * and core 1 held on every reset path. Ported from Textura's firmware/src/core1.h (same author, GPL-3.0-only:
 * commits 4a3cff4, 2135288, 3c14399 "read again before holding", 548fd55), on the HAL hooks of Frodhes' c1-hal.
 *
 * The app includes this file BEFORE platform.c (frodhes.c, FR_C1 1: the FM-1 firmware only; the host build has its
 * twin, src/c1_host.h). It defines the HAL's two hooks first, so every reset and fault goes through it:
 *   FM1_RESET_HOOK()   fm1_sys.h: fm1_reset_reason (boot), fm1_reboot, fm1_enter_uboot, fm1_core_reset: core 1 held
 *   FM1_FAULT_HOOK(f)  fm1_irq.h fm1_fault_c, first: a fault of core 0 holds core 1 and goes on to the crash screen; a
 *                      fault of core 1 is noted (vector, frame, ETM) and core 1 stops in a RAM loop: no crash record,
 *                      no reset; core 0's next wait holds it (C1_FAULT: one core from then on)
 * and platform.c calls APP_EARLY() (fr_c1_boot) at boot, interrupts off, before the audio, the timers and the top of
 * RAM's write lock (core 1 started later does not reach its entry: Textura's finding).
 *
 *   int  fr_c1_boot(void)           hold, the rails (VDC14 -> 7 = 1.60 V, then SYSVDD -> 15 = 1.38 V, a level per
 *                                   100 us, never lowered: the stock firmware's), then start and one ping job:
 *                                   1 = live; 0 = one core (C1_LOWV: the rails not raised; C1_DEAF: no answer)
 *   int  fr_c1_live(void)           1 while core 1 takes jobs
 *   int  fr_c1_run(fn, arg)         core 0: hand core 1 fn(arg); 1 = taken (fr_c1_wait_us before touching what it
 *                                   works on), 0 = not live (the caller does the work)
 *   int  fr_c1_wait_us(us)          core 0: 0 = done; -2 = refused (a bad read core 1 could not clear: never run, the
 *                                   caller does the work, core 1 stays on); -1 = late, refusals too often, a fault
 *                                   of core 1 or a mailbox not as core 0 left it: core 1 held for good (the caller
 *                                   does the work, maybe half done by core 1)
 *   void fr_c1_park(void)           hold core 1 (registers only: safe before the RAM is set up and when it never ran)
 *   uint32_t fr_c1_info(k)          the CC 116 items (frodhes.c fr_c1_reply): FR_C1_INFO_* below
 *
 * The chip (the AC79 SDK's start sequence, as Textura documented it):
 *   C1_CON 0x1EEE004   bit 3 enables core 1, bit 1 holds it. Start: bit 3 set, then bit 1 cleared; hold: bit 1 set,
 *                      then bit 3 cleared.
 *   0x01C7FFF8         the address core 1 starts at (through the chip's ROM first: the PC limits open meanwhile; in
 *                      the reserved top of RAM, written before fm1_guard_lock_top).
 *   0x00010008 bit 3   (SYS_DIV) set while it starts, then restored.  0x1EEF300..: core 1's interrupt bank (all
 *                      off).  0x1EEF2D0..: core 1's EMU bank (its stack limit).
 *   Holding core 1 cuts the read it has on the bus: the chip flags an AXI read invalid a few instructions later, which
 *   core 0's bus guard would trap, so the bus traps are off around a hold and the flag is cleared after. The AXI traps
 *   (DBG_EN bits 4, 5) are not per core: off while core 1 runs, back when it is held for good.
 *   Some units read wrong words now and then while both cores are busy (single bits, the memory intact: the read
 *   path). Every job carries a check word (its function, argument and number). A mismatch is read again (C1_REREAD
 *   times) before it counts: a read-again that matches runs the job (a recovery, counted); else core 1 refuses it for
 *   good (its number consumed, counted) and core 0 does the work itself, core 1 staying on. Core 0 reads "done" and
 *   "bad" twice. Held for good only on a late job (C1_LATE), refusals too often (C1_BAD: over C1_REF_MAX in about
 *   C1_REF_WIN jobs) or a mailbox not as core 0 left it.
 *
 * Core 1 waits in RAM (.ram_text.c1: no flash fetch while idle), looking at the mailbox once every C1_POLL_TICKS (a
 * timer read between: the RAM left alone meanwhile; core 0's wait the same). Its jobs run inside core 0's audio ISR,
 * which waits for them before it returns, and the flash goes off only with the interrupts off (fm1_flash.h slices),
 * never under a job, so the jobs run XIP code: in FR_C1 builds every function a job reaches is in flash (frodhes.c
 * FX_RAM), so the two cores never fetch code from RAM at once under load (Textura 548fd55; Frodhes' 14-voice crashes
 * were core 0 misfetching its RAM code while core 1 ran RAM code too). */
#pragma once
#if defined(DINGHY_HOST) || defined(DINGHY_WEB)
#error "fm1_core1.h is FM-1 firmware only (the host's twin: src/c1_host.h)"
#endif
#include <stdint.h>

static void fr_c1_park(void);
static void fr_c1_fault_hook(uint32_t *f);
#define FM1_RESET_HOOK() fr_c1_park()
#define FM1_FAULT_HOOK(f) fr_c1_fault_hook(f)
#include "fm1_cc.h"
#include "fm1_time.h"
#include "fm1_irq.h"
#include "fm1_sys.h"
#include "fm1_guard.h"
#include "fm1_keep.h"

#define C1_CON (*(volatile uint32_t *)0x1EEE004u)
#define C1_GATE (*(volatile uint32_t *)0x00010008u)
#define C1_BOOT (*(volatile uint32_t *)0x01C7FFF8u)
#define C1_IRQ(i) (*(volatile uint32_t *)(0x1EEF300u + 4u * (i)))
#define C1_EMU_CON (*(volatile uint32_t *)0x1EEF2D0u)
#define C1_EMU_MSG (*(volatile uint32_t *)0x1EEF2D4u)  /* core 1's EMU bank (core 0's 0x1EEF0D0.. + 0x200) */
#define C1_EMU_SSP_H (*(volatile uint32_t *)0x1EEF2D8u)
#define C1_EMU_SSP_L (*(volatile uint32_t *)0x1EEF2DCu)
#define C1_EMU_USP_H (*(volatile uint32_t *)0x1EEF2E0u)
#define C1_EMU_USP_L (*(volatile uint32_t *)0x1EEF2E4u)
#define C1_ETM_CON (*(volatile uint32_t *)0x1EEF3C0u)  /* its branch trace (core 0's 0x1EEF1C0 + 0x200) */
#define C1_ETM_PC(i) (*(volatile uint32_t *)(0x1EEF3C4u + 4u * (i)))
#define C1_SYNC() __asm__ volatile("csync" ::: "memory")
#define C1_BUS ((0x3Fu << 16) | (0x3u << 4))    /* the bus-invalid traps (fm1_guard.h FM1_GUARD_BUS) */
#define C1_AXI 0x30u                            /* of those, the AXI ones (not per core) */
#define C1_STACK (12288u / 4u)                  /* words: core 1's user stack (the effects' chain, fx_process, and
                                                 * room; FR_C1_INFO_STACK says how deep it went), its lowest 256 B the
                                                 * margin below the limit; fr_c1_entry's literals 12288 / 12544 follow */
#define C1_MARK 0x43314D4Bu                     /* painted at start: how deep the stack went */
#ifndef C1_REREAD
#define C1_REREAD 3u                            /* reads again on a mismatch before it counts (Textura 3c14399) */
#endif
#ifndef C1_POLL_TICKS
#define C1_POLL_TICKS 24u                       /* each core's mailbox poll: once a us (0: as fast as it goes) */
#endif
#define C1_REF_WIN 64u                          /* refusals: + this each, - 1 a job done; over C1_REF_MAX of them in
                                                 * about C1_REF_WIN jobs: held for good (C1_BAD) */
#define C1_REF_MAX 16u
#define C1_P3_VDC14 0x06u                       /* P3_ANA_CON6 [2:0]: 1.25 V + 50 mV a level */
#define C1_P3_SYSVDD 0x09u                      /* P3_ANA_CON9 [3:0]: the core and SRAM rail, 0.93 V + 30 mV a level */

/* the mailbox: core 0 writes the first 64 B line only, core 1 the second only (each core's writes on lines of its own) */
typedef struct {
    volatile uint32_t seq;                      /* core 0: the job's number (done + 1 hands it over) */
    volatile uint32_t arg;
    void (*volatile fn)(uint32_t);
    volatile uint32_t chk;                      /* C1_CHK(fn, arg, seq), written before seq */
    uint32_t pad0[12];
    volatile uint32_t alive, done;              /* core 1: it runs; the last job it finished (or refused) */
    volatile uint32_t bad, bad_seq, bad_fn;     /* the jobs it refused: count, the last one's number, the fn it read */
    volatile uint32_t rec;                      /* mismatches a read-again cleared */
    volatile uint32_t busy;                     /* its time in jobs since start (TIMER4 ticks, wrapping) */
    uint32_t pad1[9];
} c1_mb_t;
_Static_assert(sizeof(c1_mb_t) == 128u, "two 64-byte lines");
#define C1_CHK(fn, arg, seq) ((uint32_t)(uintptr_t)(fn) ^ (arg) ^ ((seq) * 0x9E3779B1u) ^ 0xC1C1A5A5u)

/* core 1's state: off (never started), live, then held for good by: refusals too often, a late job, no answer at start,
 * the rails not raised, a fault of core 1, a park (a reset path), a mailbox not as core 0 left it */
enum { C1_OFF, C1_ON, C1_BAD, C1_LATE, C1_DEAF, C1_LOWV, C1_FAULT, C1_PARK, C1_DESYNC };
enum { FR_C1_INFO_STATE, FR_C1_INFO_JOBS_LO, FR_C1_INFO_JOBS_HI, FR_C1_INFO_REFUSED, FR_C1_INFO_BAD, FR_C1_INFO_WAIT_US,
       FR_C1_INFO_VDC14_BOOT, FR_C1_INFO_SYSVDD_BOOT, FR_C1_INFO_VDC14, FR_C1_INFO_SYSVDD, FR_C1_INFO_FAULT_VEC,
       FR_C1_INFO_STACK, FR_C1_INFO_REC1, FR_C1_INFO_REC0, FR_C1_INFO_LATE, FR_C1_INFO_HOLDS, FR_C1_INFO_BUSY_US,
       FR_C1_INFO_N };
/* after a fault of core 1: its frame's reti, rete, retx, rets, sp, psr, core 1's EMU_MSG, DBG_MSG (shared), its last
 * 4 branches (ETM) */
enum { C1_FR_RETI, C1_FR_RETE, C1_FR_RETX, C1_FR_RETS, C1_FR_SP, C1_FR_PSR, C1_FR_EMU, C1_FR_DBG, C1_FR_ETM,
       C1_FR_N = C1_FR_ETM + 4 };

static c1_mb_t c1_mb __attribute__((aligned(64)));
/* its stacks, one block (the stack limit spans both): the user stack (the jobs), then 256 bytes of system stack (its
 * exceptions only: no interrupts on core 1). In NOINIT's spare room (app.ld: never cleared; painted by fr_c1_boot),
 * as Textura's: the pool and the RAM stay the app's */
uint32_t fr_c1_stk[C1_STACK + 64u] __attribute__((aligned(8), section(".noinit.c1")));
_Static_assert(4u * C1_STACK == 12288u && 4u * (C1_STACK + 64u) == 12544u, "fr_c1_entry's stack literals");
static struct {
    uint8_t st, started, rail_boot[2];
    volatile uint32_t faulted, fvec;            /* a fault of core 1 (this run): set by core 1 in fr_c1_fault_hook */
    volatile uint32_t frec[C1_FR_N];            /* its record (C1_FR_*), written before faulted */
    uint32_t s, bad0;                           /* core 0: the job handed last, core 1's refusals before it */
    uint32_t jobs, ref, ref_win, rec0, late, holds, wait_max;
    uint32_t busy_last, busy_max;               /* core 1's busy count at the last block's end; the most in one block */
} c1;

void fr_c1_main(void);
void fr_c1_entry(void);
/* its entry: interrupts off, its stacks, then out of the start-up state into fr_c1_main (in RAM, never returns) */
__asm__(".section .ram_text.c1e,\"ax\",@progbits\n"
        ".balign 4\n"
        ".globl fr_c1_entry\n"
        "fr_c1_entry:\n"
        "\tcli\n"
        "\ticfg = 0\n"
        "\tusp = fr_c1_stk + 12288\n"
        "\tsp = fr_c1_stk + 12544\n"
        "\tssp = fr_c1_stk + 12544\n"
        "\treti = fr_c1_main\n"
        "\trti\n"
        ".text\n");

/* a pause between two looks at the mailbox (the timer register only: inline, no call, in RAM on core 1) */
static inline __attribute__((always_inline)) void c1_pause(void)
{
#if C1_POLL_TICKS
    uint32_t t0 = FM1_T4_CNT;
    while (FM1_T4_CNT - t0 < C1_POLL_TICKS) ;
#endif
}

void __attribute__((section(".ram_text.c1"), used, noreturn)) fr_c1_main(void)
{
    uint32_t done, busy = 0;
    C1_EMU_CON &= ~(1u << 3);                   /* its stack limit: its two stacks (as core 0's, fm1_guard.h), the
                                                 * lowest 256 bytes the margin below the limit */
    C1_EMU_SSP_L = (uint32_t)(uintptr_t)(fr_c1_stk + 64);
    C1_EMU_SSP_H = (uint32_t)(uintptr_t)(fr_c1_stk + C1_STACK + 64u) - 1u;
    C1_EMU_USP_L = (uint32_t)(uintptr_t)(fr_c1_stk + 64);
    C1_EMU_USP_H = (uint32_t)(uintptr_t)(fr_c1_stk + C1_STACK + 64u) - 1u;
    C1_EMU_CON = (C1_EMU_CON & ~((1u << 2) | (0x1Fu << 16))) | (1u << 3);
    done = c1_mb.done;
    c1_mb.alive = 1;
    C1_SYNC();
    for (;;) {
        uint32_t s = c1_mb.seq, k, a = 0, job = 0;
        void (*fn)(uint32_t) = 0;
        C1_SYNC();
        if (s != done + 1u) {                   /* idle; or a number no job has (a misread: the next look reads it
                                                 * again) */
            if (s != done) { c1_mb.rec++; C1_SYNC(); }
            c1_pause();
            continue;
        }
        for (k = 0; ; k++) {                    /* the next number: its job and check, a mismatch read again */
            fn = c1_mb.fn;
            a = c1_mb.arg;
            C1_SYNC();
            if (fn && c1_mb.chk == C1_CHK(fn, a, s)) { job = 1; break; }
            if (k == C1_REREAD) break;
            C1_SYNC();
            if (c1_mb.seq != s) break;          /* (the number was the misread: no job) */
        }
        if (k && (job || k < C1_REREAD)) { c1_mb.rec++; C1_SYNC(); }   /* (cleared by a read-again) */
        if (job) {
            uint32_t t = FM1_T4_CNT;            /* (the register: no call out of RAM) */
            fn(a);
            busy += FM1_T4_CNT - t;
            done = s;                           /* (its own count, in a register: a misread done never moves it) */
            c1_mb.busy = busy;
        } else if (k == C1_REREAD) {            /* refused: number s (core 0 hands only done + 1) is consumed, never
                                                 * run; core 0 sees bad move with done and does the work itself */
            done = s;
            c1_mb.bad_seq = s; c1_mb.bad_fn = (uint32_t)(uintptr_t)fn;
            C1_SYNC();
            c1_mb.bad++;
        } else continue;
        C1_SYNC();
        c1_mb.done = done;
        C1_SYNC();
    }
}

/* after a fault of core 1: it stops here, in RAM (no flash fetch), until core 0 holds it */
static void __attribute__((section(".ram_text.c1"), used, noreturn, noinline)) c1_stop_here(void)
{
    for (;;) C1_SYNC();
}

static void __attribute__((section(".ram_text.c1"), used, noinline)) c1_ping(uint32_t p)
{
    *(volatile uint32_t *)(uintptr_t)p ^= 0xA5C3u;
}

/* hold core 1: registers only (before the RAM is set up too, and when it never ran) */
static void c1_hold(void)
{
    uint32_t locked = !(FM1_DBG_WR_EN & 1u), en, m = C1_BUS;
    volatile uint32_t k;
    HAL_KEEP_REG(m);                            /* (~C1_BUS as a constant is 0xFFC0FFCF: a ROM address to build.py) */
    if (locked) FM1_DBG_WR_EN = 0xE7u;
    en = FM1_DBG_EN;
    FM1_DBG_EN = en & ~m;                       /* (the hold's cut read: not a trap on core 0) */
    C1_SYNC();
    C1_CON |= 0x2u;
    C1_CON &= ~0x8u;
    C1_SYNC();
    for (k = 0; k < 256u; k++) ;                /* (the flag comes a few instructions after the hold) */
    FM1_DBG_MSG_CLR = C1_BUS;
    C1_SYNC();
    FM1_DBG_EN = en;
    if (locked && (FM1_DBG_WR_EN & 1u)) FM1_DBG_WR_EN = 0xE7u;
    C1_SYNC();
}

static void c1_axi(int on)                      /* the AXI traps: off while core 1 runs, on once it is held */
{
    fm1__dbg_unlock();
    if (on) FM1_DBG_EN |= C1_AXI; else FM1_DBG_EN &= ~C1_AXI;
    fm1__dbg_lock();
}

static void fr_c1_park(void)
{
    c1_hold();
    if (c1.st == C1_ON) {
        c1.st = C1_PARK;
        c1_axi(1);
    }
}

static void c1_stop(uint32_t why)               /* held for good: one core from now on */
{
    fr_c1_park();
    c1.st = (uint8_t)why;
    c1.holds++;
}

static int fr_c1_live(void)
{
    if (c1.st == C1_ON && c1.faulted) c1_stop(C1_FAULT);
    return c1.st == C1_ON;
}

/* a rail up to level, one step per 100 us, never lowered (interrupts off: P33 is not reentrant) */
static uint32_t c1_rail_up(uint32_t reg, uint32_t mask, uint32_t level)
{
    uint32_t r = fm1_p33_read(reg);
    while ((r & mask) < level) {
        fm1_p33_write(reg, (uint8_t)((r & ~mask) | ((r & mask) + 1u)));
        fm1_delay_us(100);
        r = fm1_p33_read(reg);
    }
    return r & mask;
}

static int fr_c1_run(void (*fn)(uint32_t), uint32_t arg)
{
    uint32_t s;
    if (!fr_c1_live()) return 0;
    C1_SYNC();
    if (c1_mb.done != c1.s || c1_mb.seq != c1.s) {   /* (the mailbox as core 0 left it? read again first) */
        C1_SYNC();
        if (c1_mb.done != c1.s || c1_mb.seq != c1.s) { c1_stop(C1_DESYNC); return 0; }
        c1.rec0++;
    }
    c1.bad0 = c1_mb.bad;
    s = c1.s + 1u;
    c1_mb.fn = fn;
    c1_mb.arg = arg;
    c1_mb.chk = C1_CHK(fn, arg, s);
    C1_SYNC();
    c1_mb.seq = s;
    C1_SYNC();
    c1.s = s;
    return 1;
}

static int fr_c1_wait_us(uint32_t us)
{
    uint32_t t0 = fm1_ticks();
    if (c1.st != C1_ON) return -1;
    for (;;) {
        C1_SYNC();
        if (c1.faulted) { c1_stop(C1_FAULT); return -1; }
        if (c1_mb.done == c1.s) {
            C1_SYNC();
            if (c1_mb.done == c1.s) {           /* (twice: a word read wrong once is not believed) */
                uint32_t t = fm1_ticks() - t0;
                if (t > c1.wait_max) c1.wait_max = t;
                if (c1_mb.bad != c1.bad0) {     /* (twice too) */
                    C1_SYNC();
                    if (c1_mb.bad != c1.bad0) {
                        c1.ref++;
                        c1.ref_win += C1_REF_WIN;
                        if (c1.ref_win > C1_REF_MAX * C1_REF_WIN) { c1_stop(C1_BAD); return -1; }
                        return -2;
                    }
                    c1.rec0++;
                }
                if (c1.ref_win) c1.ref_win--;
                c1.jobs++;
                return 0;
            }
            c1.rec0++;
        }
        if (fm1_ticks() - t0 > us * FM1_TICKS_PER_US) { c1.late++; c1_stop(C1_LATE); return -1; }
#if C1_POLL_TICKS
        {
            uint32_t t1 = fm1_ticks();
            while (fm1_ticks() - t1 < C1_POLL_TICKS) ;
        }
#endif
    }
}

static void fr_c1_block_end(void)               /* core 0, each audio block's end: core 1's busy time in it */
{
    uint32_t b = c1_mb.busy, d = b - c1.busy_last;
    c1.busy_last = b;
    if (d > c1.busy_max) c1.busy_max = d;
}

/* at boot, interrupts off, before the audio, the timers and fm1_guard_lock_top (platform.c APP_EARLY) */
static int fr_c1_boot(void)
{
    uint32_t g, t0, i, v[2];
    volatile uint32_t ping = 0x1234u;
    c1_hold();
    c1.rail_boot[0] = (uint8_t)(fm1_p33_read(C1_P3_VDC14) & 7u);
    c1.rail_boot[1] = (uint8_t)(fm1_p33_read(C1_P3_SYSVDD) & 15u);
    v[0] = c1_rail_up(C1_P3_VDC14, 7u, 7u);       /* VDC14 first: it feeds SYSVDD */
    v[1] = c1_rail_up(C1_P3_SYSVDD, 15u, 15u);
    if (v[0] < 7u || v[1] < 15u) { c1.st = C1_LOWV; return 0; }   /* (core 1 misreads at the boot loader's levels:
                                                 * a run hangs; it stays off, one core) */
    for (i = 0; i < C1_STACK + 64u; i++) fr_c1_stk[i] = C1_MARK;
    c1_mb.alive = 0; c1_mb.bad = 0; c1_mb.busy = 0; c1_mb.rec = 0;
    c1_mb.done = c1_mb.seq;
    c1.s = c1_mb.seq; c1.bad0 = 0;
    c1_mb.fn = 0;
    C1_SYNC();
    for (i = 0; i < 32u; i++) C1_IRQ(i) = 0;
    C1_ETM_CON |= 1u;                           /* its branch trace (for a fault's record) */
    fm1__dbg_unlock();                          /* the PC limits open: it starts in the chip's ROM */
    FM1_PC_LIMIT0_L = 0u; FM1_PC_LIMIT0_H = 0xFFFFFFFFu;
    FM1_PC_LIMIT1_L = 0u; FM1_PC_LIMIT1_H = 0xFFFFFFFFu;
    fm1__dbg_lock();
    C1_BOOT = (uint32_t)(uintptr_t)&fr_c1_entry;
    g = C1_GATE;
    C1_GATE = g | 0x8u;
    C1_SYNC();
    C1_CON |= 0x8u;
    C1_CON &= ~0x2u;
    t0 = fm1_ticks();
    while (!c1_mb.alive && fm1_ticks() - t0 < 20000u * FM1_TICKS_PER_US) C1_SYNC();   /* 20 ms */
    C1_GATE = g;
    fm1_guard_enable(FM1_GUARD_PC);             /* the PC limits as before (XIP and .ram_text) */
    if (!c1_mb.alive) { c1_hold(); c1.st = C1_DEAF; return 0; }
    c1_axi(0);
    c1.st = C1_ON;
    c1.started = 1;
    if (!fr_c1_run(c1_ping, (uint32_t)(uintptr_t)&ping) || fr_c1_wait_us(2000u) != 0 || ping != (0x1234u ^ 0xA5C3u)) {
        if (c1.st == C1_ON) c1_stop(C1_DEAF);   /* (a wrong answer; else the wait gave the reason) */
        return 0;
    }
    c1.jobs = 0; c1.holds = 0;
    return 1;
}

/* every fault, first (fm1_fault_c, before its "next fault resets" flag). Which core: by its sp alone, core 1 always on
 * its own stacks (fr_c1_stk), core 0 never (not by ICFG bit 8: core 0 runs without it from fr_c1_boot at APP_EARLY to
 * fm1_irq_enable_all). Before core 1 ever started, every fault is core 0's */
static void fr_c1_fault_hook(uint32_t *f)
{
    uint32_t lo = (uint32_t)(uintptr_t)fr_c1_stk, sp = f[24], i;
    if (!c1.started || sp < lo || sp > lo + (C1_STACK + 64u) * 4u) {
        fr_c1_park();                           /* core 0's: core 1 held, on to the crash screen and the reset */
        return;
    }
    c1.fvec = (f[19] - (uint32_t)(uintptr_t)fm1_fatal_stubs) / 6u;   /* (vector + 1: 0 = none) */
    c1.frec[C1_FR_RETI] = f[16]; c1.frec[C1_FR_RETE] = f[17]; c1.frec[C1_FR_RETX] = f[18];
    c1.frec[C1_FR_RETS] = f[25]; c1.frec[C1_FR_SP] = f[24]; c1.frec[C1_FR_PSR] = f[20];
    c1.frec[C1_FR_EMU] = C1_EMU_MSG; c1.frec[C1_FR_DBG] = FM1_DBG_MSG;
    for (i = 0; i < 4u; i++) c1.frec[C1_FR_ETM + i] = C1_ETM_PC(i);
    C1_SYNC();
    c1.faulted = 1;
    C1_SYNC();
    {
        void (*volatile stop)(void) = c1_stop_here;   /* (XIP -> RAM: through a register) */
        stop();
    }
}

static uint32_t fr_c1_info_n(void)             /* the items fr_c1_info has: the record's after a fault of core 1 */
{
    return FR_C1_INFO_N + (c1.faulted ? 3u * C1_FR_N : 0u);
}

static uint32_t fr_c1_info(uint32_t k)          /* main loop: the CC 116 items 30 + k (frodhes.c fr_c1_reply) */
{
    uint32_t v = 0, i;
    if (k >= FR_C1_INFO_N) {                    /* the fault's record: word (k - N) / 3, its part (k - N) % 3 */
        k -= FR_C1_INFO_N;
        return k < 3u * C1_FR_N ? c1.frec[k / 3u] >> (14u * (k % 3u)) & 16383u : 0u;
    }
    switch (k) {
    case FR_C1_INFO_STATE: v = c1.st; break;
    case FR_C1_INFO_JOBS_LO: v = c1.jobs & 16383u; break;
    case FR_C1_INFO_JOBS_HI: v = c1.jobs >> 14 & 16383u; break;
    case FR_C1_INFO_REFUSED: v = c1.ref; break;
    case FR_C1_INFO_BAD: v = c1_mb.bad; break;
    case FR_C1_INFO_WAIT_US: v = c1.wait_max / FM1_TICKS_PER_US; c1.wait_max = 0; break;
    case FR_C1_INFO_VDC14_BOOT: v = c1.rail_boot[0]; break;
    case FR_C1_INFO_SYSVDD_BOOT: v = c1.rail_boot[1]; break;
    case FR_C1_INFO_VDC14: case FR_C1_INFO_SYSVDD:
        fm1_irq_off();
        v = fm1_p33_read(k == FR_C1_INFO_VDC14 ? C1_P3_VDC14 : C1_P3_SYSVDD) & (k == FR_C1_INFO_VDC14 ? 7u : 15u);
        fm1_irq_on();
        break;
    case FR_C1_INFO_FAULT_VEC: v = c1.fvec; break;
    case FR_C1_INFO_STACK:
        for (i = 64u; i < C1_STACK && fr_c1_stk[i] == C1_MARK; i++) ;   /* (the user stack: words 64 .. C1_STACK) */
        v = c1.started ? (C1_STACK - i) * 4u : 0u;
        break;
    case FR_C1_INFO_REC1: v = c1_mb.rec; break;
    case FR_C1_INFO_REC0: v = c1.rec0; break;
    case FR_C1_INFO_LATE: v = c1.late; break;
    case FR_C1_INFO_HOLDS: v = c1.holds; break;
    case FR_C1_INFO_BUSY_US: v = c1.busy_max / FM1_TICKS_PER_US; c1.busy_max = 0; break;
    }
    return v;
}
