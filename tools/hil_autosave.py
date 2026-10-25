#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""autosave_stream: the settings autosave while a computer records the FM-1's USB audio (the FM-1 over USB-MIDI + USB
audio). The USB input stream stays open for the whole take (hil_must.Rec, the device switched to the rate, blocksize
128 as a DAW); the firmware's own counters are read every 0.5 s (CC 116, hil_must's reader).

    tools/hil_autosave.py [--rate 48000|44100|both] [--deadline S | --old-rule] [--rounds N] [--off-limit US]
                          [--teeth] [--no-sound-take] [--dry-run]

Auto save keeps the system setup only (0.9.5: params.c settings_record): a SETUP change is saved 2 s later, a sound
change never. Two takes per rate and round: "setup" (the save must come) and "sound" (no save may come).
One take:
  settle   the stream open 3 s; the FM-1 reports it flowing at the rate (CC 100 item 12), the counters restart
  note     a short C4 at v60 (0.2 s): the silence the save waits for starts after its tail, so the old 60 s rule
           (FL_REC_QUIET: 60 s of silence while recording) cannot be satisfied by silence from before the take
  prime    (setup) CC 57, chord mode's INVERSION, to the value the change will not set, before the settle (its own
           save falls in the settle, ~2 s in: the stream must take it too)
  change   setup: CC 57 (INVERSION, a setup item: chord mode off, so nothing heard) to the other value;
           sound: TONE (CC 28) to 63, then 64 (its default): a sound change, the panel left at TONE's default
  silent   nothing sent but the CC 116 queries; the recording goes on until the save is seen + 3 s, or the deadline
           + 5 s
