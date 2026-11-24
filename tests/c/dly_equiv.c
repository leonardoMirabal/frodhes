/* SPDX-License-Identifier: GPL-3.0-only */
/* Delay core equivalence harness (the plugin's own-code build): the delay's two sample loops of fx.c (the old ones,
 * side `old`) against firmware/src/dly_own.c (side `new`), from the same random state, compared sample for sample
 * and field for field after every call. Host only. The driver (this file) knows nothing of either loop: each side is
 * tests/c/dly_side.c compiled as a unit of its own (dly_state.h is the interface), so the old side can be a
 * precompiled object (tests/c/dly_old.o) in a tree that does not hold the old code. Build: tests/c/dly_equiv_build.sh.
 *
 *   dly_equiv [--calls N] [--seed S] [--jobs J]       default N = 1000000, S = 1, J = 1 (10^8: --calls 100000000)
 * Output: "PASS <n> calls" or "FAIL case <k> field <name> index <i>", nothing else (k: the call's number).
 *
 * What dly_own.c must expose. One file, included five times
 * with DLY_OWN_PART set to 1 to 5 (fx.c, and dly_tape.c four times: where the derived routines were), after
 * everything fx.c / dly_tape.c define above that place; it must not define a name they define. Part 1: dly_loop (the
 * spec's main loop, section 2) and dly_loop_kb (section 10); part 2: dly_rd, dly_fade (11, 12); part 3 (FR_CHUNK):
 * dly_wr_c, dly_loop_c, dly_loop_cd, dly_loop_ca, dly_fade_c (13 to 15); part 4 (FR_CHUNK): tp_loop_c (16); part 5:
 * tp_tape_k, tp_tape_kb, tp_tape, tp_loop (17, 18). Signatures
 * as in dly_tape.c / the spec; dly_side.c shows the call of each. The harness reaches them in 23 shapes (tp_tape_k in all eight rst / msk / kb forms) (dly_state.h):
 * dly_loop with literal tape / msk (kb 0), dly_loop_kb, dly_fade, dly_loop_cd, dly_loop_ca, dly_fade_c, dly_wr_c,
 * tp_loop_c, dly_rd (result in L[0]). FX_ENG_K / FX_ENG_G read the harness's variables; fxc_o / fxc_e are set per call. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include "dly_state.h"
#define DLY_LEN DLYQ_LEN
typedef dly_st_t st_t;
typedef dly_args_t args_t;
static st_t S;                                   /* the state the next call starts from */

/* ---- random numbers */
static uint64_t rs;
static uint32_t rnd(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return (uint32_t)((rs * 0x2545F4914F6CDD1DULL) >> 32); }
static int32_t rr(int32_t lo, int32_t hi) { return lo + (int32_t)(rnd() % (uint32_t)(hi - lo + 1)); }   /* lo..hi */
static int rp(uint32_t pct) { return rnd() % 100u < pct; }
static int32_t clampi(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : v > hi ? hi : v; }

/* ---- the report: the first mismatch only */
static uint64_t g_case;
static int fail(const char *field, long idx)
{
    printf("FAIL case %llu field %s index %ld\n", (unsigned long long)g_case, field, idx);
    return 1;
}

/* a block of 256 samples: full scale, silence, DC, impulses, noise, square, sine; amp = the full scale */
static void signal_fill(int32_t *x, int amp)
{
    int mode = rr(0, 7), j;
    int32_t dc = rr(-amp, amp), pos = rr(0, 255);
    double ph = 0;
    for (j = 0; j < 256; j++) {
        int32_t v = 0;
        switch (mode) {
        case 0: case 1: v = rr(-amp, amp); break;
        case 2: v = 0; break;
        case 3: v = dc; break;
        case 4: v = j == pos ? (rp(50) ? amp : -amp) : 0; break;
        case 5: v = rr(-64, 64); break;
        case 6: v = (j & 1) ? amp : -amp; break;
        case 7: ph += 0.07 + (double)(pos & 15) * 0.05; v = (int32_t)(amp * ((ph - (long)ph) * 2.0 - 1.0)); break;
        }
        x[j] = v;
    }
}
/* both lines get the same new content */
static void line_fill(void)
{
    int mode = rr(0, 6);
    uint32_t i, pos = rnd();
    int16_t dc = (int16_t)rr(-32768, 32767), *a = old_line();
    for (i = 0; i < DLY_LEN; i++) {
        int32_t v = 0;
        switch (mode) {
        case 0: case 1: v = rr(-32768, 32767); break;
        case 2: v = 0; break;
        case 3: v = dc; break;
        case 4: v = (i == (pos & 65535u) || rp(1)) ? rr(-32768, 32767) : 0; break;
        case 5: v = rp(50) ? 32767 : -32768; break;
        case 6: v = ((i * 37u) & 4095u) * 16 - 32768 + rr(-3, 3); break;
        }
        a[i] = (int16_t)clampi(v, -32768, 32767);
    }
    memcpy(new_line(), a, DLY_LEN * sizeof a[0]);
}

