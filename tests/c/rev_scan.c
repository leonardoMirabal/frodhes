/* SPDX-License-Identifier: GPL-3.0-only */
/* The REVERB (every TYPE: HALL, PLATE, INFINITE, and OFF) measured on the firmware's own code: frodhes.c is included
 * whole (as tests/c/rev_fuzz.c does) and fx_reverb runs on a stereo signal, 256 frames per call as fx_process calls it.
 * Integer-exact and deterministic. tools/rev_scan.py writes the signals, runs this and analyses the output
 * (tests/py/test_rev_types.py).
 *
 *   rev_scan IN OUT [#ID=V ...] [@BLOCK:#ID=V ...]
 *
 * IN: raw little-endian int32 stereo frames (L, R), as fx_reverb's L / R; after its end the input is silence.
 * #ID = an fx parameter (an FXP_* id; tools/rev_scan.py reads the enum from fx.c), set with fx_set before the first
 * block, or before block BLOCK with the @ form (a switch while the signal plays).
 * Environment:
 *   REV_SCAN_BLOCKS=N   run N blocks (default: as many as IN holds)
 *   REV_SCAN_WIN=K      OUT gets per K frames (K a multiple of 256) the wet's statistics instead of the samples: 8
 *                       doubles a window, sum of squares L, sum L, max |L|, sum of squares R, sum R, max |R|, the
 *                       number of nonzero wet samples, the window's last block's fx_cost_us (the wet = out - in)
 *   REV_SCAN_TRACE=1    per block on stderr: "blk N cost N"
 *   REV_SCAN_RESET=B    fx_reset_state() before block B
 * Built with -DREV_SCAN_POLL (tools/rev_scan.py, when the tree has rev_types.c's rev_poll): rev_poll() after every
 * block, as the main loop runs it (it clears HALL's lines after another TYPE took over, before HALL runs again).
 * Prints "cost_us N peak N blocks N last_nonzero N" at the end (fx_cost_us() after the last block, the largest |out|,
 * the last block whose wet was not exactly 0, -1 for none). */
#define main host_main
#include "frodhes.c"
#undef main

static int set_one(const char *s)                     /* "#ID=V" */
{
    const char *eq = strchr(s, '=');
    int id;
    if (s[0] != '#' || !eq) return -1;
    id = atoi(s + 1);
    if (id < 0 || id >= FXP_N) return -1;
    fx_set((uint32_t)id, atoi(eq + 1));
    return 0;
}

int main(int argc, char **argv)
{
    static int32_t L[256], R[256], iL[256], iR[256], fr[512];
    FILE *fi, *fo;
    const char *e;
    long nb = -1, win = 0, rst = -1, blk, last = -1;
    int a, trace = getenv("REV_SCAN_TRACE") != 0, eof = 0;
    int32_t pk = 0;
    double st[8] = {0};
    long wn = 0;
    if (argc < 3) { fprintf(stderr, "usage: rev_scan IN OUT [#ID=V ...] [@BLOCK:#ID=V ...]\n"); return 2; }
    if ((e = getenv("REV_SCAN_BLOCKS"))) nb = atol(e);
    if ((e = getenv("REV_SCAN_WIN"))) win = atol(e);
    if ((e = getenv("REV_SCAN_RESET"))) rst = atol(e);
    if (win && win % 256) { fprintf(stderr, "REV_SCAN_WIN: a multiple of 256\n"); return 2; }
    for (a = 3; a < argc; a++)
        if (argv[a][0] != '@' && set_one(argv[a])) { fprintf(stderr, "bad parameter %s\n", argv[a]); return 2; }
    fi = fopen(argv[1], "rb"); fo = fopen(argv[2], "wb");
    if (!fi || !fo) { perror("rev_scan"); return 2; }
    for (blk = 0; nb < 0 ? !eof : blk < nb; blk++) {
        uint32_t j, n = 0, nz = 0;
        for (a = 3; a < argc; a++)                    /* the switches due before this block */
            if (argv[a][0] == '@' && atol(argv[a] + 1) == blk) {
                const char *c = strchr(argv[a], ':');
                if (!c || set_one(c + 1)) { fprintf(stderr, "bad switch %s\n", argv[a]); return 2; }
            }
        if (!eof) n = (uint32_t)fread(fr, 2 * sizeof fr[0], 256, fi);
        if (n < 256) eof = 1;
        if (nb < 0 && !n) break;
        for (j = 0; j < 256u; j++) { L[j] = iL[j] = j < n ? fr[2u * j] : 0; R[j] = iR[j] = j < n ? fr[2u * j + 1u] : 0; }
        if (blk == rst) fx_reset_state();
        fx_reverb(L, R, 256u);
#ifdef REV_SCAN_POLL
        rev_poll();
#endif
        for (j = 0; j < 256u; j++) {
            int32_t wl = L[j] - iL[j], wr = R[j] - iR[j];
            if (iabs(L[j]) > pk) pk = iabs(L[j]);
            if (iabs(R[j]) > pk) pk = iabs(R[j]);
            nz += (wl | wr) != 0;
            if (win) {
                st[0] += (double)wl * wl; st[1] += wl; if (iabs(wl) > st[2]) st[2] = iabs(wl);
                st[3] += (double)wr * wr; st[4] += wr; if (iabs(wr) > st[5]) st[5] = iabs(wr);
                st[6] += (wl | wr) != 0;
            } else {
                fr[2u * j] = L[j]; fr[2u * j + 1u] = R[j];
            }
        }
        if (nz) last = blk;
        if (win) {
            wn += 256;
            if (wn == win) { st[7] = fx_cost_us(); fwrite(st, sizeof st[0], 8, fo); memset(st, 0, sizeof st); wn = 0; }
        } else fwrite(fr, 2 * sizeof fr[0], 256, fo);
        if (trace) fprintf(stderr, "blk %ld cost %u\n", blk, fx_cost_us());
    }
    fclose(fi); fclose(fo);
    printf("cost_us %u peak %d blocks %ld last_nonzero %ld\n", fx_cost_us(), pk, blk, last);
    return 0;
}
