# SPDX-License-Identifier: GPL-3.0-only
"""usb.c's FELUCCA_UAC_PHASE (uac_tick / uac_slot / uac_ep4_poll) and platform.c's tick as a policy for
tests/py/uac_timing.py: the same steps, line for line, so the timing model checks the firmware's logic.
tests/uac_seq_test.c runs the C itself (frames, counters); this file checks its timing."""
import uac_timing as u

UA_PROBE = 16
UA_LOOKS = 4
M = 0xFFFFFFFF


def _ep4_poll(st, ep, h, probe, service):
    """uac_ep4_poll: 0 while the packet waits (a look does not count a wait), else 1 + the polls it waited"""
    if not ep.alt:
        return 0
    if not ep.queued:                         # (uac_stream: a (re)started stream)
        st["wait"], st["look"], st["acq"] = 0, 0, 1
    if ep.rdy:
        if not probe and st["wait"] < 255:
            st["wait"] += 1
        return 0
    w, st["wait"] = st["wait"], 0
    service(h)
    return 1 + w


def _slot(st, ep, h, c, service):
    look = st["look"]
    if c == 1:
        st["cd"] = 1
        if st["wait"] != 1:
            return
        st["probe"] += 1
        if st["probe"] < UA_PROBE:
            return
        st["probe"] = 0
        if not _ep4_poll(st, ep, h, 1, service):
            return
        st["early"] += 1
        st["probe"] = UA_PROBE
        st["cd"] = 5
        return
    q = _ep4_poll(st, ep, h, look != 0, service)
    st["cd"] = 5
    if ((c + 4) & M) < 4:
        st["cd"] = (5 + c) & M
    st["look"] = 0
    if q:
        st["late"] += look != 0
        if q > 3:
            st["acq"] = 1
        return
    if (look < UA_LOOKS) if look else (st["wait"] == 2 or (st["wait"] == 1 and st["acq"])):
        st["look"] = look + 1
        st["acq"] = 0
        st["cd"] = 1
    elif look:
        st["cd"] = 1


def _tick(st, ep, h, n, service):
    c = (st["cd"] - n) & M
    if ((c - 2) & M) < 4:
        st["cd"] = c
    else:
        _slot(st, ep, h, c, service)


def timer5_phase(st, ep, h, late, in_audio, usb_poll, service):
    """platform.c fm1_timer5_irq with FELUCCA_UAC_PHASE: uac_tick(n) on every tick (n: the ticks since the last one,
    from TIMER4: dt < 200 us -> 1, else dt / 100 us), then the 2 kHz usb_poll (without uac_service) as before"""
    for k, v in (("cd", 0), ("probe", 0), ("look", 0), ("acq", 0), ("wait", 0), ("early", 0), ("late", 0)):
        st.setdefault(k, v)
    due = st["sub"] == 0
    if due:
        st["owed"] = 1
    st["sub"] = (st["sub"] + 1) % u.POLL_DIV
    _tick(st, ep, h, 1 + late // u.TICK_US, service)
    if in_audio:
        return
    if st["owed"]:
        usb_poll(h, serve=False)
        if ep.alt and not ep.queued:          # (SET_INTERFACE just now: usb.c queues the first packet at once)
            _ep4_poll(st, ep, h, 0, service)
    st["owed"] = 0