static const uint32_t C_EDGE[] = {0, 1, 2, 3, 65534, 65535, 65536, 65537, 65538, 65600, 0x7FFFFFFFu, 0xFFFFFFFFu};
static const int32_t DI_EDGE[] = {0, 1, 2, 3, 4, 5, 65535, 65534, 65533, 65532, 441, 882};
static const int32_t FR_EDGE[] = {0, 1, 128, 255};
static const uint32_t N_EDGE[] = {1, 2, 3, 4, 7, 8, 255, 256, 128, 64, 32, 16, 5, 6, 100, 200};
#define NEDGE(a) (sizeof(a) / sizeof((a)[0]))

static int32_t pick_edgy(int32_t lo, int32_t hi)        /* uniform with the ends over-weighted */
{
    int k = rr(0, 9);
    return k == 0 ? lo : k == 1 ? hi : k == 2 ? lo + 1 : k == 3 ? hi - 1 : rr(lo, hi);
}

/* a run's start: every state field over its reachable range (and past it where that costs nothing) */
static void state_init(st_t *s, args_t *a, uint64_t run)
{
    uint32_t w = rnd(), edge_c = 0xFFFFFFFEu;
    int e = run < 20000;                         /* the first runs walk the edge tables */
    int32_t d;
    memset(s, 0, sizeof *s);
    a->shape = e ? (int)((run / 9) % DLY_SHAPES) : rr(0, DLY_SHAPES - 1);
    memset(a, 0, sizeof *a), a->shape = e ? (int)((run / 9) % DLY_SHAPES) : rr(0, DLY_SHAPES - 1);
    a->rst = e ? (int)((run / 45) & 1) : rr(0, 1);
    a->target = rr(0, 65534) << 8;
    if (rp(25)) a->target = rr(0, 4096) << 8;
    if (rp(20)) a->target = rr(65000, 65533) << 8;
    if (rp(30)) a->target = rr(441, 22050) << 8;
    d = rp(25) ? a->target : rp(30) ? a->target + rr(-4096, 4096) : rr(0, 65535 << 8);
    if (d < 0) d = 0;
    if (e) {
        d = (DI_EDGE[(run / 3) % NEDGE(DI_EDGE)] << 8) | FR_EDGE[(run / 36) % 4];
        if (rp(50)) a->target = d & ~255;
        edge_c = C_EDGE[run % NEDGE(C_EDGE)];
    }
    s->dly_w = w; s->dly_wow = rnd(); s->dly_flt = rnd();
    if (rp(10)) { s->dly_wow = 0xFFFFFFFFu - (uint32_t)rr(0, 2000000); s->dly_flt = 0xFFFFFFFFu - (uint32_t)rr(0, 2000000); }
    if (rp(10)) { s->dly_wow = (uint32_t)rr(-70000, 70000); s->dly_flt = (uint32_t)rr(-700000, 700000); }
    if (rp(5)) s->dly_w = 0xFFFFFFFFu - (uint32_t)rr(0, 600);
    if (rp(5)) s->dly_w = (uint32_t)rr(0, 600);
    s->dly_d = d;
    s->dly_lp = rp(20) ? rr(-300, 300) : rp(10) ? (rp(50) ? 262144 : -262144) : rr(-262144, 262144);
    s->dly_hp = rp(20) ? rr(-300, 300) : rp(10) ? (rp(50) ? 131072 : -131072) : rr(-131072, 131072);
    s->dly_sgk = rr(0, 65536);
    s->dly_sg = rr(-40000, 40000);
    a->col = pick_edgy(1200, 32480);
    a->fb = pick_edgy(0, 17382);
    a->wet = pick_edgy(0, 32768);
    a->dgt = pick_edgy(0, 32768);
    a->sg16 = rp(30) ? 65536 : pick_edgy(0, 65536);
    s->dly_dg = rp(30) ? a->dgt + rr(-600, 600) : rp(10) ? a->dgt : rr(0, 32768);
    s->dly_dg = clampi(s->dly_dg, 0, 32768);
    s->dly_wk = rp(25) ? a->wet : pick_edgy(0, 32768);
    s->dly_fbk = rp(30) ? -1 : rp(30) ? a->fb : pick_edgy(0, 17382);
    s->dly_rst = (uint8_t)(edge_c != 0xFFFFFFFEu ? 1 : rp(50));
    s->dly_rb = s->dly_rst ? (edge_c != 0xFFFFFFFEu ? w - edge_c
                            : rp(35) ? w - (uint32_t)rr(0, 600) : rp(35) ? w - (uint32_t)rr(65000, 66100)
                            : rp(50) ? w - rnd() : w - (uint32_t)rr(0, 70000)) : rnd();
    s->dly_son = (uint8_t)rr(0, 1); s->dly_clrn = rnd(); s->dly_qz = rnd(); s->dly_lc = (uint8_t)rr(0, 1);
    s->dly_xo = (uint8_t)rr(0, 1); s->dly_sil = rnd(); s->dly_ana = (uint8_t)rr(0, 1);
    s->dly_qlo = rr(-100, 100); s->dly_qhi = rr(-100, 100); s->dly_ring = (uint8_t)rr(0, 1); s->dly_rf = (uint8_t)rr(0, 1);
    s->dly_xg = rp(30) ? pick_edgy(0, 32768) : rr(0, 32768);
    s->dly_xm = rp(35) ? pick_edgy(0, 32768) : rp(20) ? 16384 + rr(-20, 20) : rr(0, 32768);
    s->dly_tx = rp(55) ? 0 : rp(30) ? 1 + 32 * rr(0, 1023) : rp(50) ? 32768 : rr(1, 32768);
    s->dly_tn = rr(0, 65534) << 8;
    s->dly_wm = (uint8_t)rr(0, 2); s->dly_wp = (uint8_t)rr(0, 1); s->dly_wk_msk = rp(50) ? -1 : 0;
    s->tp_b1 = rr(-4194304, 4194304); s->tp_b2 = rr(-4194304, 4194304);
    s->tp_h1 = rr(-262144, 262144); s->tp_h2 = rr(-262144, 262144);
    if (rp(30)) { s->tp_b1 = rr(-300, 300); s->tp_b2 = rr(-300, 300); s->tp_h1 = rr(-300, 300); s->tp_h2 = rr(-300, 300); }
    s->tp_kf = rp(80) ? rr(1000, 8000000) : pick_edgy(0, 8388608);
    s->tp_kq = rr(8000, 32768);
    s->tp_kh = rp(80) ? rr(100, 32768) : pick_edgy(0, 32768);
    memset(s->hc, 0, sizeof s->hc);
    a->ek = rp(30) ? 32768 : rr(16384, 65536);
    a->eg = rp(30) ? 32768 : rr(8192, 32768);
    if (a->shape == 4 && rp(50)) { a->ek = 46341; a->eg = 23197; }
    if (rp(10)) line_fill();
}
/* each call of a run: a new block size and new inputs; some of the arguments move */
static void args_next(args_t *a, int first, uint64_t run)
{
    int e = first && run < 20000;
    a->n = e ? N_EDGE[(run / 7) % NEDGE(N_EDGE)] : rp(25) ? N_EDGE[rnd() % NEDGE(N_EDGE)] : (uint32_t)rr(1, 256);
    {   /* the other shapes' arguments */
        static const int32_t GS[] = {0, 16, -16, 8, -8, 32768, -32768, 1, -1, 2048, -2048, 128, -128};
        uint32_t k;
        a->mode = rr(0, 3);
        a->msk = rp(50) ? -1 : 0;
        a->gs = rp(60) ? GS[rnd() % NEDGE(GS)] : rr(-32768, 32768);
        a->e = a->n; a->o = 0;
        if (a->shape >= 6 && a->shape <= 8) {                  /* 0 <= o < e <= n, mostly on the 32-grid */
            a->e = rp(60) ? 32u * (uint32_t)rr(1, 8) : (uint32_t)rr(1, 256);
            if (a->n < a->e) a->n = rp(70) ? a->e + (uint32_t)rr(0, (int)(256 - a->e)) : a->e;
            if (a->n > 256) a->n = 256;
            if (a->e > a->n) a->e = a->n;
            k = rp(60) ? 32u * (uint32_t)rr(0, 7) : (uint32_t)rr(0, 255);
            a->o = k < a->e ? k : (a->e - 1) / 32u * 32u;
            if (a->o >= a->e) a->o = 0;
        }
        a->w0 = rp(10) ? 0xFFFFFFFFu - (uint32_t)rr(0, 300) : rnd();
        a->j1 = (uint32_t)rr(1, 256); a->j0 = (uint32_t)rr(0, (int)a->j1 - 1);
        if (rp(40)) { a->j0 = 32u * (uint32_t)rr(0, 7); a->j1 = a->j0 + 8u * (uint32_t)rr(1, 4); if (a->j1 > 256) a->j1 = 256; if (a->j0 >= a->j1) a->j0 = a->j1 - 1; }
        a->p0 = (uint32_t)rr(0, (int)a->j0);
        a->np = rp(40) ? 8u : rp(30) ? 256u : (uint32_t)rr(1, 256);
        a->g0 = pick_edgy(0, 32768);
        a->c = rp(40) ? C_EDGE[rnd() % NEDGE(C_EDGE)] : rp(40) ? (uint32_t)rr(0, 70000) : rnd();
        a->d = rp(50) ? (DI_EDGE[rnd() % NEDGE(DI_EDGE)] << 8 | FR_EDGE[rnd() % 4]) : rp(10) ? rr(-300, 0) : rr(0, 65535 << 8);
    }
    if (!first) {
        if (rp(20)) a->col = pick_edgy(1200, 32480);
        if (rp(20)) a->fb = pick_edgy(0, 17382);
        if (rp(20)) a->wet = pick_edgy(0, 32768);
        if (rp(20)) a->dgt = pick_edgy(0, 32768);
        if (rp(10)) a->target = rr(0, 65534) << 8;
        if (rp(20)) a->sg16 = rp(50) ? 65536 : rr(0, 65536);
        if (rp(10)) a->rst = rr(0, 1);
    }
}

