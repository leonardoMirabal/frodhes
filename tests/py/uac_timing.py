# SPDX-License-Identifier: GPL-3.0-only
"""A timing model of the USB audio IN endpoint's packet hand-off (firmware/src/usb.c uac_service, platform.c
fm1_timer5_irq): no firmware code runs here, only the timing, so a change to the poll / service logic can be checked
against the same cases (tests/py/test_uac_timing.py).

What is modelled (integer microseconds):

- TIMER5: a hardware tick every 100 us (10 kHz) on a fixed grid. One pending flag: the ticks that fall in an
  interrupts-off stretch (a flash slice, fm1_flash.h FL_SLICE_US) become one tick, taken when the stretch ends;
  the others are lost. `sub` counts the ticks taken (0..4, platform.c), so a lost tick moves every later poll.
- The 2 kHz USB poll as platform.c does it: on the tick with sub == 0 (usb_due) outside a render, usb_poll (and
  uac_service at its end); nested in a render, uac_service alone; after the render, the poll it owed on the next
  tick taken.
- The audio ISR: one render every 5805 us (256 frames at 44.1 kHz), its start held by an interrupts-off stretch too.
- The host's IN tokens: one per 1 ms USB frame, at a place in the frame (phase) plus an optional per-frame jitter.
- EP4 (MUSB ISO IN): TxPktRdy set by a service, cleared by the IN token that takes the packet; an IN token with
  nothing queued sets the sticky UnderRun bit and the host gets a zero-length packet (a "true miss").
- uac_service's own logic: TxPktRdy still set -> wait; else count UnderRun as missed (only after the first packet
  since the stream (re)started: uac.queued), queue the next packet, clear UnderRun.
- The stream (re)start: SET_INTERFACE handled in a usb_poll (outside a render) resets EP4 (uac_ep4_reset,
  uac_stream: queued = 0) and the same poll queues the first packet; the IN tokens start some frames later.

Results: true misses (IN tokens that found nothing), the firmware's count (`missed`: one per UnderRun latch, so a
long stall counts 1), and the margin: for every packet the slack between the service that queued the next one and
the next IN token (min over the run; < 0 is a miss).

A fix that changes the poll can pass its own `timer5` (the policy for one taken tick, see timer5_today)."""
from dataclasses import dataclass, field

TICK_US = 100                 # platform.c: TIMER5 at 10 kHz
POLL_DIV = 5                  # usb_due every 5th tick taken: 2 kHz
FRAME_US = 1000               # one USB full-speed frame
BLOCK_US = 5805               # one render (256 frames at 44.1 kHz), the audio ISR's period


@dataclass
class Case:
    ms: int = 30                              # the run's length (USB frames)
    tok_phase: int = 300                      # the IN token's place in the frame (us)
    tok_jitter: dict = field(default_factory=dict)   # frame -> extra us for that frame's token (+ later, - earlier)
    tick_phase: int = 0                       # the TIMER5 grid's offset (us)
    sub0: int = 0                             # platform.c's sub at the first tick
    cli: tuple = ()                           # interrupts-off stretches: ((start, length), ...)
    audio: tuple = None                       # (first render start, render length) or None (no render)
    start_at: int = 0                         # the host's SET_INTERFACE alt 1 is pending from this time (us)
    first_frame: int = 2                      # the first frame whose IN token the host sends
    timer5: object = None                     # the tick policy (None: timer5_today)


@dataclass
class Result:
    true_missed: int = 0                      # IN tokens that found no packet (the host got a zero-length packet)
    counted: int = 0                          # the firmware's uac.missed
    services: list = field(default_factory=list)
    margin: int = 10 ** 9                     # min slack (us) between a queue and the IN token that takes it next
    worst_gap: int = 0                        # the longest time between two packets queued (us)
    missed_frames: list = field(default_factory=list)


class Ep4:
    """the endpoint and uac_service's state"""

    def __init__(self):
        self.alt = 0
        self.rdy = 0              # TxPktRdy
        self.underrun = 0         # TXCSR1 bit 2 (sticky until a TXCSR1 write)
        self.queued = 0           # uac.queued
        self.missed = 0           # uac.missed
        self.after_take = 0       # a packet was taken; the next queue's time not yet seen
        self.pending_queue = None # that queue's time

    def reset(self):              # SET_INTERFACE: uac_ep4_reset + uac_stream(1)
        self.alt, self.rdy, self.underrun, self.queued = 1, 0, 0, 0

    def service(self):            # uac_service: 1 if a packet was queued
        if not self.alt:
            return 0
        if self.rdy:
            return 0
        if self.queued and self.underrun:
            self.missed += 1
        self.queued = 1
        self.rdy = 1
        self.underrun = 0
        return 1


def timer5_today(st, ep, h, late, in_audio, usb_poll, service):
    """platform.c fm1_timer5_irq (FELUCCA_UAC) for one taken tick at h: st holds sub / owed. late: how far h is
    behind the grid tick it stands for (us). usb_poll(h) runs the poll (SET_INTERFACE, then uac_service);
    service(h) uac_service alone"""
    due = st["sub"] == 0
    if due:
        st["owed"] = 1
    st["sub"] = (st["sub"] + 1) % POLL_DIV
    if in_audio:
        if due:
            service(h)
        return
    if st["owed"]:
        usb_poll(h)
    st["owed"] = 0


