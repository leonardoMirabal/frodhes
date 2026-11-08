/* SPDX-License-Identifier: GPL-3.0-only */
/* The desktop plugin's coroutine: app_main() (the FM-1's main loop) on the audio thread, as on the Move
 * (move/coro/coro.h, which stays as it is). The process call resumes it once a host millisecond; it runs one main-loop
 * pass up to its next platform_poll() and switches back. Registers only, no syscall, no allocation:
 *   arm64 (macOS, Linux)    coro_arm64.S: x19-x30, sp, d8-d15
 *   x86_64 (macOS, Linux)   coro_x86_64.S: rbx, rbp, r12-r15, rsp, the resume address
 *   Windows x64             Fibers (below): a hand-made stack switch breaks the thread block's stack limits there
 * The floating-point control state (FPCR, MXCSR) is the thread's and is not switched: both sides run on one thread.
 * ASan builds tell the sanitizer about the stack switches (fiber annotations). */
#ifndef FRODHES_PLUGIN_CORO_H
#define FRODHES_PLUGIN_CORO_H
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

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

#if defined(_WIN32)
/* Windows: the coroutine is a fiber with its own stack (CreateFiberEx at coro_init, outside the audio thread; its
 * whole stack committed then). The resuming thread is turned into a fiber for the switch and back after it, only if
 * it was not one already, so the host's thread is left as it was found (that costs the system a small allocation a
 * resume, ~1 a millisecond). No floating-point state is switched (no FIBER_FLAG_FLOAT_SWITCH): one thread runs both
 * sides, as on the other platforms. The four calls are declared here, not from windows.h, whose macros and type
 * names must not meet the app's (slot_bridge.h includes this file after the app). */
#define CORO_WIN 1
__declspec(dllimport) void *__stdcall CreateFiberEx(size_t commit, size_t reserve, unsigned long flags,
                                                    void (__stdcall *start)(void *), void *param);
__declspec(dllimport) void __stdcall SwitchToFiber(void *fiber);
__declspec(dllimport) void *__stdcall ConvertThreadToFiberEx(void *param, unsigned long flags);
__declspec(dllimport) int __stdcall ConvertFiberToThread(void);
__declspec(dllimport) int __stdcall IsThreadAFiber(void);
unsigned __int64 __readgsqword(unsigned long offset);   /* (intrin.h: the TEB's FiberData, GetCurrentFiber()) */

typedef struct {
    void *fiber, *caller;
    coro_fn fn; void *arg;
    int started;
} coro_t;

static void *coro_stack_alloc(size_t size) { (void)size; return (void *)1; }   /* (the fiber owns its stack) */
static void coro_stack_free(void *stack) { (void)stack; }
static void __stdcall coro_fiber_main(void *p)
{
    coro_t *c = p;
    c->fn(c->arg);
    for (;;) SwitchToFiber(c->caller);           /* (a body that returns: never resumed past its end) */
}
static void coro_init(coro_t *c, void *stack, size_t size, coro_fn fn, void *arg)
{
    (void)stack;
    c->fn = fn; c->arg = arg; c->started = 0; c->caller = 0;
    c->fiber = CreateFiberEx(size, size, 0, coro_fiber_main, c);
}
static void coro_resume(coro_t *c)
{
    int conv = !IsThreadAFiber();
    c->caller = conv ? ConvertThreadToFiberEx(0, 0) : (void *)__readgsqword(0x20);
    if (!c->caller || !c->fiber) return;
    c->started = 1;
    SwitchToFiber(c->fiber);
    if (conv) ConvertFiberToThread();
}
static void coro_yield(coro_t *c) { SwitchToFiber(c->caller); }
static void coro_entered(coro_t *c) { (void)c; }
#else

#if defined(__aarch64__) || defined(__arm64__)
typedef struct { uint64_t r[21]; } coro_regs_t;   /* x19..x30, sp, d8..d15 (coro_arm64.S) */
#define CORO_ARM64 1
#elif defined(__x86_64__)
typedef struct { uint64_t r[8]; } coro_regs_t;    /* rbx, rbp, r12..r15, rsp, the resume address (coro_x86_64.S) */
#define CORO_X86_64 1
#else
#error "the plugin's coroutine: arm64 or x86_64 only (Windows: Fibers)"
#endif
void frp_coro_switch(coro_regs_t *from, const coro_regs_t *to);
void frp_coro_boot(void);

typedef struct {
    coro_regs_t caller, self;
    void *stack; size_t size;
    void *fake;                                   /* ASan's fake stack of the side that switched away */
    const void *caller_bottom; size_t caller_size;
    int started;
} coro_t;

/* the coroutine's stack: 16-byte aligned, every page touched now (not on the audio thread later) */
static void *coro_stack_alloc(size_t size)
{
    void *s = aligned_alloc(16, size);
    if (s) memset(s, 0, size);
    return s;
}
static void coro_stack_free(void *stack) { free(stack); }

/* stack: 16-byte aligned, size a multiple of 16 */
static void coro_init(coro_t *c, void *stack, size_t size, coro_fn fn, void *arg)
{
    uint32_t i;
    uint64_t top = (uint64_t)(((uintptr_t)stack + size) & ~(uintptr_t)15);
    c->stack = stack; c->size = size; c->started = 0; c->fake = 0;
    for (i = 0; i < sizeof c->self.r / sizeof c->self.r[0]; i++) c->self.r[i] = 0;
#ifdef CORO_ARM64
    c->self.r[0] = (uint64_t)(uintptr_t)fn;          /* x19 */
    c->self.r[1] = (uint64_t)(uintptr_t)arg;         /* x20 */
    c->self.r[11] = (uint64_t)(uintptr_t)frp_coro_boot;   /* x30: where the first switch "returns" */
    c->self.r[12] = top;                             /* sp */
#else
    c->self.r[0] = (uint64_t)(uintptr_t)fn;          /* rbx */
    c->self.r[2] = (uint64_t)(uintptr_t)arg;         /* r12 */
    c->self.r[6] = top;                              /* rsp: 16-aligned, frp_coro_boot's call pushes the return */
    c->self.r[7] = (uint64_t)(uintptr_t)frp_coro_boot;
#endif
}

/* the caller's side: run the coroutine until it yields */
static void coro_resume(coro_t *c)
{
#ifdef CORO_ASAN
    __sanitizer_start_switch_fiber(&c->fake, c->stack, c->size);
#endif
    c->started = 1;
    frp_coro_switch(&c->caller, &c->self);
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
    frp_coro_switch(&c->self, &c->caller);
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
#endif /* _WIN32 */
#endif