/* compares one call's two results: outputs, every state field, the rows written; the whole line when `full` */
static int check_all(const st_t *o, const st_t *nw, const int32_t *oL, const int32_t *oR, const int32_t *nL,
                     const int32_t *nR, uint32_t w0, uint32_t n, int full)
{
    uint32_t j;
    const int16_t *a = old_line(), *b = new_line();
    for (j = 0; j < 256; j++) if (oL[j] != nL[j]) return fail("L", j);
    for (j = 0; j < 256; j++) if (oR[j] != nR[j]) return fail("R", j);
#define F(nm, t) if (o->nm != nw->nm) return fail(#nm, 0);
    DLY_SCALARS(F)
#undef F
    for (j = 0; j < 5; j++) if (o->hc[j] != nw->hc[j]) return fail("fx_h_clip", j);
    for (j = 0; j < 256; j++) if (o->dt[j] != nw->dt[j]) return fail("dly_dt", j);
    for (j = 0; j < 256; j++) if (o->m[j] != nw->m[j]) return fail("dly_m", j);
    for (j = 0; j < 256; j++) if (o->fbv[j] != nw->fbv[j]) return fail("dly_fbv", j);
    for (j = 0; j < n; j++) {
        uint32_t row = (w0 + j) & (DLY_LEN - 1u);
        if (a[row] != b[row]) return fail("dly_buf", row);
    }
    if (full) for (j = 0; j < DLY_LEN; j++) if (a[j] != b[j]) return fail("dly_buf", j);
    return 0;
}

