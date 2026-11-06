# SPDX-License-Identifier: GPL-3.0-only
"""DISPLAY PLAY LED (issue #29): settings_t.play_off, 0 BEAT (the PLAY button's light blinks with the beat) or 1 OFF (dark).
A former reserved byte, so every older record reads 0 = BEAT; anything above 1 is sanitized to BEAT (frodhes.c app_main).
Saved with the settings and back after a power cycle (FRODHES_FLASH); the record stays 256 B, and on the SMK-37 build
smk_mst keeps its place (offset 235) with play_off after it. The blink itself: tests/scripts/play_led.txt."""
import os
import subprocess

import pytest

from hostrun import ROOT, run
from test_persistence import boot, flash_file, put_settings, read_img, settings_record

DISPLAY = ["100 btn 7", "200 btn 7", "300 btn 7", "400 btn 7"]   # GLO four times (ends the splash too)
PG = 21                                                         # ui.c PG_DISPLAY (host: card.<page>.<k>)
O_PLAY_OFF = 235                                                # settings_t play_off (FM-1; after flags2 at 234)
O_PLAY_OFF_SMK, O_SMK_MST = 237, 235                            # the SMK-37 build: smk_mst first, as 0.9.6 saved it
# ARP on (its page, then on) and a C major triad held: the arpeggio's notes are the beat; the PLAY light sampled
ARP = ["2000 btn 10", "2100 btn 10", "2200 keys 0x4880"] + [f"{t} dump" for t in range(2300, 3300, 25)]


def play_led(r):
    """the PLAY light (12) at every dump of the ARP lines"""
    return [r.state(t)["led.12"] for t in range(2300, 3300, 25)]


def offsets(defs, fields):
    """settings_t's offsets and size in a host build with these -D's"""
    tmp = os.path.join(ROOT, "build", "host", os.environ.get("PYTEST_XDIST_WORKER", ""), "py")
    os.makedirs(tmp, exist_ok=True)
    tag = "smk" if defs else "fm1"
    src = os.path.join(tmp, f"play-led-off-{tag}.c")
    open(src, "w").write("#define main host_main\n#include \"frodhes.c\"\n#undef main\n#include <stddef.h>\n"
                         "int main(void) {\n" + "".join(f'printf("{f} %zu\\n", offsetof(settings_t, {f}));\n'
                                                        for f in fields) +
                         'printf("size %zu\\n", sizeof(settings_t)); return 0; }\n')
    exe = src[:-2]
    subprocess.run(["cc", "-w", "-DDINGHY_HOST", '-DAPP_VER="dev"', *defs, "-I" + os.path.join(ROOT, "firmware", "src"),
                    "-I" + os.path.join(ROOT, "firmware", "gen"), "-o", exe, src, "-lm"], check=True)
    out = subprocess.run([exe], capture_output=True, text=True, check=True).stdout
    return {k: int(v) for k, v in (ln.split() for ln in out.splitlines())}


def test_layout():
    """256 B on both boards; play_off out of the reserve, after flags2; the SMK's smk_mst where 0.9.6 put it"""
    assert offsets([], ["flags2", "play_off"]) == {"flags2": 234, "play_off": O_PLAY_OFF, "size": 256}
    assert offsets(["-DBOARD_SMK37=1", "-DFR_VOICES=7u"], ["flags2", "smk_mst", "play_off"]) == \
        {"flags2": 234, "smk_mst": O_SMK_MST, "play_off": O_PLAY_OFF_SMK, "size": 256}


def test_off_is_saved_and_back_after_a_power_cycle():
    fl = flash_file("play-led-pc")
    r1 = run("play-led-pc1", DISPLAY + ["450 dump", "500 turn 5 -1", "600 dump", "4500 dump"], 4700, flash=fl)
    assert (r1.state(450)["label3"], r1.state(450)["card3"]) == ("PLAY LED", "BEAT")
    assert r1.state(600)["card3"] == "OFF"
    assert r1.saves, "the settings were not saved"
    rec = settings_record(read_img(fl))
    assert len(rec) == 256 and rec[O_PLAY_OFF] == 1, (len(rec), rec[O_PLAY_OFF])
    r2 = boot("play-led-pc2", fl, ARP, 3400)
    assert r2.state(100)[f"card.{PG}.3"] == "OFF"
    assert set(play_led(r2)) == {"0"}, play_led(r2)                # dark through the arpeggio
    # and back to BEAT: saved as 0, the light blinks again after the next power cycle
    r3 = run("play-led-pc3", DISPLAY + ["500 turn 5 1", "600 dump", "4500 dump"], 4700, flash=fl, check=False)
    assert r3.state(600)["card3"] == "BEAT" and r3.saves
    assert settings_record(read_img(fl))[O_PLAY_OFF] == 0
    r4 = boot("play-led-pc4", fl, ARP, 3400)
    assert r4.state(100)[f"card.{PG}.3"] == "BEAT"
    assert set(play_led(r4)) == {"0", "1"}, play_led(r4)           # blinks


@pytest.mark.parametrize("byte, want", [(0, "BEAT"), (1, "OFF"), (2, "BEAT"), (255, "BEAT")])
def test_old_records_and_bad_bytes_load(byte, want):
    """0 (every record before, a former reserved byte) BEAT; 1 OFF; anything above 1 sanitized to BEAT"""
    fl = flash_file(f"play-led-b{byte}")
    run(f"play-led-b{byte}-0", DISPLAY + ["500 turn 2 1", "4500 dump"], 4700, flash=fl)   # a record (COLOR moved)
    img = read_img(fl)
    rec = bytearray(settings_record(img))
    assert len(rec) == 256 and rec[O_PLAY_OFF] == 0
    rec[O_PLAY_OFF] = byte
    a = bytearray(img); put_settings(a, rec); open(fl, "wb").write(a)
    r = boot(f"play-led-b{byte}", fl, ARP, 3400, check_preset=False)
    s = r.state(100)
    assert (s[f"card.{PG}.0"], s[f"card.{PG}.3"]) == ("PEACH", want), (byte, s[f"card.{PG}.3"])
    assert set(play_led(r)) == ({"0"} if want == "OFF" else {"0", "1"}), (byte, play_led(r))
