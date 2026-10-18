#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments
"""Lossless-codec study for the engines' big read-only tables (private/PULSE-COMPRESSION-PLAN.md).

Reads the generated headers (firmware/gen/rhodes_tables.h, bars_tables.h: the data the firmware links, so the
ground truth) and, per table, sizes every candidate codec exactly (bit counts of a real encoding, headers and side
tables included) and checks each one by a full integer round trip (encode -> decode -> compare, every value).

    python3 tools/pulse_codec_study.py            # the tables, the ratios, the best codec per table
    python3 tools/pulse_codec_study.py --emit X   # also write the best pulse codec's streams to X (a .npz), for a
                                                  # later C decoder's test vectors

Codecs (all lossless, integer only, each block decodable on its own = random access by (key, layer)):
  raw          the table as linked
  trim         the two zero pad samples of each 66-point pulse dropped (they are always 0)
  dN+fix       N-th order difference, fixed bit width per block (a 5-bit header): the cheapest decode
  dN+rice      N-th order difference, zigzag, Rice code with the best k per block (a 4-bit header)
  dN+huff      N-th order difference, one canonical Huffman code per table with an escape (the code's table counted)
  lpcP+rice    order-P integer predictor (Q12 coefficients per table), residual Rice per block
  layer+rice   Rhodes / bars: each layer predicted from the layer below of the same key (band, family), d2 of the
               difference, Rice; a block is then a key's (or band x family's) 8 layers: decoding layer l costs l+1
  key+rice     each pulse predicted from the same layer of the key below (Rhodes) / band below (bars)
  zlib / lzma  references only (a whole-table byte stream: no random access, not a firmware candidate)
"""
import argparse
import bz2
import heapq
import lzma
import os
import re
import sys
import zlib

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RH = os.path.join(ROOT, "firmware/gen/rhodes_tables.h")
BA = os.path.join(ROOT, "firmware/gen/bars_tables.h")


def table(path, name):
    s = open(path).read()
    m = re.search(r"\b" + name + r"\s*\[", s)
    j = s.index("= {", m.start()) + 2
    k = s.index("};", j)
    body = re.sub(r"/\*.*?\*/", "", s[j:k], flags=re.S)
    rows = re.findall(r"(?<!\w)[A-Za-z_]\w*", body)
    if rows:                                            # a pointer table (RH_PHI = {RH_PHI_0, RH_PHI_0}): its rows
        return np.concatenate([table(path, r) for r in rows])
    return np.array([int(x, 0) for x in re.findall(r"-?(?:0x[0-9a-fA-F]+|\d+)", body)], dtype=np.int64)


# ---------------------------------------------------------------- bit-level primitives (sizes exact)
def zz(v):
    v = np.asarray(v, dtype=np.int64)
    return np.where(v >= 0, 2 * v, -2 * v - 1)


def unzz(u):
    u = np.asarray(u, dtype=np.int64)
    return np.where(u & 1, -((u + 1) >> 1), u >> 1)


def diffs(x, order):
    d = np.asarray(x, dtype=np.int64).copy()
    for _ in range(order):
        d = np.diff(np.concatenate(([0], d)))
    return d


def undiff(d, order):
    x = np.asarray(d, dtype=np.int64).copy()
    for _ in range(order):
        x = np.cumsum(x)
    return x


def rice_bits(u, k):
    return int(np.sum((u >> k) + 1 + k))


def rice_best(u, kmax=30):
    best = None
    for k in range(kmax + 1):
        b = rice_bits(u, k)
        if best is None or b < best[0]:
            best = (b, k)
    return best


def width(u):
    m = int(np.max(u)) if len(u) else 0
    return m.bit_length()


class BitW:
    def __init__(self):
        self.bits = []

    def put(self, v, n):
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)

    def unary(self, q):
        self.bits.extend([1] * q)
        self.bits.append(0)


class BitR:
    def __init__(self, bits):
        self.b, self.p = bits, 0

    def get(self, n):
        v = 0
        for _ in range(n):
            v = v << 1 | self.b[self.p]
            self.p += 1
        return v

    def unary(self):
        q = 0
        while self.b[self.p]:
            q += 1
            self.p += 1
        self.p += 1
        return q