/* one worker: `calls` calls, the case numbers from first_case. A run is 1..12 calls that continue from the old
 * side's state, with the caller's part between them (dly_wk, dly_fbk); a new run starts from a new random state. */
static int worker(uint64_t calls, uint64_t seed, uint64_t first_case)
{
    uint64_t done = 0, run = 0;
    rs = seed * 0x9E3779B97F4A7C15ULL + 0x1234567ULL;
    if (!rs) rs = 1;
    line_fill();
    while (done < calls) {
        args_t a;
        int runlen = rp(40) ? 1 : rr(2, 12), c;
        state_init(&S, &a, run);
        for (c = 0; c < runlen && done < calls; c++, done++) {
            st_t so, sn;
            int32_t Lin[256], Rin[256], oL[256], oR[256], nL[256], nR[256];
            uint32_t j, w0, nrows;
            g_case = first_case + done;
            args_next(&a, c == 0, run);
            signal_fill(S.m, rp(70) ? 131072 : 16777216);     /* the send (masked by msk inside), the stale scratch */
            for (j = 0; j < 256; j++) S.dt[j] = (int32_t)rnd();
            if (a.shape == 10 || a.shape >= 12) {                              /* tp_loop_c reads dly_dt: valid times */
                int32_t base = rr(0, 65534 << 8);
                for (j = 0; j < 256; j++) S.dt[j] = rp(80) ? base + rr(-2000, 2000) : rr(0, 65535 << 8);
            }
            signal_fill(S.fbv, rp(70) ? 131072 : 8388608);
            signal_fill(Lin, rp(70) ? 1048576 : 16777216);
            signal_fill(Rin, rp(70) ? 1048576 : 16777216);
            if (rp(20)) memcpy(Rin, Lin, sizeof Lin);
            if (rp(25)) {                                      /* the taps' neighbourhood: impulses, extremes */
                uint32_t base = S.dly_w - (uint32_t)(S.dly_d >> 8);
                for (j = 0; j < 6; j++) {
                    uint32_t r = (base + (uint32_t)rr(-4, 4)) & (DLY_LEN - 1u);
                    int16_t v = (int16_t)(rp(50) ? (rp(50) ? 32767 : -32768) : rr(-32768, 32767));
                    old_line()[r] = v; new_line()[r] = v;
                }
            }
            if (c == 0 && rp(2)) line_fill();
            w0 = a.shape == 9 ? a.w0 : S.dly_w;
            nrows = a.shape <= 5 || a.shape == 9 || a.shape >= 12 ? a.n : 0;
            memcpy(oL, Lin, sizeof oL); memcpy(oR, Rin, sizeof oR);
            memcpy(nL, Lin, sizeof nL); memcpy(nR, Rin, sizeof nR);
            old_put(&S); old_call(&a, oL, oR); old_get(&so);
            new_put(&S); new_call(&a, nL, nR); new_get(&sn);
            if (check_all(&so, &sn, oL, oR, nL, nR, w0, nrows, (done & 63u) == 0 || done < 20000)) return 1;
            S = so;                                            /* the next block continues from here */
            S.dly_wk = a.wet; S.dly_fbk = rp(5) ? -1 : a.fb;
            if (c == runlen - 1 && (run & 255u) == 0) line_fill();
        }
        run++;
    }
    return 0;
}

int main(int argc, char **argv)
{
    uint64_t calls = 1000000, seed = 1;
    int jobs = 1, i, bad = 0;
    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--calls") && i + 1 < argc) calls = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--seed") && i + 1 < argc) seed = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--jobs") && i + 1 < argc) jobs = atoi(argv[++i]);
        else { fprintf(stderr, "usage: dly_equiv [--calls N] [--seed S] [--jobs J]\n"); return 2; }
    }
    if (jobs < 1) jobs = 1;
    fflush(stdout);
    if (jobs == 1) {
        if (worker(calls, seed, 0)) return 1;
        printf("PASS %llu calls\n", (unsigned long long)calls);
        return 0;
    }
    {
        uint64_t per = calls / (uint64_t)jobs;
        for (i = 0; i < jobs; i++) {
            pid_t p = fork();
            if (p == 0) _exit(worker(per, seed + (uint64_t)i, per * (uint64_t)i));
        }
        for (i = 0; i < jobs; i++) { int st; wait(&st); if (!WIFEXITED(st) || WEXITSTATUS(st)) bad = 1; }
        if (bad) return 1;
        printf("PASS %llu calls\n", (unsigned long long)(per * (uint64_t)jobs));
    }
    return 0;
}
