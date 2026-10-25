/* SPDX-License-Identifier: GPL-3.0-only */
/* rhodes.c rh_coef on a grid, for tests/py/test_rh_coef.py: the function is copied out of rhodes.c (its own text,
 * between its signature and the closing brace) into RH_COEF_CORE by the test. stdin (int32, native order):
 * nm, nt, nk, then nm modes (sh ch th d drel cr b), nt cents values t30 (Q30), nk decay k16 (Q16). stdout (int32):
 * for each mode, t, k and the decay d then drel: E, eps. */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include "fxp.h"
#define Q30 (1 << 30)
typedef struct { int32_t sh, ch, th, d, drel, cr, b; } rh_mode_t;
#include RH_COEF_CORE

static int32_t *rd(size_t n)
{
    int32_t *p = malloc(n * sizeof *p + 1);
    if (!p || fread(p, sizeof *p, n, stdin) != n) { fprintf(stderr, "short input\n"); exit(2); }
    return p;
}

int main(void)
{
    int32_t *h = rd(3), nm = h[0], nt = h[1], nk = h[2], i, j, k, o[4];
    rh_mode_t *m = (rh_mode_t *)rd((size_t)nm * 7);
    int32_t *t = rd((size_t)nt), *kk = rd((size_t)nk);
    for (i = 0; i < nm; i++)
        for (j = 0; j < nt; j++)
            for (k = 0; k < nk; k++) {
                rh_coef(&m[i], m[i].d, t[j], kk[k], &o[0], &o[1]);
                rh_coef(&m[i], m[i].drel, t[j], kk[k], &o[2], &o[3]);
                fwrite(o, sizeof o[0], 4, stdout);
            }
    return 0;
}
