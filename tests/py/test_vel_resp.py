# SPDX-License-Identifier: GPL-3.0-only
"""MIDI RESPONSE (0.9.10): settings_t.vel_resp, the velocity response (params.c N_VRESP / VRESP_N; rhodes.c
rh_touch_mode, read at each note-on). A former reserved byte, so every older record reads 0 = A (DEEP); anything at
or above VRESP_N is sanitized to A. Saved with the settings and back after a power cycle (FRODHES_FLASH), the engine's
copy with it; the record stays 256 B, and on the SMK-37 build smk_mst and play_off keep their places. CC 40 sets it.
The card, its ends and CC 40's bands: tests/scripts/vel_resp.txt."""
import pytest

from hostrun import run
from test_persistence import boot, flash_file, put_settings, read_img, settings_record
from test_play_led import offsets

MIDI = ["100 btn 7", "200 btn 7", "300 btn 7"]                  # GLO three times: TEMPO, SETUP, MIDI (ends the splash)
PG = 18                                                         # ui.c PG_MIDI (host: card.<page>.<k>)
O_VRESP = 236                                                   # settings_t vel_resp (FM-1: after play_off at 235)
O_VRESP_SMK = 238                                               # the SMK-37 build: after smk_mst (235) and play_off (237)


def test_layout():
    """256 B on both boards; vel_resp out of the reserve, after play_off; nothing before it moved"""
    assert offsets([], ["flags2", "play_off", "vel_resp"]) == \
        {"flags2": 234, "play_off": 235, "vel_resp": O_VRESP, "size": 256}
    assert offsets(["-DBOARD_SMK37=1", "-DFR_VOICES=7u"], ["flags2", "smk_mst", "play_off", "vel_resp"]) == \
        {"flags2": 234, "smk_mst": 235, "play_off": 237, "vel_resp": O_VRESP_SMK, "size": 256}


def test_saved_and_back_after_a_power_cycle():
    fl = flash_file("vel-resp-pc")
    r1 = run("vel-resp-pc1", MIDI + ["350 dump", "400 turn 5 1", "500 dump", "4500 dump"], 4700, flash=fl)
    assert (r1.state(350)["label3"], r1.state(350)["card3"], r1.state(350)["vresp"]) == ("RESPONSE", "DEEP", "0/0")
    assert (r1.state(500)["card3"], r1.state(500)["vresp"]) == ("EVEN", "1/1")
    assert r1.saves, "the settings were not saved"
    rec = settings_record(read_img(fl))
    assert len(rec) == 256 and rec[O_VRESP] == 1, (len(rec), rec[O_VRESP])
    r2 = boot("vel-resp-pc2", fl, ["300 dump"], 400)
    assert (r2.state(300)[f"card.{PG}.3"], r2.state(300)["vresp"]) == ("EVEN", "1/1")   # the engine's copy at boot too
    # back to A by CC 40: saved as 0, A after the next power cycle
    r3 = run("vel-resp-pc3", ["300 midi 0xB0 40 0", "400 dump", "4500 dump"], 4700, flash=fl, check=False)
    assert r3.state(400)["vresp"] == "0/0" and r3.saves
    assert settings_record(read_img(fl))[O_VRESP] == 0
    r4 = boot("vel-resp-pc4", fl, ["300 dump"], 400)
    assert (r4.state(300)[f"card.{PG}.3"], r4.state(300)["vresp"]) == ("DEEP", "0/0")


@pytest.mark.parametrize("byte, want", [(0, "DEEP"), (1, "EVEN"), (2, "DEEP"), (3, "DEEP"), (255, "DEEP")])
def test_old_records_and_bad_bytes_load(byte, want):
    """0 (every record before: a former reserved byte) A; 1 B; at or above VRESP_N (2 today: C not built) A"""
    fl = flash_file(f"vel-resp-b{byte}")
    run(f"vel-resp-b{byte}-0", MIDI + ["400 turn 2 1", "4500 dump"], 4700, flash=fl)   # a record (CHANNEL 1)
    img = read_img(fl)
    rec = bytearray(settings_record(img))
    assert len(rec) == 256 and rec[O_VRESP] == 0
    rec[O_VRESP] = byte
    a = bytearray(img); put_settings(a, rec); open(fl, "wb").write(a)
    r = boot(f"vel-resp-b{byte}", fl, ["300 dump"], 400, check_preset=False)
    s = r.state(300)
    eng = {"DEEP": "0", "EVEN": "1"}[want]
    assert (s[f"card.{PG}.0"], s[f"card.{PG}.3"], s["vresp"]) == ("1", want, eng + "/" + eng), (byte, s["vresp"])
