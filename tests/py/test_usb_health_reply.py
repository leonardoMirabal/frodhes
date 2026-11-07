# SPDX-License-Identifier: GPL-3.0-only
"""The CC 116 query's reply fits the MIDI out ring (64 messages, platform.c) with room to spare: frodhes.c's CC 116
branch and usb_health_reply, preprocessed with the shipped flags (cc -E), their messages counted (midi_send 1,
usb_health_send 3: CC 100 id, 101 / 102 value; the 5-counter loop as many times as it sends). A reply of 64 left
nothing for anything else queued at the same moment: item 12 (the stream's state) was dropped on the FM-1
(rc-0.9.9f, the lock's item 22 in the reply)."""
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "firmware", "src", "frodhes.c")
RING = 64
SPARE = 6


def block(src, start):
    """the brace block from the first '{' after start (comments and strings: none with braces here)"""
    i = src.index("{", start)
    d = 0
    for j in range(i, len(src)):
        d += {"{": 1, "}": -1}.get(src[j], 0)
        if d == 0:
            return src[i:j + 1]
    raise AssertionError("unbalanced")


def count(code):
    """the messages one pass of the preprocessed code sends"""
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    n = 0
    loop = re.search(r"for \(i = 0; i < (\d+)u; i\+\+\)\s*(if \(([^)]*)\))?\s*usb_health_send", code)
    if loop:
        k = int(loop.group(1)) - (len(re.findall(r"i != \d+u", loop.group(3))) if loop.group(3) else 0)
        n += 3 * k
        code = code[:loop.start()] + code[loop.end():]
    n += 3 * len(re.findall(r"\busb_health_send\(", code)) + len(re.findall(r"\bmidi_send\(", code))
    return n


def reply(lock):
    src = open(SRC).read()
    a = src.index("FR_COLD static void usb_health_reply(void)")
    health = block(src, a)
    b = src.index("    if (cc == 116u) {")
    q = block(src, b)
    q = q.replace("usb_health_reply();", "HEALTH_REPLY")
    p = subprocess.run(["cc", "-E", "-P", f"-DFELUCCA_UAC_LOCK={lock}", "-"], input=q + "\n@@@\n" + health,
                       capture_output=True, text=True, check=True).stdout
    q, health = p.split("@@@")
    # (DINGHY_HOST undefined: the device; RH_PROF_STAGES / FM1_PROF_TICK undefined: the shipped build)
    assert "HEALTH_REPLY" in q
    return count(q.replace("HEALTH_REPLY", "")) + count(health)


@pytest.mark.parametrize("lock", [1, 0])
def test_cc116_reply_fits_the_midi_ring(lock):
    n = reply(lock)
    print("lock", lock, "messages", n)
    assert n <= (RING - SPARE if lock else 61), n      # (0: the reply of rc-0.9.9e, 61, unchanged)


def test_lock_query_alone():
    """the lock's item 22 answers its own query (CC 116 = 22) and is not in the full reply"""
    src = open(SRC).read()
    assert "usb_health_send(22" not in block(src, src.index("FR_COLD static void usb_health_reply(void)"))
    assert re.search(r"cc == 116u && v == 22u\)", src)