def rice_rt(u, k, hdr):
    """encode / decode one block (Rice, k in a hdr-bit header): returns (bits, ok)"""
    w = BitW()
    w.put(k, hdr)
    for v in u:
        w.unary(int(v) >> k)
        w.put(int(v) & ((1 << k) - 1), k)
    r = BitR(w.bits)
    k2 = r.get(hdr)
    out = [(r.unary() << k2) | r.get(k2) for _ in range(len(u))]
    return len(w.bits), list(map(int, u)) == out


# ---------------------------------------------------------------- Huffman (canonical, length-limited by escape)
def huff_lengths(freq):
    h = [(f, i, (s,)) for i, (s, f) in enumerate(freq.items())]
    heapq.heapify(h)
    L = {s: 0 for s in freq}
    if len(h) == 1:
        return {next(iter(freq)): 1}
    n = len(h)
    while len(h) > 1:
        f1, _, a = heapq.heappop(h)
        f2, _, b = heapq.heappop(h)
        for s in a + b:
            L[s] += 1
        heapq.heappush(h, (f1 + f2, n, a + b))
        n += 1
    return L


def huff_cost(vals, nsym=64, esc_bits=None):
    """one code per table: the nsym most frequent zigzag values get codes, the rest an escape + raw value; table
    cost = 1 byte per symbol value + 1 nibble length (a canonical code)"""
    u = zz(vals)
    uniq, cnt = np.unique(u, return_counts=True)
    order = np.argsort(-cnt)
    keep = set(int(x) for x in uniq[order[:nsym]])
    esc_bits = esc_bits or max(1, int(u.max()).bit_length())
    freq = {}
    nesc = 0
    for s, c in zip(uniq, cnt):
        s, c = int(s), int(c)
        if s in keep:
            freq[s] = c
        else:
            nesc += c
    if nesc:
        freq["E"] = nesc
    L = huff_lengths(freq)
    bits = sum(L[s] * c for s, c in freq.items()) + nesc * esc_bits
    tab = len(freq) * (2 * 8 + 4)                       # value (16 bit) + length (4 bit) per symbol
    return bits + tab, max(L.values())


def entropy_bits(vals):
    u, c = np.unique(vals, return_counts=True)
    p = c / c.sum()
    return float(-(c * np.log2(p)).sum())


# ---------------------------------------------------------------- integer LPC (fixed per table)
def lpc_fit(blocks, order):
    X, y = [], []
    for b in blocks:
        b = np.concatenate((np.zeros(order, dtype=np.int64), b))
        for i in range(order, len(b)):
            X.append(b[i - order:i][::-1])
            y.append(b[i])
    X, y = np.array(X, dtype=float), np.array(y, dtype=float)
    c = np.linalg.lstsq(X, y, rcond=None)[0]
    return [int(round(v * 4096)) for v in c]


def lpc_res(b, coef):
    o = len(coef)
    x = np.concatenate((np.zeros(o, dtype=np.int64), np.asarray(b, dtype=np.int64)))
    r = np.empty(len(b), dtype=np.int64)
    for i in range(o, len(x)):
        p = sum(coef[j] * int(x[i - 1 - j]) for j in range(o))
        r[i - o] = x[i] - ((p + 2048) >> 12)
    return r


def lpc_dec(r, coef):
    o = len(coef)
    x = [0] * o
    for v in r:
        p = sum(coef[j] * x[-1 - j] for j in range(o))
        x.append(int(v) + ((p + 2048) >> 12))
    return np.array(x[o:], dtype=np.int64)


