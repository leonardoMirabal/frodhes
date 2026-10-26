# SPDX-License-Identifier: GPL-3.0-only
"""0.9.6's store migration on the host (BUGHUNT #28; firmware/src/frodhes_store.h, set_store.c, frodhes.c app_main).
0.9.5 kept its data where the stock firmware keeps its own (the FM-1: presets 0x97000 / 0x98000 and the pattern
0x99000 / 0x9A000 in V15's VM; the SMK-37: 0x9D000.. in the stock VM). 0.9.6 keeps the settings and the user presets
as one record in two copies (FM-1 0xFC000 / 0xFD000, SMK 0xEB000 / 0xEC000) and the pattern at 0xFE000 (SMK 0xED000
/ 0xEE000). The first boot reads 0.9.5's data once and writes it to the new places (the pattern first, the record
last: the commit); nothing old is ever written.

Here: a full 0.9.5 bank (every user slot, in v3, v2 and v1 format) migrates with every field; the settings and the
pattern come along; 0.9.5's sectors stay byte for byte; no 0.9.5 data: nothing written at boot; a corrupt bank;
a cut migration (the pattern's save fails; the record's header never written) migrates again at the next boot; the
SMK-37 reads 0x9D000.. only under Frodhes' owner tag. The power cut inside a save on the flash chip model:
tests/set_ab_test.c."""
import os
import random
import re
import struct
import zlib

from hostrun import cc, run
from test_persistence import (BANK_A, BANK_B, MAGIC, NSEC, O_OCT, O_PRESET, PAT, REC_B, SECTOR, SEQ_A, SEQ_B, SET,
                              boot, defaults, flash_file, newest_record, put_bank, put_sector, read_img, record_at,
                              settings_record, user_presets)
from test_smk_host import FLASH, smk_build

V3 = 96


def same_setup(a, b):
    """the setup the same (the panel is the preset's: preset_boot loads it as stored, as 0.9.5 did)"""
    return a[O_OCT] == b[O_OCT] and a[O_PRESET] == b[O_PRESET] and a[0] == b[0] and a[4:10] == b[4:10]


def v3_bank(seed):
    """16 used v3 records, every byte chosen: the head, the name, all 64 values, the 16 spare bytes"""
    rnd = random.Random(seed)
    n = len(defaults())
    recs = []
    for i in range(16):
        name = (f"MIG{i:02d} " + "".join(rnd.choice("ABCXYZ 0123") for _ in range(5))).encode()[:12].ljust(12)
        vals = bytes(rnd.randrange(128) for _ in range(64))
        spare = bytes(rnd.randrange(256) for _ in range(16))
        recs.append(struct.pack("<BBBB", 3, n, 1, 0) + name + vals + spare)
    return recs


def old_flash(path, bank_recs=None, bank_k=BANK_A, pattern=True):
    """a 0.9.5 flash: its settings (made by a run: U01 shown, OCT +1, a three-note pattern), its preset bank, its
    pattern; then saved as 0.9.5's five sectors (the record and the 0.9.6 pattern removed)"""
    lines = ["30 btn 1", cc(200, 63, 127)]
    for i, n in enumerate((60, 64, 67)):
        lines += [f"{300 + 100 * i} midi 0x90 {n} 100", f"{340 + 100 * i} midi 0x80 {n} 0"]
    lines += [cc(700, 63, 0), "800 midi 0xC0 17 0"]
    run(os.path.basename(path) + "-make", lines, 4500, flash=path)
    img = read_img(path)
    rec = settings_record(img)
    pat = bytes(img[PAT * SECTOR:(PAT + 1) * SECTOR])
    old = bytearray(b"\xff" * 5 * SECTOR)
    put_sector(old, SET, struct.pack("<III", MAGIC, 256, zlib.crc32(bytes(rec))) + bytes(rec) + struct.pack("<II", 4, 4 ^ 0xFFFFFFFF))
    if bank_recs:
        put_bank(old, bank_k, 9, bank_recs)
    if pattern:
        old[SEQ_A * SECTOR:(SEQ_A + 1) * SECTOR] = pat
    open(path, "wb").write(old)
    return old, rec


