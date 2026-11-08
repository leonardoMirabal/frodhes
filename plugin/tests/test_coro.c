/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin's coroutine (plugin/src/coro), after move/tests/test_coro.c: 10^6 switches each way, the callee-saved
 * integer and floating-point registers kept across switches on both sides, deep frames on the coroutine's stack,
 * and (built with -fsanitize=address) ASan clean. macOS has no seccomp: the no-syscall property is the switch
 * routine's (registers only, see coro_arm64.S / coro_x86_64.S) and the Move's test checks the same design on Linux.
 *   test_coro            run (exit 0: ok) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/coro/coro.h"

#define N 1000000L
static coro_t co;
static volatile long ticks;
static double fsum;

static double deep(int d, double x) { volatile char pad[256]; pad[d & 255] = (char)d; return d ? deep(d - 1, x * 1.0000001) + pad[d & 255] * 0.0 : x; }

static void body(void *arg)
{
    long *n = arg;
    register double a = 1.5, b = 2.25;           /* live across yields: the callee-saved FP registers must survive */
    coro_entered(&co);
    for (;;) {
        a = a * 1.0000001 + b; b = b * 0.9999999;
        ticks++;
        if ((ticks & 65535) == 0) fsum += deep(100, a);
        (*n)++;
        coro_yield(&co);
    }
}

int main(void)
{
    static long n;
    size_t sz = 256u * 1024u;
    void *stk = coro_stack_alloc(sz);
    double x = 0.5, y = 3.0;                      /* the caller's FP state, kept across switches */
    long i, k = 7;
    if (!stk) return 1;
    coro_init(&co, stk, sz, body, &n);
    coro_resume(&co);
    for (i = 0; i < N; i++) {
        x = x * 1.0000001 + 1e-9; y -= 1e-7; k += i & 3;
        coro_resume(&co);
    }
    printf("%s    coro: %ld switches each way, coroutine ran %ld passes, x %.6f y %.6f k %ld\n",
           n == N + 1 && ticks == N + 1 && k > 0 ? "ok" : "FAIL", N, n, x, y, k);
    if (!(n == N + 1 && x > 0.55 && y > 2.89 && y < 2.91)) { printf("FAIL coro: values\n"); return 1; }
    coro_stack_free(stk);
    return 0;
}
