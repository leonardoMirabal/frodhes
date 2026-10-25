# SPDX-License-Identifier: GPL-3.0-only
"""The PERFORM knobs' defaults (after 0.9.4): DRIVE, FILTER (the cutoff), DELAY, REVERB, the chain's order; up to
0.9.4 TREM/WAH, PHASER, FILTER, DELAY. A new FM-1 plays the new ones (perf[] all 0 = the default, flags bit 64 set).
A 0.9.4 settings record (no flags bit 64) is moved once (params.c params_perf_update): an assignment that plays the
old defaults on all four knobs (never changed: 0s, or set back to them by hand) gets the new ones; any other is kept
exactly, written out as it plays so it stays so."""
from hostrun import cc, run
from test_persistence import O_FLAGS, boot, flash_file, put_settings, read_img, settings_record

O_PERF = 121                                               # settings_t perf[4]: ui.c PERF_T index + 1, 0 = the default
PERF_NEW = ["DRIVE", "FILTER", "DELAY", "REVERB"]
PERF_OLD_IDX = [0, 1, 27, 3]                               # TREM/WAH, PHASER, FILTER, DELAY


def labels(s):
    return [s[f"label.0.{k}"] for k in range(4)]


def fresh_record():
    fl = flash_file("perf-new")
    r = run("perf-new", ["100 dump", cc(200, 114, 127)], 3000, flash=fl)      # a setup edit, so it saves
    assert labels(r.state(100)) == PERF_NEW and r.saves
    rec = settings_record(read_img(fl))
    assert rec[O_FLAGS] & 64 and list(rec[O_PERF:O_PERF + 4]) == [0, 0, 0, 0], (rec[O_FLAGS], list(rec[O_PERF:O_PERF + 4]))
    return fl, rec


def boot_with(tag, fl, rec, flags_off=0, perf=None):
    old = bytearray(rec)
    old[O_FLAGS] &= ~flags_off & 0xFF
    if perf is not None:
        old[O_PERF:O_PERF + 4] = bytes(perf)
    img = read_img(fl)
    put_settings(img, old)
    f2 = flash_file(tag)
    open(f2, "wb").write(img)
    return f2


CASES = {"zeros": ([0, 0, 0, 0], PERF_NEW),                                    # never changed
         "old-by-hand": ([i + 1 for i in PERF_OLD_IDX], PERF_NEW),             # set back to the old defaults
         "old-mixed": ([0, 2, 0, PERF_OLD_IDX[3] + 1], PERF_NEW),
         "bad-index": ([0, 200, 0, 0], PERF_NEW),                              # out of range plays the default
         "changed-k1": ([5, 0, 0, 0], ["REVERB", "PHASER", "FILTER", "DELAY"]),          # REVERB = index 4
         "changed-all": ([24, 6, 1, 26], ["KEY VEL", "DRIVE", "TREM/WAH", "ARP RATE"])}


def test_perform_defaults_and_the_update_from_0_9_4():
    fl, rec = fresh_record()
    for name, (perf, want) in CASES.items():
        f2 = boot_with(f"perf-{name}", fl, rec, flags_off=64, perf=perf)      # a 0.9.4 record
        b = run(f"perf-{name}", ["100 dump", cc(200, 114, 0)], 3000, flash=f2)   # (a setup change: it saves)
        assert labels(b.state(100)) == want, (name, labels(b.state(100)))
        saved = settings_record(read_img(f2))
        stored = list(saved[O_PERF:O_PERF + 4])
        assert saved[O_FLAGS] & 64, name
        if want == PERF_NEW:
            assert stored == [0, 0, 0, 0], (name, stored)
        else:                                                                  # written out as it plays
            assert stored == [v if v else PERF_OLD_IDX[k] + 1 for k, v in enumerate(perf)], (name, stored)
        assert labels(boot(f"perf-{name}-2", f2).state(100)) == want, name    # on again: the same


def test_an_updated_record_keeps_its_choice():
    """after the update (bit 64 set) a choice of the old defaults is the user's own and stays"""
    fl, rec = fresh_record()
    f2 = boot_with("perf-kept", fl, rec, perf=[1, 2, 28, 4])
    assert labels(boot("perf-kept", f2).state(100)) == ["TREM/WAH", "PHASER", "FILTER", "DELAY"]
