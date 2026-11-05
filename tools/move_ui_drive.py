#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Drive the Move's own controls and read its screen, for checking Frodhes' screen on the device (no finger needed).

Schwung's test bus (schwung-testd, its src/host/test_daemon): INJECT_MIDI puts one USB-MIDI packet into the Move's
MIDI_IN, and with shadow_control->inject_as_hardware set (byte 108 of /dev/shm/schwung-control) the shim delivers it
AS IF THE HARDWARE SENT IT: the shadow UI, its claims and Move all see a press. The screen comes from the shim's live
mirror (/dev/shm/schwung-display-live, 1024 B, 8 pages x 128 columns, bit = row; written while display_mirror, byte
33, is 1).

    tools/move_ui_drive.py arm                  start schwung-testd (cwd /data/UserData/frodhes-bench), set
                                                inject_as_hardware and display_mirror (the previous values kept)
    tools/move_ui_drive.py seq OP [OP ...]      play the ops in order (below); `shot:NAME` writes NAME.png
    tools/move_ui_drive.py state                display_mode, inject_as_hardware, display_mirror, ui_slot
    tools/move_ui_drive.py disarm               the two bytes back, schwung-testd stopped

OPs: tap:CC (press 127, 90 ms, release 0) · cc:CC:V · knob:K:D (K 0-7, D detents, + clockwise, one CC a detent) ·
touch:K:1|0 (note K, 127 / 0) · jog:D · click · back · shift:1|0 · sleep:MS · watch:MS (the large card -> overview
edges, ms after the last packet sent, from the mirror) · shot:NAME (--out DIR, default
build/move/ui-device). Takes the MOVE lock. Writes nothing on the Move but the two shm bytes and the daemon's log in
/data/UserData/frodhes-bench."""
import argparse
import os
import struct
import subprocess
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HOST = os.environ.get("MOVE_HOST", "ableton@move.local")
BENCH = "/data/UserData/frodhes-bench"
KEEP = BENCH + "/ui_drive.prev"           # the two bytes as they were before arm
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
JOG_CC, CLICK_CC, BACK_CC, SHIFT_CC, KNOB_CC = 14, 3, 51, 49, 71

# runs on the Move (python3 there): argv[1] the command, argv[2:] its words
DEVICE = r'''
import mmap, os, socket, subprocess, sys, time
CTRL, LIVE, BENCH, KEEP = "/dev/shm/schwung-control", "/dev/shm/schwung-display-live", %r, %r
def ctrl():
    f = os.open(CTRL, os.O_RDWR); m = mmap.mmap(f, 256); os.close(f); return m
def testd_up():
    return subprocess.run(["pidof", "schwung-testd"], capture_output=True).returncode == 0
cmd, args = sys.argv[1], sys.argv[2:]
if cmd == "arm":
    m = ctrl()
    if not os.path.exists(KEEP):
        open(KEEP, "w").write("%%d %%d\n" %% (m[108], m[33]))
    m[108] = 1; m[33] = 1; m.close()
    if not testd_up():
        subprocess.Popen(["nohup", "/data/UserData/schwung/bin/schwung-testd"], cwd=BENCH,
                         stdout=open(BENCH + "/testd.log", "a"), stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(20):
            time.sleep(0.25)
            if testd_up(): break
    time.sleep(0.5)
    print("armed testd=%%d" %% testd_up())
elif cmd == "disarm":
    m = ctrl()
    a, b = 0, 0
    if os.path.exists(KEEP):
        a, b = map(int, open(KEEP).read().split()); os.remove(KEEP)
    m[108] = a; m[33] = b; m.close()
    subprocess.run(["killall", "schwung-testd"], capture_output=True)
    print("disarmed inject_as_hardware=%%d display_mirror=%%d" %% (a, b))
elif cmd == "state":
    m = ctrl()
    print("display_mode=%%d ui_slot=%%d selected_slot=%%d inject_as_hardware=%%d display_mirror=%%d testd=%%d" %%
          (m[0], m[6], m[20], m[108], m[33], testd_up()))
    m.close()
elif cmd == "seq":
    s = None; last_sent = time.time()
    def inject(pkt):
        global s
        for _ in range(6):
            if s is None:
                s = socket.create_connection(("127.0.0.1", 47777), timeout=5); s.settimeout(0.8)
            s.sendall(("INJECT_MIDI %%s\n" %% pkt).encode())
            try: r = s.recv(4096).decode(errors="replace").strip()
            except socket.timeout: r = ""
            if r.startswith("OK"): return
            if not r:
                s.close(); s = None
            time.sleep(0.12)
        print("DROP " + pkt); sys.stdout.flush()
    def ask(line):
        global s
        if s is None:
            s = socket.create_connection(("127.0.0.1", 47777), timeout=5); s.settimeout(0.8)
        s.sendall((line + "\n").encode())
        r = b""
        try:
            while not r.endswith(b"\n"): r += s.recv(65536)
        except socket.timeout: pass
        return r.decode(errors="replace").strip()
    for w in args:
        if w.startswith("sleep:"): time.sleep(int(w[6:]) / 1000.0)
        elif w.startswith("set:"):
            slot, rest = w[4:].split(":", 1)
            key, _, val = rest.rpartition(":")
            ask("SLOT " + slot)
            print("SET %%s:%%s=%%s %%s" %% (slot, key, val, ask("SET_PARAM %%s %%s" %% (key, val)))); sys.stdout.flush()
        elif w.startswith("get:"):
            slot, key = w[4:].split(":", 1)
            ask("SLOT " + slot)
            r = ask("GET_PARAM " + key)
            print("GOT %%s:%%s %%s" %% (slot, key, r[:160] + (" ... (%%d B)" %% len(r) if len(r) > 160 else "")))
            sys.stdout.flush()
        elif w.startswith(("dump:", "load:")):
            # dump:SLOT:KEY:NAME / load:SLOT:KEY:NAME: a value too long for a line (the state) to / from BENCH/NAME
            slot, rest = w[5:].split(":", 1)
            key, name = rest.rsplit(":", 1)          # (the key may hold a colon: synth:state)
            ask("SLOT " + slot)
            cmd = "DUMP_PARAM_FILE" if w.startswith("dump:") else "SET_PARAM_FILE"
            print("%%s %%s:%%s %%s" %% ("GOT" if cmd[0] == "D" else "SET", slot, key,
                                    ask("%%s %%s %%s/%%s" %% (cmd, key, BENCH, name)))); sys.stdout.flush()
        elif w == "shot":
            m = ctrl(); live = m[33]; m.close()
            if not live: print("STALE")
            print("SHOT " + open(LIVE, "rb").read(1024).hex()); sys.stdout.flush()
        elif w.startswith("watch:"):
            # from the last packet sent: when the large card goes (its bar frame = a row lit across all 128 columns,
            # rows 44..56; the overview has none), polled every 2 ms from the live mirror
            t0, until, card, seen = last_sent, time.time() + int(w[6:]) / 1000.0, None, []
            while time.time() < until:
                fb = open(LIVE, "rb").read(1024)
                c = any(all(fb[(r >> 3) * 128 + x] >> (r & 7) & 1 for x in range(128)) for r in range(44, 57))
                if c != card:
                    seen.append("%%s@%%d" %% ("card" if c else "overview", round((time.time() - t0) * 1000)))
                    card = c
                time.sleep(0.002)
            print("WATCH " + " ".join(seen)); sys.stdout.flush()
        else:
            inject(w)
            last_sent = time.time()
    if s is not None: s.close()
'''


def ssh_py(words, check=True):
    script = DEVICE % (BENCH, KEEP)
    r = subprocess.run(SSH + [HOST, "mkdir -p %s && python3 - %s" % (BENCH, " ".join(words))],
                       input=script.encode(), capture_output=True)
    if check and r.returncode:
        raise SystemExit("move_ui_drive: %s" % (r.stderr.decode() or r.stdout.decode()).strip())
    return r.stdout.decode()


def pkt(status, d1, d2):
    cin = status >> 4
    return "%02X%02X%02X%02X" % (cin, status, d1 & 127, d2 & 127)


def rel(d):
    return d & 127 if d >= 0 else 128 + d


def expand(op):
    """one OP -> device words (packets, sleep:MS, shot) and the names of the shots"""
    p = op.split(":")
    k = p[0]
    if k == "tap":
        cc = int(p[1])
        return [pkt(0xB0, cc, 127), "sleep:90", pkt(0xB0, cc, 0), "sleep:60"]
    if k == "cc":
        return [pkt(0xB0, int(p[1]), int(p[2]))]
    if k == "knob":
        kk, d = int(p[1]), int(p[2])
        one = pkt(0xB0, KNOB_CC + kk, rel(1 if d > 0 else -1))
        out = []
        for _ in range(abs(d)):
            out += [one, "sleep:40"]
        return out
    if k == "touch":
        return [pkt(0x90, int(p[1]), 127 if int(p[2]) else 0), "sleep:40"]
    if k == "note":                                # note:N:V: a pad (68-99) pressed at V as the hardware sends it; V 0
        n, v = int(p[1]), int(p[2])                # its release (0x80)
        return [pkt(0x90, n, v) if v else pkt(0x80, n, 0)]
    if k == "pa":                                  # pa:N:V: the pad's pressure (poly aftertouch), as the pads send it
        return [pkt(0xA0, int(p[1]), int(p[2]))]
    if k == "jog":
        d = int(p[1])
        out = []
        for _ in range(abs(d)):
            out += [pkt(0xB0, JOG_CC, rel(1 if d > 0 else -1)), "sleep:60"]
        return out
    if k == "click":
        return expand("tap:%d" % CLICK_CC)
    if k == "back":
        return expand("tap:%d" % BACK_CC)
    if k == "shift":
        return [pkt(0xB0, SHIFT_CC, 127 if int(p[1]) else 0), "sleep:40"]
    if k == "sleep":
        return ["sleep:" + p[1]]
    if k == "shot":
        return ["sleep:120", "shot"]
    if k == "watch":                               # watch:MS: the card -> overview edges, ms after the last packet
        return [op]
    if k in ("dump", "load"):                      # dump:SLOT:KEY:NAME, load:SLOT:KEY:NAME (a file in BENCH)
        return [op]
    if k in ("get", "set"):                        # get:SLOT:KEY, set:SLOT:KEY:VALUE (0-based slot; synth:<key>)
        return [op]
    raise SystemExit("move_ui_drive: unknown op %r" % op)


def png(path, fb):
    """the mirror's 1024 B (page-major, bit = row) -> an 8-bit grey PNG, 4x (white on black, as the screen)"""
    k = 4
    rows = []
    for y in range(64 * k):
        yy = y // k
        line = bytearray([0])
        for x in range(128 * k):
            xx = x // k
            line.append(255 if fb[(yy >> 3) * 128 + xx] >> (yy & 7) & 1 else 0)
        rows.append(bytes(line))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 128 * k, 64 * k, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(data)


