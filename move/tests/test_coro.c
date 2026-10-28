/* SPDX-License-Identifier: GPL-3.0-only */
/* M1-02: the coroutine. 10^6 switches each way with no syscall (a seccomp filter: any syscall but write / exit kills
 * the process), the callee-saved integer and FP registers kept across switches on both sides, deep
 * frames on the coroutine's stack, and (built with -fsanitize=address) ASan clean. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "../coro/coro.h"
#include "seccomp_guard.h"

#define N 1000000L
static coro_t co;
static volatile long ticks;
static double fsum;

static double deep(int d, double x) { volatile char pad[256]; pad[d & 255] = (char)d; return d ? deep(d - 1, x * 1.0000001) + pad[d & 255] * 0.0 : x; }

static void body(void *arg)
{
    long *n = arg;
    register double a = 1.5, b = 2.25;           /* live across yields: d8-d15 must survive */
    coro_entered(&co);
    for (;;) {
        a = a * 1.0000001 + b; b = b * 0.9999999;
        ticks++;
        if ((ticks & 65535) == 0) fsum += deep(100, a);
        (*n)++;
        coro_yield(&co);
    }
}

static void say(const char *s) { if (write(1, s, strlen(s)) < 0) {} }

int main(int argc, char **argv)
{
    static long n;
    size_t sz = 256u * 1024u;
    void *stk = aligned_alloc(16, sz);
    char msg[160];
    double x = 0.5, y = 3.0;                      /* the caller's FP state, kept across switches */
    long i, k = 7;
    if (!stk) return 1;
    if (argc > 1 && !strcmp(argv[1], "selftest")) {   /* the guard must kill on a syscall: expect SIGSYS */
        (void)argv;
        if (guard_no_syscalls() != 0) return 2;
        syscall(SYS_getpid);
        say("FAIL seccomp guard: getpid was allowed\n");
        return 1;
    }
    coro_init(&co, stk, sz, body, &n);
    coro_resume(&co);                             /* (the first entry: before the filter, ASan may set up here) */
#if !defined(CORO_ASAN) && !defined(CORO_UCONTEXT)
    if (guard_no_syscalls() != 0) { perror("seccomp"); return 1; }
#endif
    for (i = 0; i < N; i++) {
        x = x * 1.0000001 + 1e-9; y -= 1e-7; k += i & 3;
        coro_resume(&co);
    }
    snprintf(msg, sizeof msg, "%s    coro: %ld switches each way, coroutine ran %ld passes, x %.6f y %.6f k %ld\n",
             n == N + 1 && ticks == N + 1 && k > 0 ? "ok" : "FAIL", N, n, x, y, k);
    say(msg);
    if (!(n == N + 1 && x > 0.55 && y > 2.89 && y < 2.91)) { say("FAIL coro: values\n"); syscall(SYS_exit, 1); }
    return 0;
}
