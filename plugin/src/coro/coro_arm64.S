// SPDX-License-Identifier: GPL-3.0-only
// The plugin coroutine's context switch on arm64 (AAPCS64; Mach-O on macOS, ELF on Linux), after
// move/coro/coro_aarch64.S: saves the callee-saved registers x19-x30, sp and d8-d15 into *from, loads them from
// *to and returns into the other context. No syscall, no signal mask, no allocation.
//   void frp_coro_switch(coro_regs_t *from, const coro_regs_t *to)    (plugin/src/coro/coro.h: 21 words)
// A new context starts in frp_coro_boot with x19 = the entry function and x20 = its argument.
// In a universal (arm64 + x86_64) build this file is assembled for both: only the arm64 pass has code.
#if defined(__aarch64__) || defined(__arm64__)
#ifdef __APPLE__
#define SYM(x) _##x
#define FUNC(x) .globl SYM(x) %% .private_extern SYM(x) %% .p2align 2 %% SYM(x):
#define ENDF(x)
#else
#define SYM(x) x
#define FUNC(x) .globl x ; .hidden x ; .type x, %function ; .p2align 2 ; x:
#define ENDF(x) .size x, .-x
#endif
    .text
FUNC(frp_coro_switch)
    mov     x9, sp
    stp     x19, x20, [x0, #0]
    stp     x21, x22, [x0, #16]
    stp     x23, x24, [x0, #32]
    stp     x25, x26, [x0, #48]
    stp     x27, x28, [x0, #64]
    stp     x29, x30, [x0, #80]
    str     x9,       [x0, #96]
    stp     d8,  d9,  [x0, #104]
    stp     d10, d11, [x0, #120]
    stp     d12, d13, [x0, #136]
    stp     d14, d15, [x0, #152]
    ldp     x19, x20, [x1, #0]
    ldp     x21, x22, [x1, #16]
    ldp     x23, x24, [x1, #32]
    ldp     x25, x26, [x1, #48]
    ldp     x27, x28, [x1, #64]
    ldp     x29, x30, [x1, #80]
    ldr     x9,       [x1, #96]
    ldp     d8,  d9,  [x1, #104]
    ldp     d10, d11, [x1, #120]
    ldp     d12, d13, [x1, #136]
    ldp     d14, d15, [x1, #152]
    mov     sp, x9
    ret
ENDF(frp_coro_switch)

FUNC(frp_coro_boot)
    mov     x29, #0                 // the end of the frame chain (debuggers, backtraces)
    mov     x0, x20
    blr     x19                     // the entry never returns
    brk     #0
ENDF(frp_coro_boot)

#ifndef __APPLE__
    .section .note.GNU-stack, "", %progbits
#endif
#endif