def ascii_art(fb):
    out = []
    for y in range(0, 64, 2):
        out.append("".join("#" if (fb[(y >> 3) * 128 + x] >> (y & 7) & 1) or (fb[((y + 1) >> 3) * 128 + x] >> ((y + 1) & 7) & 1)
                           else "." for x in range(0, 128, 1)))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("arm", "seq", "state", "disarm"))
    ap.add_argument("ops", nargs="*")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "move", "ui-device"))
    ap.add_argument("--ascii", action="store_true", help="print each shot as text too")
    a = ap.parse_args(argv)
    import lock
    with lock.Lock("MOVE", who="move_ui_drive", why="move_ui_drive " + a.cmd, wait_s=0, poll_s=0):
        if a.cmd != "seq":
            print(ssh_py([a.cmd]).strip())
            return 0
        words, names = [], []
        for op in a.ops:
            if op.startswith("shot:"):
                names.append(op[5:])
            words += expand(op)
        out = ssh_py(["seq"] + words)
        for l in out.splitlines():
            if l.startswith(("DROP", "GOT", "SET", "WATCH")):
                print(l)
        for ln in save_shots(out, names, a.out, a.ascii):
            print(ln)
    return 0


def save_shots(out, names, outdir, show_ascii=False):
    """the seq's SHOT lines as NAME.png in outdir; the lines to print. A shot taken while display_mirror is 0 (the
    device printed STALE before it) is the last frame the shim wrote, maybe long ago: 2026-10-30 one showed F01's page
    count with F13 loaded. It is saved, and says so"""
    shots, stale = [], False
    for ln in out.splitlines():
        if ln == "STALE":
            stale = True
        elif ln.startswith("SHOT "):
            shots.append((ln[5:], stale))
            stale = False
    os.makedirs(outdir, exist_ok=True)
    lines = []
    for name, (hx, old) in zip(names, shots):
        fb = bytes.fromhex(hx)
        p = os.path.join(outdir, name + ".png")
        png(p, fb)
        lines.append("shot %s (%d px lit)%s" % (p, sum(bin(b).count("1") for b in fb),
                                                "  STALE: display_mirror is 0, the mirror's last frame, not the "
                                                "screen now (arm first)" if old else ""))
        if show_ascii:
            lines.append(ascii_art(fb))
    return lines


if __name__ == "__main__":
    sys.exit(main())