The save, from the device's own report (the first that the firmware has, named in the report):
  item 17  CC 100 item 17, the longest stretch a flash write held the interrupts off (us; > 0 = a write in that poll;
           fm1_flash.h fl_off_max_us, the split-erase firmware)
  item 10  else the longest ISR entry spacing (CC 100 item 10) >= 9000 us (1.5 blocks; 5805 nominal): an erase that
           held the interrupts off (~24 ms) on a firmware without item 17
  --old-rule / --teeth: one "legacy" take, the TONE change with the save expected (firmware before 0.9.5's rule)
  neither  no save seen: FAIL on the setup take (the last resort is by hand: switch the FM-1 off and on, CHORD's
           INVERSION shows the value set); on the sound take that is the PASS, a save seen there FAILS
The gates (all must hold, over the whole take, settle included): every reply carries the USB items, the stream
flowing at the rate in every reply, USB underruns / missed / overruns 0, PortAudio's input flags 0, the save seen
within --deadline s of the change (default 10 s, the split-erase rule: 2 s after the change once silent; --old-rule:
70 s, FL_REC_QUIET's 60 s + the note's tail), the flash's interrupts-off stretch <= --off-limit us (default 1100)
where item 17 reports it (else information: item 10's spacing). Information: the whole ISR's max, the ring's low
point, the rate adaptation, the frames recorded against the take's length, when the USB audio fell silent.

The teeth: on a firmware that erases with the interrupts off (353d023: FL_REC_QUIET), `--old-rule` must FAIL with
missed > 0 at the ~60 s save. `--teeth` inverts the exit code for that proof: 0 only when every take FAILED with
missed > 0 in the poll that saw the save.

Exit 0 PASS, 1 FAIL. Report: build/hil/autosave/report.txt + report.json. Needs the macOS sandbox off (CoreAudio).
Takes the device lock (tools/lock.py DEVICE; inherited from run_final_rev.sh)."""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "build", "hil", "autosave")
TONE_CC, TONE_DEF = 28, 64              # params.c [P_TONE]: CC 28, default 64 (a sound parameter: never auto-saved)
INV_CC = 57                             # frodhes.c midi_cc: chord mode's INVERSION 0..3 (set.chord[1], the setup)
NOTE, NOTE_V, NOTE_S = 60, 60, 0.2
SETTLE_S, TAIL_S, OVER_S = 3.0, 3.0, 5.0
POLL_S = 0.5
DEADLINE_NEW, DEADLINE_OLD = 10.0, 70.0
OFF_LIMIT_US = 1100
GAP_US = 9000                           # item 10: an ISR entry spacing this long = the interrupts held off by an erase
SILENT_DBFS = -66.0                     # fx_quiet's level (fx.c: 500 ms under -66 dBFS)
ITEM_OFF, ITEM_GAP = 17, 10


# ---- the analysis (host-tested: tests/py/test_hil_autosave.py) ----------------------------------------------------

def parse(msgs):
    """one CC 116 reply [(control, value)] -> {usb: {id: value}, isr_us}: every CC 100 id / 101-102 value item (the
    raw ids: item 17 is newer than hil_cpu's USB_ITEMS), the whole ISR's max (CC 108 / 107, us / 4)"""
    items, got, cur = {}, {}, dict(id=None, msb=0)
    for c, v in msgs:
        if c == 100:
            cur["id"] = v
        elif c == 101:
            cur["msb"] = v
        elif c == 102 and cur["id"] is not None:
            items[cur["id"]] = cur["msb"] << 7 | v
        elif c in (107, 108, 116, 117):
            got[c] = v
    r = dict(items=items, answered={116, 117} <= got.keys())
    if {107, 108} <= got.keys():
        r["isr_us"] = (got[108] << 7 | got[107]) * 4
    return r


def stream_ok(state, rate):
    return state is not None and bool(state & 2) and bool(state & 4) == (rate == 48000)


def find_save(polls, t_change):
    """the save in [(t, parsed)] after t_change (s): (method, t_seen, t_before, poll index) or None. item 17 when any
    reply carries it, else item 10's spacing"""
    has17 = any(ITEM_OFF in p["items"] for _, p in polls)
    prev = t_change
    for i, (t, p) in enumerate(polls):
        if t < t_change:
            continue
        it = p["items"]
        if has17 and it.get(ITEM_OFF, 0) > 0:
            return "item 17", t, prev, i
        if not has17 and it.get(ITEM_GAP, 0) >= GAP_US:
            return "item 10", t, prev, i
        prev = t
    return None


def analyse(polls, rate, t_change, deadline, off_limit=OFF_LIMIT_US, pa_flags=0, frames=None, dur=None,
            silent_at=None, expect_save=True):
    """one take: polls [(t s on the take's clock, parse() result)] from the counters' restart on. expect_save False:
    the sound take (a sound change: no save may come). -> dict(rows=[(check, value, criterion, ok|FAIL|info)],
    verdict, save=..., missed_at_save=...)"""
    rows = []

    def row(check, value, crit, ok, info=False):
        rows.append((check, value, crit, "info" if info else "ok" if ok else "FAIL"))
    ans = [p for _, p in polls if p["answered"]]
    row("counters answer", f"{len(ans)}/{len(polls)} replies", "CC 116 answers every poll",
        bool(polls) and len(ans) == len(polls))
    with_usb = [p for p in ans if 1 in p["items"]]
    row("USB counters answer", f"{len(with_usb)}/{len(ans)} replies", "every reply carries them (items 1-12)",
        bool(ans) and len(with_usb) == len(ans))
    bad_state = [t for t, p in polls if p["answered"] and not stream_ok(p["items"].get(12), rate)]
    row("USB stream state", "flowing at the rate in every reply" if not bad_state else
        f"not flowing at {rate / 1000:g} kHz in {len(bad_state)} replies (first at {bad_state[0]:.1f} s)",
        f"flowing at {rate / 1000:g} kHz", not bad_state)
    tot = {k: sum(p["items"].get(i, 0) for p in with_usb) for k, i in (("underruns", 1), ("missed", 2),
                                                                      ("overruns", 3))}
    for k, v in tot.items():
        where = [f"{t:.1f}" for t, p in polls if p["items"].get(dict(underruns=1, missed=2, overruns=3)[k], 0)]
        row(f"USB {k}", v if not where else f"{v} (in the polls at {', '.join(where[:6])} s)", "0", v == 0)
    row("PortAudio input flags", pa_flags, "0 (the computer's side: no input overflow)", pa_flags == 0)
    sv = find_save(polls, t_change)
    missed_at_save = None
    if not expect_save:
        row("no save after a sound change", "none seen" if sv is None else
            f"a save ({sv[0]}) {sv[1] - t_change:.1f} s after the change",
            "no flash write (the sound is never auto-saved)", sv is None)
    elif sv is None:
        how = ("item 17 never > 0" if any(ITEM_OFF in p["items"] for _, p in polls) else
               f"no item 17 in the replies and item 10 never >= {GAP_US} us")
        row("save seen", f"no ({how})", f"within {deadline:g} s of the change (by hand: power-cycle, CHORD's "
            "INVERSION shows the value set)", False)
    else:
        method, ts, tb, i = sv
        it = polls[i][1]["items"]
        missed_at_save = sum(polls[j][1]["items"].get(2, 0) for j in range(max(0, i - 1), min(len(polls), i + 2)))
        row("save seen", f"{method} at {tb - t_change:.1f}..{ts - t_change:.1f} s after the change",
            f"within {deadline:g} s", ts - t_change <= deadline)
        row("save evidence", "CC 100 item 17 (the flash write's interrupts-off time)" if method == "item 17" else
            f"CC 100 item 10 (an ISR entry spacing of {it.get(ITEM_GAP)} us: the erase held the interrupts off)",
            "the device's own report", True, info=True)
        if method == "item 17":
            off = max(p["items"].get(ITEM_OFF, 0) for _, p in polls)
            row("flash interrupts off, longest (us)", off, f"<= {off_limit}", off <= off_limit)
        row("USB missed around the save", missed_at_save, "(the polls before, at and after it)", True, info=True)
    gaps = [p["items"][ITEM_GAP] for p in with_usb if ITEM_GAP in p["items"]]
    if gaps:
        row("ISR entry spacing, longest (us)", max(gaps), "information (5805 nominal)", True, info=True)
    isr = [p["isr_us"] for p in ans if "isr_us" in p]
    if isr:
        row("whole ISR max (us)", max(isr), "information", True, info=True)
    lows = [p["items"][8] for p in with_usb if p["items"].get(8, 16383) != 16383]
    if lows:
        row("USB ring low point (frames)", min(lows), "information", True, info=True)
    row("USB adj up / down", f"{sum(p['items'].get(4, 0) for p in with_usb)} / "
        f"{sum(p['items'].get(5, 0) for p in with_usb)}", "information (the rate adaptation)", True, info=True)
    if frames is not None and dur:
        row("frames recorded", f"{frames} ({frames / (rate * dur) * 100:.2f} % of {dur:.1f} s)", "information",
            True, info=True)
    if silent_at is not None:
        row("USB silent from (s after the change)", f"{silent_at - t_change:.2f}", f"information (< {SILENT_DBFS:g} "
            "dBFS from there on)", True, info=True)
    verdict = "FAIL" if any(r[3] == "FAIL" for r in rows) else "PASS"
    return dict(rows=rows, verdict=verdict, save=sv[:2] + (sv[1] - t_change,) if sv else None,
                missed=tot["missed"], missed_at_save=missed_at_save)


def silent_from(x, fs, win_s=0.0058):
    """the start (s) of the recording's last stretch under SILENT_DBFS, in 5.8 ms windows; None if it ends loud"""
    import numpy as np
    x = np.asarray(x, float)
    if x.ndim > 1:
        x = np.max(np.abs(x), axis=1)
    w = max(1, int(win_s * fs))
    n = len(x) // w
    if not n:
        return None
    pk = np.abs(x[:n * w]).reshape(n, w).max(axis=1)
    thr = 10 ** (SILENT_DBFS / 20)
    loud = np.nonzero(pk >= thr)[0]
    if np.abs(x[n * w:]).max(initial=0.0) >= thr:
        return None
    if not len(loud):
        return 0.0
    if loud[-1] == n - 1:
        return None
    return (loud[-1] + 1) * w / fs


def teeth_ok(results):
    """--teeth: every take FAILED, a save was seen, and USB missed > 0 in the polls around it"""
    return bool(results) and all(r["verdict"] == "FAIL" and r["save"] and (r["missed_at_save"] or 0) > 0
                                 for r in results)


def report_text(results, a):
    out = []
    for r in results:
        out.append(f"take {r['name']}: {r['verdict']}")
        for c, v, k, s in r["rows"]:
            out.append(f"  {s:4}  {c}: {v}   [{k}]")
    v = "FAIL" if any(r["verdict"] == "FAIL" for r in results) else "PASS"
    head = (f"hil_autosave: {v} ({len(results)} take(s), deadline {a.deadline:g} s"
            f"{', old rule' if a.old_rule else ''}"
            + (f"; teeth {'PROVEN' if teeth_ok(results) else 'NOT PROVEN'}" if a.teeth else "") + ")")
    return [head] + out


# ---- the plan and the run ------------------------------------------------------------------------------------------

def rates_of(a):
    return (48000, 44100) if a.rate == "both" else (int(a.rate),)


def kinds_of(a):
    """the takes: --old-rule / --teeth (a firmware before 0.9.5's setup-only rule, which saved every change but never
    marked CC 57): "legacy", the TONE change with the save expected, as this tool did before"""
    if a.old_rule or a.teeth:
        return ("legacy",)
    return ("setup",) if a.no_sound_take else ("setup", "sound")


def plan(a):
    per = SETTLE_S + 1.5 + a.deadline + OVER_S
    nk = len(kinds_of(a))
    lines = [f"hil_autosave plan: rates {', '.join(f'{r / 1000:g} kHz' for r in rates_of(a))}, {a.rounds} round(s) "
             f"each, takes {' + '.join(kinds_of(a))}; the save within {a.deadline:g} s of the change{' (--old-rule)' if a.old_rule else ''}; "
             f"flash interrupts off <= {a.off_limit} us (item 17)"]
    t = 0.0
    lines += [f"  t={t:5.1f}  setup take: CC {INV_CC} (INVERSION) to the value the change will not set (the prime)",
              f"  t={t:5.1f}  open the USB stream, CC 116 until item 12 says flowing at the rate; CC 116 (restart)",
              f"  t={SETTLE_S:5.1f}  note on {NOTE} v{NOTE_V}, off {NOTE_S} s later (the silence starts after its tail)",
              f"  t={SETTLE_S + 1.5:5.1f}  setup: CC {INV_CC} to the other value (the change, saved 2 s later); "
              f"sound: CC {TONE_CC} {TONE_DEF - 1}, CC {TONE_CC} {TONE_DEF} (TONE: never saved)",
              f"         then silent: CC 116 every {POLL_S} s; stop {TAIL_S} s after the save is seen, at most "
              f"{a.deadline + OVER_S:g} s after the change",
              f"  gates: counters + USB items answer, stream flowing at the rate, USB underruns / missed / overruns "
              f"0, PortAudio flags 0, setup: save seen (item 17, else item 10 >= {GAP_US} us) within {a.deadline:g} s; "
              f"sound: no save within {a.deadline + OVER_S:g} s",
              f"total ~{per * a.rounds * len(rates_of(a)) * nk / 60:.1f} min at most ({per:.0f} s per take)"]
    if a.teeth:
        lines.append("teeth: exit 0 only if every take FAILS with USB missed > 0 around the save (353d023)")
    return lines


def take(name, rate, a, hm, sd, dev, udev, kind="setup", inv=1):
    if kind == "setup":
        dev.cc(INV_CC, 1 - inv)                              # the prime (its save: in the settle)
    rec = hm.Rec(udev, rate, 2, (0, 1), secs=SETTLE_S + 2 + a.deadline + OVER_S + 10, blocksize=128,
                 switch_rate=True, sd=sd)
    polls = []
    try:
        t0 = time.monotonic()
        st = None
        while time.monotonic() - t0 < SETTLE_S:              # the stream flowing at the rate (item 12)
            st = parse(dev.query())["items"].get(12)
            if stream_ok(st, rate):
                break
            time.sleep(0.25)
        dev.query()                                          # the counters and the ranges restart
        tz = time.monotonic()
        clock = lambda: time.monotonic() - tz               # noqa: E731
        start_frames = rec.w

        def poll():
            polls.append((clock(), parse(dev.query())))
        while clock() < SETTLE_S:
            poll(); time.sleep(POLL_S - 0.25)
        dev.send("on", NOTE, NOTE_V); time.sleep(NOTE_S); dev.send("off", NOTE)
        while clock() < SETTLE_S + 1.5:
            poll(); time.sleep(POLL_S - 0.25)
        if kind == "setup":
            dev.cc(INV_CC, inv)
        else:                                                # sound, legacy
            dev.cc(TONE_CC, TONE_DEF - 1); dev.cc(TONE_CC, TONE_DEF)
        t_change = clock()
        seen = None
        while clock() - t_change < a.deadline + OVER_S:
            poll()
            if seen is None and find_save(polls, t_change):
                seen = clock()
            if seen is not None and clock() - seen >= TAIL_S and kind != "sound":
                break
            time.sleep(max(0.0, POLL_S - 0.25))
        dur = clock()
        x = rec.buf[start_frames:rec.w]
        flags = rec.flags
    finally:
        rec.stop()
    sil = silent_from(x, rate)
    res = analyse(polls, rate, t_change, a.deadline, a.off_limit, pa_flags=flags, frames=len(x), dur=dur,
                  silent_at=sil if sil is None or sil >= SETTLE_S else None, expect_save=kind != "sound")
    res["name"] = name
    res["polls"] = [(round(t, 3), p) for t, p in polls]
    return res


def run(a):
    import hil_must as hm
    import sounddevice as sd
    dev = hm.Dev()
    udev = hm.find_usb(sd)
    if udev is None:
        sys.exit("no FM-1 USB audio input")
    results = []
    try:
        for rate in rates_of(a):
            for k in range(a.rounds):
                for kind in kinds_of(a):
                    name = f"{rate}-{k + 1}-{kind}"
                    print(f"take {name} ...", flush=True)
                    r = take(name, rate, a, hm, sd, dev, udev, kind, inv=(k + 1) % 2)
                    print(f"take {name}: {r['verdict']}" + (f", save {r['save'][0]} {r['save'][2]:.1f} s after the "
                                                            f"change" if r["save"] else ", no save seen"), flush=True)
                    results.append(r)
                    time.sleep(1.0)
    finally:
        dev.close()
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rate", default="48000", choices=("48000", "44100", "both"))
    ap.add_argument("--deadline", type=float, default=None, help=f"s (default {DEADLINE_NEW:g}; --old-rule "
                    f"{DEADLINE_OLD:g})")
    ap.add_argument("--old-rule", action="store_true", help="the current firmware's 60 s rule: the deadline 70 s")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--off-limit", type=int, default=OFF_LIMIT_US)
    ap.add_argument("--teeth", action="store_true", help="exit 0 only if every take FAILS with missed > 0 at the save")
    ap.add_argument("--no-sound-take", action="store_true", help="only the setup take (no 'a sound change: no save')")
    ap.add_argument("--out", default=OUTDIR)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.deadline is None:
        a.deadline = DEADLINE_OLD if a.old_rule else DEADLINE_NEW
    if a.dry_run:
        print("\n".join(plan(a)))
        print("DRY RUN: no lock, no MIDI, no audio")
        return 0
    print("\n".join(plan(a)), flush=True)
    import lock
    with lock.Lock("DEVICE", who="hil_autosave", wait_s=3600):
        results = run(a)
    txt = report_text(results, a)
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, "report.txt"), "w").write("\n".join(txt) + "\n")
    json.dump(dict(args=vars(a), takes=results), open(os.path.join(a.out, "report.json"), "w"), indent=1,
              default=str)
    print("\n".join(txt))
    print(f"report: {os.path.join(a.out, 'report.txt')}")
    ok = teeth_ok(results) if a.teeth else all(r["verdict"] == "PASS" for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