def test_full_v3_bank_migrates_every_field():
    """every user slot of a 0.9.5 bank (v3, 96 bytes each) lands in the record byte for byte; the settings and the
    pattern come along; 0.9.5's sectors are as they were; the record went to B (A: 0.9.5's settings, kept)"""
    fl = flash_file("mig-v3")
    recs = v3_bank(1)
    old, rec = old_flash(fl, recs, BANK_B)
    b = boot("mig-v3-1", fl, ["150 dump"], 400, check_preset=False)
    assert not b.fails and "FAIL flash" not in b.out, b.fails
    img = read_img(fl)
    k, data = newest_record(img)
    assert k == REC_B and record_at(img, SET) is None                  # (A holds 0.9.5's 256 bytes)
    assert bytes(user_presets(img)) == b"".join(recs)
    assert bytes(img[:5 * SECTOR]) == bytes(old)                        # 0.9.5's five sectors untouched
    assert same_setup(data, rec)                                        # the settings as 0.9.5 had them
    s = b.state(100)
    assert s["notes"] == "3" and s["oct"] == "1", (s["notes"], s["oct"])
    assert b.saves and b.saves[0] < 400 and len(b.seq_saves) == 1      # at boot: the pattern, then the record
    for i in range(16):
        assert s[f"u.{i + 1}"].startswith(f"U{i + 1:02d} MIG{i:02d}"), (i, s[f"u.{i + 1}"])
    # the next boot: the record, no migration again (no write at boot), all as before
    b2 = boot("mig-v3-2", fl, ["150 dump"], 400, check_preset=False)
    assert not b2.saves and not b2.seq_saves and b2.state(100)["notes"] == "3"
    assert all(b2.state(100)[f"u.{i + 1}"] == s[f"u.{i + 1}"] for i in range(16))
    assert bytes(read_img(fl)) == bytes(img)


def test_v2_and_v1_banks_migrate():
    """a 0.9.2 (v2, 64 bytes) and a 0.3 (v1, 48 bytes) bank: every slot's name and use migrate, the values convert
    as 0.9.5 converted them (presets.c, unchanged), and the record holds v3 records"""
    dflt = defaults()
    for ver, nold, size in ((2, 48, 64), (1, 32, 48)):
        fl = flash_file(f"mig-v{ver}")
        recs = []
        for i in range(16):
            p = bytearray(dflt[:nold])
            p[0], p[1] = i % 4, 10 + i
            used = 1 if i % 3 else 0
            recs.append(struct.pack("<BBBB", ver, nold, used, 0) + f"OLD{ver}S{i:02d}".encode().ljust(12) + bytes(p))
        assert len(recs[0]) == size
        old_flash(fl, recs)
        b = boot(f"mig-v{ver}-1", fl, ["150 dump"], 400, check_preset=False)
        assert not b.fails, b.fails
        tail = user_presets(read_img(fl))
        for i in range(16):
            r = tail[i * V3:(i + 1) * V3]
            assert r[0] == 3 and r[1] == len(dflt) and r[2] == (1 if i % 3 else 0), (ver, i, bytes(r[:4]))
            assert bytes(r[4:16]) == f"OLD{ver}S{i:02d}".encode().ljust(12) and r[16 + 1] == 10 + i, (ver, i)
            want = f"U{i + 1:02d} OLD{ver}S{i:02d}" if i % 3 else f"U{i + 1:02d} ----"
            assert b.state(100)[f"u.{i + 1}"] == want, (ver, i, b.state(100)[f"u.{i + 1}"])


def test_nothing_from_before_writes_nothing_at_boot():
    """a new unit (or one that never saved): no migration write; the first save writes the record (A)"""
    fl = flash_file("mig-none")
    b = run("mig-none", ["100 dump", "200 btn 1", "4000 dump"], 4500, flash=fl, env={"FRODHES_FLASHLOG": "1"})
    first = [int(t) for t in re.findall(r"^\s*(\d+) ms  flash ", b.out, re.M)]
    assert first and min(first) > 2000, first
    assert newest_record(read_img(fl))[0] == SET


