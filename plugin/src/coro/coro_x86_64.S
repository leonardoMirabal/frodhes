// SPDX-License-Identifier: GPL-3.0-only
// The plugin coroutine's context switch on x86_64 (System V: macOS, Linux): saves the callee-saved registers rbx,
// rbp, r12-r15, the stack pointer after the return and the return address into *from, loads *to's and jumps to its
// resume address. No syscall, no signal mask, no allocation. MXCSR and the x87 control word are the thread's (both
// sides run on one thread) and are not switched.
//   void frp_coro_switch(coro_regs_t *from, const coro_regs_t *to)    (plugin/src/coro/coro.h: 8 words)
// A new context starts in frp_coro_boot with rbx = the entry function, r12 = its argument and a 16-byte aligned rsp.
// In a universal (arm64 + x86_64) build this file is assembled for both: only the x86_64 pass has code.
#if defined(__x86_64__)
#ifdef __APPLE__
#define SYM(x) _##x
#define FUNC(x) .globl SYM(x) ; .private_extern SYM(x) ; .p2align 4 ; SYM(x):
#define ENDF(x)
#else
#define SYM(x) x
#define FUNC(x) .globl x ; .hidden x ; .type x, @function ; .p2align 4 ; x:
#define ENDF(x) .size x, .-x
#endif
    .text
FUNC(frp_coro_switch)
    movq    (%rsp), %rax            // the return address
    leaq    8(%rsp), %rcx           // the stack pointer after the return
    movq    %rbx, 0(%rdi)
    movq    %rbp, 8(%rdi)
    movq    %r12, 16(%rdi)
    movq    %r13, 24(%rdi)
    movq    %r14, 32(%rdi)
    movq    %r15, 40(%rdi)
    movq    %rcx, 48(%rdi)
    movq    %rax, 56(%rdi)
    movq    0(%rsi), %rbx
    movq    8(%rsi), %rbp
    movq    16(%rsi), %r12
    movq    24(%rsi), %r13
    movq    32(%rsi), %r14
    movq    40(%rsi), %r15
    movq    48(%rsi), %rsp
    jmpq    *56(%rsi)
ENDF(frp_coro_switch)

FUNC(frp_coro_boot)
    xorl    %ebp, %ebp              // the end of the frame chain (debuggers, backtraces)
    movq    %r12, %rdi
    callq   *%rbx                   // the entry never returns
    ud2
ENDF(frp_coro_boot)

#ifndef __APPLE__
    .section .note.GNU-stack, "", @progbits
#endif
#endif