def _blocked(t, cli):
    """t moved to the end of any interrupts-off stretch it falls in"""
    for s, n in cli:
        if s <= t < s + n:
            return s + n
    return t


def simulate(c: Case) -> Result:
    timer5 = c.timer5 or timer5_today
    end = c.ms * FRAME_US
    cli = tuple(sorted(c.cli))
    # the ticks taken: grid ticks in a stretch fold into one at its end
    taken, seen_end = [], set()
    g = c.tick_phase
    while g < end:
        h = _blocked(g, cli)
        if h != g:
            if h not in seen_end:
                seen_end.add(h)
                taken.append((h, g))      # (taken at h, for the first grid tick folded there)
        else:
            taken.append((g, g))
        g += TICK_US
    # the renders: start held by a stretch; [start, start + len)
    renders = []
    if c.audio:
        a, rl = c.audio
        while a < end:
            s = _blocked(a, cli)
            renders.append((s, s + rl))
            a += BLOCK_US

    def in_audio(t):
        return any(s <= t < e for s, e in renders)
    tokens = []
    for n in range(c.first_frame, c.ms):
        t = n * FRAME_US + c.tok_phase + c.tok_jitter.get(n, 0)
        tokens.append((t, n))                 # (the host's token is not held by the device's interrupts)
    ep, r, st = Ep4(), Result(), {"sub": c.sub0, "owed": 0}
    started = [False]

    def service(h):
        if ep.service():
            r.services.append(h)
            if ep.after_take:
                ep.pending_queue = h
                ep.after_take = 0

    def usb_poll(h, serve=True):              # (serve False: a policy that runs uac_service on its own)
        if not started[0] and h >= c.start_at:
            started[0] = True
            ep.reset()
        if serve:
            service(h)
    ev = [(h, 1, ("tick", g)) for h, g in taken] + [(t, 0, ("tok", n)) for t, n in tokens]
    ev.sort(key=lambda e: (e[0], e[1]))       # (a token and a tick at the same us: the token first, the harsher)
    for t, _, (kind, x) in ev:
        if kind == "tick":
            timer5(st, ep, t, t - x, in_audio(t), usb_poll, service)
            continue
        if not ep.alt:
            continue
        if ep.rdy:                            # the token takes the queued packet
            if ep.pending_queue is not None:  # (the stream's first packet: queued at the reset, no slack)
                r.margin = min(r.margin, t - ep.pending_queue)
            ep.rdy = 0
            ep.after_take = 1
            ep.pending_queue = None
        else:                                 # nothing queued: a zero-length packet
            ep.underrun = 1
            if ep.queued:
                r.true_missed += 1
                r.missed_frames.append(x)
                r.margin = min(r.margin, -1)
    r.counted = ep.missed
    s = r.services
    r.worst_gap = max((b - a for a, b in zip(s, s[1:])), default=0)
    return r


def sweep(length, *, timer5=None, jitter=0, step=10, audio=None, ms=14):
    """an interrupts-off stretch of `length` us at every place against the IN tokens and the poll grid (token phase
    0..990 us, the stretch's start over one 1 ms frame, both in `step` us): (worst margin, cases with a true miss,
    cases counted by the firmware, cases). jitter: the token of the frame the stretch starts in comes `jitter` us late
    and the next one `jitter` us early (the two moves that shorten one IN interval the most)."""
    worst, bad, counted, n = 10 ** 9, 0, 0, 0
    for ph in range(0, FRAME_US, step):
        for s in range(5 * FRAME_US, 6 * FRAME_US, step):
            j = {5: jitter, 6: -jitter} if jitter else {}
            r = simulate(Case(ms=ms, tok_phase=ph, tok_jitter=j, cli=((s, length),), audio=audio, timer5=timer5))
            worst = min(worst, r.margin)
            bad += r.true_missed > 0
            counted += r.counted > 0
            n += 1
    return worst, bad, counted, n


def critical(timer5=None, jitter=0, lo=0, hi=2000, step=20):
    """the shortest interrupts-off stretch (us, to `step`) that loses an IN token at the worst placement (a binary
    search: a longer stretch never loses fewer); None: none up to hi"""
    def bad(n):
        return sweep(n, timer5=timer5, jitter=jitter, step=step)[1] > 0
    if not bad(hi):
        return None
    lo //= step
    hi //= step
    while lo < hi:
        m = (lo + hi) // 2
        if bad(m * step):
            hi = m
        else:
            lo = m + 1
    return lo * step


def timer5_every_tick(st, ep, h, late, in_audio, usb_poll, service):
    """a candidate, for comparison only (not the firmware): uac_service on every tick taken (10 kHz), usb_poll still
    at 2 kHz. Its cost on the FM-1 (an INDEX write and a TXCSR1 read per tick, nested in the render 58 times a block)
    is not modelled"""
    due = st["sub"] == 0
    if due:
        st["owed"] = 1
    st["sub"] = (st["sub"] + 1) % POLL_DIV
    if in_audio or not st["owed"]:
        service(h)
        return
    usb_poll(h)
    st["owed"] = 0