def test_corrupt_old_bank():
    """0.9.5's two bank copies both broken: no user presets (as 0.9.5 had it), the settings and the pattern migrate"""
    fl = flash_file("mig-badbank")
    old, rec = old_flash(fl, v3_bank(2))
    old[BANK_A * SECTOR + 40] ^= 4
    open(fl, "wb").write(old)
    b = run("mig-badbank", ["300 dump"], 400, flash=fl, check=False)   # (no "expect sound": a record naming an empty
    # slot boots with a synced rate the panel does not match, mix-fix2 too: reported apart, not the store's)
    s = b.state(300)
    assert not b.fails and s["notes"] == "3" and s["u.1"] == "U01 ----", (s["notes"], s["u.1"])
    assert same_setup(settings_record(read_img(fl)), rec)


def test_cut_migration_runs_again():
    """the migration cut before its commit: (1) the pattern's save fails (the record is then not written); (2) the
    record's header never programmed (a cut in its save). Either way the next boot finds no record and migrates
    again from 0.9.5's places, all of it"""
    fl = flash_file("mig-cut")
    recs = v3_bank(3)
    old, rec = old_flash(fl, recs)
    b = run("mig-cut-1", ["100 dump"], 300, flash=fl, env={"FRODHES_SQ_FAIL": "1"}, check=False)
    assert "pattern erase failed" in b.out and not b.saves, b.saves
    img = read_img(fl)
    assert record_at(img, SET) is None and record_at(img, REC_B) is None and bytes(img[:5 * SECTOR]) == bytes(old)
    b = boot("mig-cut-2", fl, [], 300, check_preset=False)
    img = read_img(fl)
    assert b.saves and bytes(user_presets(img)) == b"".join(recs) and b.state(100)["notes"] == "3"
    img[REC_B * SECTOR:REC_B * SECTOR + 12] = b"\xff" * 12            # (2): the header not programmed
    open(fl, "wb").write(img)
    b = boot("mig-cut-3", fl, ["150 dump"], 300, check_preset=False)
    img = read_img(fl)
    assert b.saves and bytes(user_presets(img)) == b"".join(recs) and b.state(100)["notes"] == "3"
    assert bytes(img[:5 * SECTOR]) == bytes(old) and same_setup(settings_record(img), rec)


def smk_old_flash(path, recs):
    """0.9.5's SMK-37 flash: the FM-1's 0.9.5 image (its five sectors map to 0x9D000..0xA1FFF there) and the
    owner tag (the host's FRODHES_SMK_STOCK=1: the tag is not Frodhes')"""
    old, rec = old_flash(path, recs)
    open(path, "wb").write(old)
    return old, rec


def test_smk_migrates_only_under_its_owner_tag():
    """the SMK-37: 0.9.5's data in the stock VM (0x9D000..) migrates to 0xEB000.. when the owner tag is Frodhes';
    the stock's own data there (no tag) is never read: the starters, nothing written at boot. Neither run writes
    anything below 0xEB000"""
    b = smk_build()
    recs = v3_bank(4)
    fl = flash_file("mig-smk")
    old, rec = smk_old_flash(fl, recs)
    r = run("mig-smk-1", ["100 dump"], 300, flash=fl, env={"FRODHES_FLASHLOG": "1"}, binary=b, check=False)
    calls = [(op, int(at, 16)) for op, _, at, _ in FLASH.findall(r.out)]
    assert calls and all(0xEB000 <= at < 0xEF000 for _, at in calls), calls
    img = bytearray(open(fl, "rb").read())
    assert len(img) == 9 * SECTOR and bytes(img[:5 * SECTOR]) == bytes(old)
    a = img[5 * SECTOR:6 * SECTOR]
    assert struct.unpack("<II", a[:8]) == (MAGIC, 256 + 16 * V3)
    assert bytes(a[12 + 256:12 + 256 + 16 * V3]) == b"".join(recs) and r.state(100)["notes"] == "3"
    fl2 = flash_file("mig-smk-stock")
    smk_old_flash(fl2, recs)
    r = run("mig-smk-stock", ["100 dump"], 300, flash=fl2, env={"FRODHES_FLASHLOG": "1", "FRODHES_SMK_STOCK": "1"},
            binary=b, check=False)
    assert not FLASH.findall(r.out) and r.state(100)["notes"] == "0", r.out[-500:]
    assert not r.state(100)["u.1"].startswith("U01 MIG"), r.state(100)["u.1"]
