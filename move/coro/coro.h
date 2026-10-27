/* SPDX-License-Identifier: GPL-3.0-only */
/* One coroutine for app_main() (the FM-1's main loop) on the audio callback's thread: the callback resumes it, it
 * runs one main-loop pass up to its next platform_poll() and switches back. aarch64: coro_aarch64.S (registers only:
 * no syscall). Elsewhere (a debug build on another CPU): ucontext, which does syscalls (never shipped).
 * ASan builds tell the sanitizer about the stack switches (fiber annotations). */
#ifndef FRODHES_MOVE_CORO_H
#define FRODHES_MOVE_CORO_H
#include <stddef.h>
#include <stdint.h>

#if defined(__has_feature)
#if __has_feature(address_sanitizer)
#define CORO_ASAN 1
#endif
#endif
#if defined(__SANITIZE_ADDRESS__)
#define CORO_ASAN 1
#endif
#ifdef CORO_ASAN
void __sanitizer_start_switch_fiber(void **fake_stack_save, const void *bottom, size_t size);
void __sanitizer_finish_switch_fiber(void *fake_stack_save, const void **bottom_old, size_t *size_old);
#endif

typedef void (*coro_fn)(void *arg);

#if defined(__aarch64__) && !defined(CORO_UCONTEXT)
typedef struct { uint64_t r[21]; } coro_regs_t;   /* x19..x30, sp, d8..d15 (coro_aarch64.S) */
void frodhes_coro_switch(coro_regs_t *from, const coro_regs_t *to);
void frodhes_coro_boot(void);
#define CORO_SWITCH(from, to) frodhes_coro_switch((from), (to))
#else
#include <ucontext.h>
typedef ucontext_t coro_regs_t;
#define CORO_SWITCH(from, to) swapcontext((from), (to))
#endif

typedef struct {
    coro_regs_t caller, self;
    void *stack; size_t size;
    void *fake;                                   /* ASan's fake stack of the side that switched away */
    const void *caller_bottom; size_t caller_size;
    int started;
} coro_t;

/* stack: 16-byte aligned, size a multiple of 16 */
static void coro_init(coro_t *c, void *stack, size_t size, coro_fn fn, void *arg)
{
    c->stack = stack; c->size = size; c->started = 0; c->fake = 0;
#if defined(__aarch64__) && !defined(CORO_UCONTEXT)
    {
        uint32_t i;
        for (i = 0; i < 21u; i++) c->self.r[i] = 0;
        c->self.r[0] = (uint64_t)(uintptr_t)fn;          /* x19 */
        c->self.r[1] = (uint64_t)(uintptr_t)arg;         /* x20 */
        c->self.r[11] = (uint64_t)(uintptr_t)frodhes_coro_boot;   /* x30: where the first switch "returns" */
        c->self.r[12] = (uint64_t)(((uintptr_t)stack + size) & ~(uintptr_t)15);   /* sp */
    }
#else
    getcontext(&c->self);
    c->self.uc_stack.ss_sp = stack; c->self.uc_stack.ss_size = size; c->self.uc_link = 0;
    makecontext(&c->self, (void (*)(void))fn, 1, arg);   /* (one pointer argument: fine on LP64 glibc) */
#endif
}

/* the caller's side: run the coroutine until it yields */
static void coro_resume(coro_t *c)
{
#ifdef CORO_ASAN
    __sanitizer_start_switch_fiber(&c->fake, c->stack, c->size);
#endif
    c->started = 1;
    CORO_SWITCH(&c->caller, &c->self);
#ifdef CORO_ASAN
    __sanitizer_finish_switch_fiber(c->fake, 0, 0);
#endif
}

/* the coroutine's side: back to whoever resumed it */
static void coro_yield(coro_t *c)
{
#ifdef CORO_ASAN
    void *fake = 0;
    __sanitizer_start_switch_fiber(&fake, c->caller_bottom, c->caller_size);
#endif
    CORO_SWITCH(&c->self, &c->caller);
#ifdef CORO_ASAN
    __sanitizer_finish_switch_fiber(fake, &c->caller_bottom, &c->caller_size);
#endif
}

/* the coroutine's first lines (ASan: learn the caller's stack) */
static void coro_entered(coro_t *c)
{
#ifdef CORO_ASAN
    __sanitizer_finish_switch_fiber(0, &c->caller_bottom, &c->caller_size);
#else
    (void)c;
#endif
}
#endif