# ---------------------------------------------------------------- per-table study
def study_blocks(name, blocks, raw_bytes, label, notes, rt=True):
    """blocks: list of 1-D int arrays (the random-access unit). Returns rows (codec, bytes, ratio, ok)."""
    rows = [("raw", raw_bytes, True)]
    allv = np.concatenate(blocks)
    for order in (1, 2, 3):
        fix = rice = 0
        ok = True
        dv = []
        for b in blocks:
            d = diffs(b, order)
            dv.append(d)
            u = zz(d)
            fix += 5 + width(u) * len(u)
            bits, k = rice_best(u)
            if rt:
                nb, good = rice_rt(u, k, 5)
                ok &= good and nb == bits + 5 and np.array_equal(undiff(unzz(u), order), b)
            rice += bits + 5
        # partitioned Rice (32-value partitions, a 5-bit k each), the first `order` values raw (32 bit): the
        # FLAC way, so a large first value does not force a large k on the whole block
        pb = 0
        for b in blocks:
            u = zz(diffs(b, order)[order:])
            pb += 32 * order + sum(rice_best(u[i:i + 32], 31)[0] + 5 for i in range(0, len(u), 32))
        rows.append((f"d{order}+rice/32 (warm-up raw)", (pb + 7) // 8 + 2 * len(blocks), True))
        rows.append((f"d{order}+fix", (fix + 7) // 8 + 2 * len(blocks), True))
        rows.append((f"d{order}+rice", (rice + 7) // 8 + 2 * len(blocks), ok))
        hb, maxl = huff_cost(np.concatenate(dv))
        rows.append((f"d{order}+huff (max {maxl} b)", (hb + 7) // 8 + 2 * len(blocks), True))
        rows.append((f"d{order} order-0 entropy (bound)", int(entropy_bits(np.concatenate(dv)) / 8), True))
    for order in (2, 3):
        coef = lpc_fit(blocks, order)
        bits = 0
        ok = True
        for b in blocks:
            r = lpc_res(b, coef)
            if rt:
                ok &= np.array_equal(lpc_dec(r, coef), b)
            bits += rice_best(zz(r))[0] + 5
        rows.append((f"lpc{order}+rice {coef}", (bits + 7) // 8 + 2 * len(blocks) + 2 * order, ok))
    rawb = np.asarray(allv, dtype=np.int64)
    w = 2 if rawb.min() >= -32768 and rawb.max() < 65536 else 4
    by = rawb.astype("<i2" if w == 2 and rawb.min() < 0 else "<u2" if w == 2 else "<i4").tobytes()
    d2 = diffs(rawb, 2).astype("<i4").tobytes()
    rows.append(("ref: zlib-9 whole table", len(zlib.compress(by, 9)), True))
    rows.append(("ref: lzma whole table", len(lzma.compress(by, preset=9 | lzma.PRESET_EXTREME)), True))
    rows.append(("ref: lzma of d2 (int32)", len(lzma.compress(d2, preset=9 | lzma.PRESET_EXTREME)), True))
    rows.append(("ref: bz2 whole table", len(bz2.compress(by, 9)), True))
    return rows


def pulses_rows(P, key_axis_name):
    """P: [..., 8 layers, 66]: the pulse banks. Extra predictors across layers and keys."""
    rows = []
    sh = P.shape
    flat = P.reshape(-1, 8, 66)[:, :, :64]            # the 2 pad zeros are implicit
    # layer prediction: layer l - layer l-1, then d2
    bits = 0
    ok = True
    for g in flat:
        prev = np.zeros(64, dtype=np.int64)
        for l in range(8):
            d = diffs(g[l] - prev, 2)
            u = zz(d)
            b, k = rice_best(u)
            bits += b + 5
            if ok:
                ok &= np.array_equal(undiff(unzz(u), 2) + prev, g[l])
            prev = g[l]
    rows.append(("layer+d2+rice (block = 8 layers)", (bits + 7) // 8 + 2 * flat.shape[0], ok))
    # key prediction (same layer, key / band below; families separately for the bars)
    if P.ndim == 3:
        seq = [P[:, l, :64] for l in range(8)]
    else:
        seq = [P[:, f, l, :64] for f in range(P.shape[1]) for l in range(8)]
    bits = 0
    for s in seq:
        prev = np.zeros(64, dtype=np.int64)
        for row in s:
            bits += rice_best(zz(diffs(row - prev, 2)))[0] + 5
            prev = row
    rows.append((f"{key_axis_name}+d2+rice (block = a column)", (bits + 7) // 8 + 2 * len(seq), True))
    return rows


# ---------------------------------------------------------------- the two proposed pulse formats, as real bit streams
# A pulse is 64 points (the 2 pad zeros of the 66-point row are not stored: the decoder writes them). Predictors:
#   own3 / own4   3rd / 4th difference of the pulse itself (x[-1..-4] = 0)
#   key2 / key3   the pulse minus the same layer of the key (band) below, then 2nd / 3rd difference
#   lay2 / lay3   the pulse minus the layer below of the same key, then 2nd / 3rd difference
#   2d2           minus (key below + layer below - both below), 2nd difference
#   avg2          minus the mean (>> 1) of key below and layer below, 2nd difference
# A missing neighbour (first key, first layer) is 0. Each pulse: 3-bit predictor, 4-bit Rice k, 64 Rice codes.
# Format PZ-A ("per pulse", for a note-on decoder): own3 / own4 only (1-bit predictor), a 16-bit offset per pulse.
# Format PZ-B ("whole bank", for a decoder at the engine change): all eight, sequential, no offsets.
PREDS = ("own3", "own4", "key2", "key3", "lay2", "lay3", "2d2", "avg2")
ESC = 24                                        # a quotient >= ESC: ESC ones, a 0, then the value raw in RAWB bits
RAWB = 22                                       # (bounds the decoder's unary loop; |d4| < 2^20 so zigzag < 2^21)


def _ref(name, kp, lp, dg):
    if name.startswith("own"):
        return 0
    if name.startswith("key"):
        return kp
    if name.startswith("lay"):
        return lp
    if name == "2d2":
        return kp + lp - dg
    return (kp + lp) >> 1


def _order(name):
    return int(name[-1])


def _rice_put(w, u, k):
    for v in u:
        v = int(v)
        if v >> k >= ESC:
            assert v < 1 << RAWB
            w.unary(ESC)
            w.put(v, RAWB)
        else:
            w.unary(v >> k)
            w.put(v & ((1 << k) - 1), k)


def _rice_get(r, k, n):
    out = []
    for _ in range(n):
        q = r.unary()
        out.append(r.get(RAWB) if q >= ESC else (q << k) | r.get(k))
    return np.array(out, dtype=np.int64)


def pulse_groups(P):
    """[groups][keys][8][64]: Rhodes one group (88 keys); bars one group per family (17 bands)"""
    if P.ndim == 3:
        return [P[..., :64]]
    return [P[:, f, :, :64] for f in range(P.shape[1])]


def encode_bank(P, allowed):
    """sequential (group, key, layer); returns (bits, per-pulse bit offsets)"""
    w = BitW()
    offs = []
    pbits = 1 if len(allowed) == 2 else 3
    for g in pulse_groups(P):
        z = np.zeros(64, dtype=np.int64)
        for k in range(g.shape[0]):
            for l in range(8):
                x = g[k, l]
                kp = g[k - 1, l] if k else z
                lp = g[k, l - 1] if l else z
                dg = g[k - 1, l - 1] if k and l else z
                best = None
                for pi, name in enumerate(allowed):
                    u = zz(diffs(x - _ref(name, kp, lp, dg), _order(name)))
                    b, kk = rice_best(u, 15)
                    if best is None or b < best[0]:
                        best = (b, pi, kk, u)
                offs.append(len(w.bits))
                w.put(best[1], pbits)
                w.put(best[2], 4)
                _rice_put(w, best[3], best[2])
    return w.bits, offs


def decode_bank(bits, shape, allowed):
    """the decoder the firmware would run (same order, integers only): returns the bank with its 66-point rows"""
    r = BitR(bits)
    pbits = 1 if len(allowed) == 2 else 3
    out = np.zeros(shape, dtype=np.int64)
    view = pulse_groups(out)                         # views into out
    for g in view:
        z = np.zeros(64, dtype=np.int64)
        for k in range(g.shape[0]):
            for l in range(8):
                name = allowed[r.get(pbits)]
                kk = r.get(4)
                d = unzz(_rice_get(r, kk, 64))
                kp = g[k - 1, l] if k else z
                lp = g[k, l - 1] if l else z
                dg = g[k - 1, l - 1] if k and l else z
                g[k, l] = undiff(d, _order(name)) + _ref(name, kp, lp, dg)
    return out


APART = 16                                      # PZ-A: a 4-bit Rice k per 16-point partition


def encode_A(P):
    """PZ-A: every pulse on its own (own3 / own4, 1 bit), 4 partitions with their own k; word-aligned starts"""
    w = BitW()
    offs = []
    for g in pulse_groups(P):
        for x in g.reshape(-1, 64):
            best = None
            for pi, name in enumerate(("own3", "own4")):
                u = zz(diffs(x, _order(name)))
                ks = [rice_best(u[i:i + APART], 15)[1] for i in range(0, 64, APART)]
                b = sum(rice_bits(u[i:i + APART], k) for i, k in zip(range(0, 64, APART), ks))
                if best is None or b < best[0]:
                    best = (b, pi, ks, u)
            while len(w.bits) % 16:
                w.bits.append(0)
            offs.append(len(w.bits))
            w.put(best[1], 1)
            for i, k in zip(range(0, 64, APART), best[2]):
                w.put(k, 4)
            for i, k in zip(range(0, 64, APART), best[2]):
                _rice_put(w, best[3][i:i + APART], k)
    return w.bits, offs


def decode_one_A(bits, off):
    """PZ-A random access: one pulse from its offset (what a note-on decoder would run)"""
    r = BitR(bits)
    r.p = off
    name = ("own3", "own4")[r.get(1)]
    ks = [r.get(4) for _ in range(64 // APART)]
    u = np.concatenate([_rice_get(r, k, APART) for k in ks])
    return undiff(unzz(u), _order(name))


def formats_rows(P, name):
    rows = []
    raw = P.size * 2
    npulse = P.size // 66
    # PZ-A: per pulse, own predictors, offsets (16-bit word offsets: pulses start word-aligned -> padding counted)
    bits, offs = encode_A(P)
    flatP = np.concatenate([g.reshape(-1, 64) for g in pulse_groups(P)])      # the encoder's order
    ok = all(np.array_equal(decode_one_A(bits, o), flatP[i]) for i, o in enumerate(offs))
    sizeA = (len(bits) + 15) // 16 * 2 + 2 * npulse
    rows.append(("PZ-A per pulse (own3/4, k per 16, offsets)", sizeA, ok))
    # PZ-B: whole bank, eight predictors
    bits, _ = encode_bank(P, PREDS)
    dec = decode_bank(bits, P.shape, PREDS)
    ok = np.array_equal(dec, P)
    rows.append(("PZ-B whole bank (8 predictors, sequential)", (len(bits) + 7) // 8, ok))
    return rows


def _bytes(bits):
    bits = list(bits) + [0] * (-len(bits) % 8 + 32)  # + 4 zero bytes: the C reader may look ahead
    return bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


def emit_c(d, R, B):
    """the streams as C arrays (MSB-first bytes), the reference banks, the PZ-A offsets; then the C check"""
    import subprocess
    os.makedirs(d, exist_ok=True)
    L = ["/* GENERATED by tools/pulse_codec_study.py --emit-c (host study only, not firmware) */", "#include <stdint.h>"]

    def arr(name, typ, vals):
        L.append(f"static const {typ} {name}[{len(vals)}] = {{" + ",".join(map(str, vals)) + "};")
    for tag, P in (("RH", R), ("BAR", B)):
        bits, _ = encode_bank(P, PREDS)
        arr(f"PZB_{tag}", "uint8_t", list(_bytes(bits)))
        bits, offs = encode_A(P)
        arr(f"PZA_{tag}", "uint8_t", list(_bytes(bits)))
        arr(f"PZA_{tag}_OFF", "uint16_t", [o // 16 for o in offs])     # 16-bit word offsets
        arr(f"REF_{tag}", "int16_t", list(P.reshape(-1)))
    path = os.path.join(d, "pz_streams.h")
    open(path, "w").write("\n".join(L) + "\n")
    exe = os.path.join(d, "pulse_codec_proto")
    src = os.path.join(ROOT, "tools", "pulse_codec_proto.c")
    subprocess.run(["cc", "-O2", "-Wall", "-I", d, "-o", exe, src], check=True)
    subprocess.run([exe], check=True)


def show(title, rows, raw):
    print(f"\n== {title}  (raw {raw} B)")
    best = min((r for r in rows if not r[0].startswith("ref") and "bound" not in r[0]), key=lambda r: r[1])
    for n, b, ok in rows:
        print(f"  {n:44s} {b:7d} B  {raw / max(b, 1):5.2f}x  {'' if ok else 'ROUND TRIP FAILED'}")
    print(f"  -> best firmware candidate: {best[0]} {best[1]} B, saves {raw - best[1]} B")
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-c", metavar="DIR", help="write DIR/pz_streams.h (PZ-A / PZ-B streams + the reference "
                    "banks) for tools/pulse_codec_proto.c, then build and run that C check on the host")
    ap.add_argument("--fast", action="store_true", help="skip the bit-level round trips")
    a = ap.parse_args()
    rt = not a.fast
    summary = []

    R = table(RH, "RH_PULSE").reshape(88, 8, 66)
    B = table(BA, "BAR_PULSE").reshape(17, 5, 8, 66)
    assert not R[..., 64:].any() and not B[..., 64:].any()
    for nm, P, raw, ax in (("RH_PULSE [88 key][8 layer][66] int16", R, R.size * 2, "key"),
                           ("BAR_PULSE [17 band][5 fam][8 layer][66] int16", B, B.size * 2, "band")):
        blocks = [b[:64] for b in P.reshape(-1, 66)]
        rows = study_blocks(nm, blocks, raw, nm, [], rt) + pulses_rows(P, ax) + formats_rows(P, nm)
        rows.insert(1, ("trim (64 of 66 points)", raw * 64 // 66, True))
        summary.append((nm, raw, show(nm, rows, raw)))
        print(f"  uniq pulses {len({b.tobytes() for b in P.reshape(-1, 66)})} of {P.reshape(-1, 66).shape[0]};"
              f" peak 32767 in every pulse: {bool((P.max(-1) == 32767).all())}; min {P.min()}")

    # the other big tables: blocks = rows along their natural random-access unit
    others = [
        (RH, "RH_PHI", (2, 2049), 4, "per sample (pickup Hermite), 2 rows"),
        (RH, "RH_SLOPE", (2, 2049), 4, "per sample (pickup Hermite), 2 rows"),
        (BA, "BAR_PRATE", (88, 50), 4, "note-on, per key (5 fam x 10 anchors)"),
        (BA, "BAR_PAMP", (88, 50), 2, "note-on, per key"),
        (RH, "RH_PRATE", (88, 10), 4, "note-on, per key"),
        (RH, "RH_PAMP", (88, 10), 2, "note-on, per key"),
        (BA, "BAR_POS", (17, 17 * 8), 2, "note-on, per band"),
        (BA, "BAR_POSG", (17, 85), 2, "note-on, per band"),
        (BA, "BAR_TUBEG", (17, 85), 2, "note-on, per band"),
    ]
    for path, nm, shp, w, use in others:
        t = table(path, nm).reshape(shp)
        raw = t.size * w
        blocks = list(t)
        rows = study_blocks(nm, blocks, raw, nm, [], rt)
        if shp[0] == 2 and np.array_equal(t[0], t[1]):     # RH_PHI / RH_SLOPE: both pickup families are equal
            rows.insert(1, ("dedup: the 2 rows are identical (one row kept)", raw // 2, True))
            sub = study_blocks(nm, [t[0]], raw // 2, nm, [], rt)
            rows += [("dedup + " + n, b, ok) for n, b, ok in sub[1:] if not n.startswith("ref")]
        summary.append((f"{nm} {list(shp)} ({use})", raw, show(f"{nm} {list(shp)}: {use}", rows, raw)))
    # the key structs: fields as columns (each field delta-coded along the keys)
    for path, nm, nk, nf, w, use in ((RH, "RH_KEYS", 88, 42, 4, "note-on + per block via v->k (pointer)"),
                                     (BA, "BAR_KEYS", 88, 62, 4, "note-on + per block via v->k (pointer)")):
        t = table(path, nm)
        raw = {"RH_KEYS": 14784, "BAR_KEYS": 21824}[nm]
        nf = t.size // nk
        t = t.reshape(nk, nf)
        cols = [t[:, j] for j in range(nf)]
        rows = study_blocks(nm, cols, raw, nm, [], rt)
        summary.append((f"{nm} ({use})", raw, show(f"{nm} [{nk} keys x {nf} fields], field-major: {use}", rows, raw)))

    print("\n== summary (best firmware candidate per table)")
    tot_raw = tot_c = 0
    for nm, raw, best in summary:
        print(f"  {nm:70s} {raw:7d} -> {best[1]:7d} B  ({raw / best[1]:4.2f}x, -{raw - best[1]} B)  {best[0]}")
        tot_raw += raw
        tot_c += best[1]
    print(f"  total {tot_raw} -> {tot_c} B, saves {tot_raw - tot_c} B")

    if a.emit_c:
        emit_c(a.emit_c, R, B)


if __name__ == "__main__":
    sys.exit(main())
