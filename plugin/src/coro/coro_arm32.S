// SPDX-License-Identifier: GPL-3.0-only
// The plugin coroutine's context switch on 32-bit ARM (AAPCS, hard float, ELF: the Akai MPC port's arm32 build,
// docs/MPC.md), the same design as coro_arm64.S: saves the callee-saved registers r4-r11, sp, lr and d8-d15 into
// *from, loads them from *to and returns into the other context. No syscall, no signal mask, no allocation.
//   void frp_coro_switch(coro_regs_t *from, const coro_regs_t *to)    (plugin/src/coro/coro.h: 10 words, 8 doubles)
// A new context starts in frp_coro_boot with r4 = the entry function and r5 = its argument. ARM state (not Thumb):
// bx / blx switch state as the addresses ask, so Thumb callers and entries work too.
// In any other build this file assembles to nothing.
#if defined(__arm__) && !defined(__aarch64__)
    .syntax unified
    .arm
    .text
    .globl  frp_coro_switch
    .hidden frp_coro_switch
    .type   frp_coro_switch, %function
    .p2align 2
frp_coro_switch:
    stmia   r0, {r4-r11}
    str     sp, [r0, #32]
    str     lr, [r0, #36]
    add     r2, r0, #40
    vstmia  r2, {d8-d15}
    ldmia   r1, {r4-r11}
    ldr     sp, [r1, #32]
    ldr     lr, [r1, #36]
    add     r2, r1, #40
    vldmia  r2, {d8-d15}
    bx      lr
    .size   frp_coro_switch, .-frp_coro_switch

    .globl  frp_coro_boot
    .hidden frp_coro_boot
    .type   frp_coro_boot, %function
    .p2align 2
frp_coro_boot:
    mov     fp, #0                  // the end of the frame chain (debuggers, backtraces)
    mov     r0, r5
    blx     r4                      // the entry never returns
    udf     #0
    .size   frp_coro_boot, .-frp_coro_boot

    .section .note.GNU-stack, "", %progbits
#endif
