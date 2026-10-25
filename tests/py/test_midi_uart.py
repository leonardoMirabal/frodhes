# SPDX-License-Identifier: GPL-3.0-only
"""The TRS MIDI IN parser (firmware/src/midi_uart.c, Dinghy 0.4's, read by content, with Frodhes' uart_midi_on) on
the host: tests/c/midi_uart_test.c feeds its ring as the DMA does and checks every packet (each message once, cable 1,
running status, realtime, SysEx dropped, TRS IN off, the ring's wrap, a full queue; the ISR's uart_midi_tick latches
(fm1_uart1_rx_take) on every call, so a message whose tail the DMA holds until the latch is passed on at that call,
and drains only when a byte is in). The file is compiled as it is,
next to a stub fm1_uart.h (no registers)."""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUB = """#pragma once
#include <stdint.h>
static void fm1_uart1_midi_init(volatile uint8_t *ring, uint32_t len) { (void)ring; (void)len; }
static uint32_t takes;                                  /* (the polls, for uart_midi_tick's tests) */
static void (*rx_latch_hook)(void);                    /* (the test's model of the DMA's held tail: RDC writes it) */
static uint32_t fm1_uart1_rx_take(void) { takes++; if (rx_latch_hook) rx_latch_hook(); return 0; }   /* (the count is
                                                          not used: the ring is read by content) */
"""


def test_midi_uart_parser(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "hal").mkdir()
    shutil.copy(ROOT / "firmware" / "src" / "midi_uart.c", tmp_path / "src" / "midi_uart.c")
    (tmp_path / "hal" / "fm1_uart.h").write_text(STUB)
    exe = tmp_path / "midi_uart_test"
    subprocess.run(["cc", "-O1", "-Wall", "-Werror", "-Wno-unused-function", f"-I{tmp_path}", "-o", str(exe),
                    str(ROOT / "tests" / "c" / "midi_uart_test.c")], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert r.stdout.count("ok  ") == 15 and "0 failed" in r.stdout, r.stdout
