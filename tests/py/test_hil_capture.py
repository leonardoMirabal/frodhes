# SPDX-License-Identifier: GPL-3.0-only
"""tools/hil_scan.py's persistent recorder (Ring / Capture / Rig.both) on synthetic streams, no FM-1: a take is cut
from a stream that stays open by the blocks' ADC times, whatever the block sizes, the clock jitter, the ring's wrap
or another device's own rate; a take is never handed out before all its frames are in."""
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import hil_scan as h  # noqa: E402


def feed(ring, sig, t_start, sr, rng, jitter=0.0):
    """sig (n, ch) pushed in blocks of 64..1024 frames, each stamped with its first frame's ADC time (+- jitter s)"""
    i = 0
    while i < len(sig):
        n = int(rng.integers(64, 1025))
        ring.push(sig[i:i + n], t_start + i / sr + rng.uniform(-jitter, jitter))
        i += n


def test_cut_by_adc_time():
    rng = np.random.default_rng(1)
    sr = 44100
    sig = np.arange(10 * sr, dtype=np.float32)[:, None] * np.ones((1, 2), np.float32)   # the frame's own index
    r = h.Ring(sr, 2, secs=30)
    feed(r, sig, 1000.0, sr, rng, jitter=20e-6)
    x = r.cut(1003.25, 1.5)
    assert x.shape == (int(1.5 * sr), 2)
    assert abs(x[0, 0] - 3.25 * sr) <= 1 and np.all(np.diff(x[:, 0]) == 1)          # +-1 frame of the ADC time
    assert r.cut(1009.5, 1.0) is None                                               # not all in yet
    assert abs(r.end_time() - (1000.0 + (10 * sr - 1) / sr)) < 1e-3


def test_ring_wraps_and_forgets():
    rng = np.random.default_rng(2)
    sr = 48000
    sig = (np.arange(25 * sr) % 1000003).astype(np.float32)[:, None]
    r = h.Ring(sr, 1, secs=10)
    feed(r, sig, 5.0, sr, rng)
    x = r.cut(5.0 + 20.0, 4.0)                                                      # across the wrap: exact
    assert np.array_equal(x[:, 0], sig[20 * sr:24 * sr, 0])
    y = r.cut(5.0 + 12.0, 6.0)                                                      # its first 2 s are gone: zeros
    assert np.all(y[:int(1.9 * sr), 0] == 0) and np.array_equal(y[int(3.1 * sr):, 0], sig[int(15.1 * sr):18 * sr, 0])


def test_missing_input_stays_a_hole():
    """300 ms the stream never delivered (the ADC clock jumps): the take re-anchors there, the hole is zeros and is
    reported, what follows stays on the event clock; a block delivered twice is overwritten, not inserted"""
    rng = np.random.default_rng(4)
    sr = 44100
    sig = np.zeros((8 * sr, 1), np.float32)
    for s in (1.0, 3.0, 6.0):
        sig[int(s * sr)] = 1.0                                                     # clicks at 1, 3, 6 s
    r = h.Ring(sr, 1)
    a, b = int(2.0 * sr), int(2.3 * sr)
    feed(r, sig[:a], 10.0, sr, rng, jitter=50e-6)
    feed(r, sig[b:], 10.0 + b / sr, sr, rng, jitter=50e-6)                       # 2.0 .. 2.3 s never came
    x = r.cut(10.5, 6.0)
    got = np.flatnonzero(x[:, 0] > 0.5) / sr
    assert np.allclose(got, [0.5, 2.5, 5.5], atol=0.002), got
    assert len(r.gaps) == 1 and abs(r.gaps[0][1] - 0.3) < 0.002
    assert not np.any(x[int(1.52 * sr):int(1.78 * sr)])
    r2 = h.Ring(sr, 1)
    feed(r2, sig[:a], 10.0, sr, rng)
    feed(r2, sig[a - 512:], 10.0 + (a - 512) / sr, sr, rng)                       # 512 frames came twice
    y = r2.cut(10.5, 6.0)
    assert np.allclose(np.flatnonzero(y[:, 0] > 0.5) / sr, [0.5, 2.5, 5.5], atol=0.002)
    assert len(r2.gaps) == 1 and abs(r2.gaps[0][1] + 512 / sr) < 0.002


def test_timestamp_jitter_is_no_gap():
    """the FM-1's USB blocks' ADC times jitter by up to ~3 ms (measured 2026-10-18): no gap, no frame moved"""
    rng = np.random.default_rng(6)
    sr = 44100
    sig = np.arange(10 * sr, dtype=np.float32)[:, None]
    r = h.Ring(sr, 1)
    feed(r, sig, 3.0, sr, rng, jitter=3e-3)
    x = r.cut(4.0, 5.0)
    assert not r.gaps and np.all(np.diff(x[:, 0]) == 1)


def test_clock_drift_is_no_gap():
    """a device clock 300 ppm off the computer's: the ADC times drift against the nominal rate, block by block well
    under the tolerance: one contiguous take, no gap"""
    rng = np.random.default_rng(5)
    sr = 44100
    sig = np.arange(30 * sr, dtype=np.float32)[:, None]
    r = h.Ring(sr, 1, secs=40)
    i = 0
    while i < len(sig):
        n = int(rng.integers(256, 1025))
        r.push(sig[i:i + n], 50.0 + i / (sr * 1.0003) + rng.uniform(-3e-4, 3e-4))
        i += n
    x = r.cut(52.0, 25.0)
    assert not r.gaps and np.all(np.diff(x[:, 0]) == 1)


def test_a_dropped_stretch_keeps_the_clock():
    """a stretch of stale input (frames still counted, the ADC clock steady): the frames after it stay on time"""
    rng = np.random.default_rng(3)
    sr = 44100
    sig = np.zeros((6 * sr, 1), np.float32)
    sig[int(4.0 * sr)] = 1.0                                                        # a click 4 s in
    sig[int(1.0 * sr):int(1.25 * sr)] = 0.5                                        # 250 ms of stale input
    r = h.Ring(sr, 1)
    feed(r, sig, 77.0, sr, rng, jitter=50e-6)
    x = r.cut(80.0, 2.0)
    assert abs(int(np.argmax(x[:, 0])) - sr) <= 2


class FakeStream:
    """an InputStream: a thread calls the callback with blocks of a synthetic device whose output is silent until a
    note was 'sent' (Fake.sent), then a tone lat s later (stream time = time.monotonic() - t_base)"""

    def __init__(self, dev, sr, n_in, callback, base, sent, lat):
        self.sr, self.n_in, self.cb, self.base, self.sent, self.lat = sr, n_in, callback, base, sent, lat
        self.run, self.k = False, 0

    @property
    def time(self):
        return time.monotonic() - self.base

    def start(self):
        self.run = True
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()

    def _loop(self):
        blk = 256
        t_adc0 = self.time
        while self.run:
            t = t_adc0 + self.k / self.sr
            while self.time < t + blk / self.sr + 0.002:                          # a block is out after it was captured
                time.sleep(0.0005)
            ts = t + np.arange(blk) / self.sr
            on = [s + self.lat for s in self.sent]
            x = np.zeros((blk, self.n_in), np.float32)
            if on:
                a = np.where(ts >= min(on), 0.25 * np.sin(2 * np.pi * 440 * ts), 0.0)
                x[:] = a[:, None]

            class TI:
                inputBufferAdcTime = float(t)
                currentTime = float(t + blk / self.sr)
            self.cb(x, blk, TI, None)
            self.k += blk

    def stop(self):
        self.run = False

    def close(self):
        pass


class FakeSD:
    """rates: each device's default_samplerate as PortAudio cached it (at its first use); opened: (device, rate,
    extra_settings) per InputStream"""
    def __init__(self, rates, lat):
        self.rates, self.lat, self.base, self.sent = rates, lat, time.monotonic() - 500.0, []
        self.opened = []

    def query_devices(self, dev):
        return {"default_samplerate": float(self.rates[dev]), "max_input_channels": 2 if dev == 0 else 6}

    class CoreAudioSettings:
        def __init__(self, change_device_parameters=False, fail_if_conversion_required=False):
            self.change, self.exact = change_device_parameters, fail_if_conversion_required

    def InputStream(self, device, samplerate, channels, dtype, callback, blocksize=0, extra_settings=None):
        self.opened.append((device, samplerate, extra_settings))
        return FakeStream(device, samplerate, channels, callback, self.base, self.sent, self.lat[device])


def fake_rig(sd):
    r = object.__new__(h.Rig)
    r.sd, r.dev, r.cap, r.xruns = sd, 0, None, []

    class Out:
        def send(self, m):
            if m.type == "note_on":
                sd.sent.append(time.monotonic() - sd.base)
    r.out, r.mido = Out(), h._Mido
    return r


def test_rig_takes_are_slices_on_the_event_clock():
    """two devices at their own rates (the USB opened at FS, the jack at its 44.1 kHz), the USB with 6 ms latency,
    the jack with 2 ms: in every take (one stream for all of them) the note sits at its event time + latency on both, at FS"""
    sd = FakeSD({0: 44100, 1: 44100}, {0: 0.006, 1: 0.002})
    r = fake_rig(sd)
    r.add_jack(1, 4)
    try:
        for k in range(3):
            d = r.both(0.5, [(0.1, "on", 60, 100), (0.4, "off", 60, 0)], jack=True)
            assert d["usb"].shape == (int(0.5 * h.FS), 2) and d["jack"].shape == (int(0.5 * h.FS), 1)
            at = {key: int(np.argmax(np.abs(d[key][:, 0]) > 0.01)) / h.FS for key in ("usb", "jack")}
            # both devices saw the same note-on: their offset is the latency difference (4 ms), exactly, whatever
            # the sender's own lateness (a busy machine sends a few ms late: that is not the recorder's error)
            assert abs((at["usb"] - at["jack"]) - 0.004) < 0.001, (k, at)
            # and at the event time + latency, never early; a busy machine may send up to some ms late (the fake note
            # is stamped at the real send time). A rate mix-up (44.1 / 48 kHz) would misplace it by tens of SECONDS
            # (the fake streams' clocks start 500 s before the take), so a 50 ms window still catches it
            for key, lat in (("usb", 0.006), ("jack", 0.002)):
                late = at[key] - (0.1 + lat)
                assert -0.001 < late < 0.050, (k, key, at[key])
            assert len(r.cap.streams) == 2                                          # no stream opened per take
            sd.sent.clear()
            assert r.wait_silence(2.0)                                              # the polls: slices too
    finally:
        r.cap.close()


def test_rig_usb_stream_switches_the_device_to_fs():
    """the FM-1's USB stream at FS with the device itself switched to it and conversion refused, whatever PortAudio's
    cached default_samplerate says. 2026-11-12 (0.9.10 / 0.9.11 T2, hil_scan --tier full): stress_max had switched the
    FM-1 to 44.1 kHz in the same process, the cache still said 48 kHz, the stream opened at 48 kHz without the switch
    and Core Audio converted: ADC-time steps of -10 / +10 ms every 0.134 s in every USB take"""
    for cached in (44100, 48000):
        sd = FakeSD({0: cached, 1: 48000}, {0: 0.006, 1: 0.002})
        r = fake_rig(sd)
        try:
            r.capture()
            dev, rate, ex = sd.opened[0]
            assert (dev, rate) == (0, h.FS), sd.opened
            assert ex is not None and ex.change and ex.exact, "the device switched, no conversion"
            assert r.cap.rings["usb"].sr == h.FS
        finally:
            r.cap.close()


def test_conversion_beat_is_a_gap_every_take():
    """the signature the fix removes: block ADC times that step -10.7 ms and back 21 ms later every 0.1344 s (frames
    continuous) make Ring.cut report gaps in every 0.5 s take, the pairs 0.134 s apart (hil_rate_check.beat reads
    the period back); the same frames with honest ADC times: no gap"""
    import hil_rate_check as rc
    sr, blk = 48000, 512
    n = int(20 * sr / blk)
    t = 100.0 + np.arange(n) * blk / sr
    bad = t.copy()
    per = 0.1344
    for i in range(n):
        ph = (t[i] - 100.0) % per
        if ph < 0.021:                                   # two blocks early by a buffer, then back on time
            bad[i] -= blk / sr
    for adc, want in ((t, 0), (bad, 1)):
        r = h.Ring(sr, 1, secs=30)
        for i in range(n):
            r.push(np.zeros((blk, 1), np.float32), adc[i])
        x = r.cut(101.0, 10.0)
        assert x is not None
        if not want:
            assert r.gaps == []
        else:
            assert len(r.gaps) > 100
            b = rc.beat(r.gaps)
            assert b and abs(b["period"] - per) < 0.01 and abs(abs(b["step_ms"]) - 1000 * blk / sr) < 1.0, b


def test_a_cut_never_stalls_the_callback():
    """the recorder's callback (Ring.push) waits for Ring.lock: a cut must hold it for microseconds whatever the log's
    length. The log as a deque turned into an array per cut held the lock and the GIL ~18 ms once it was full (120 s
    at 48 kHz: 180,000 blocks), and the FM-1's USB input lost 13-27 ms in the takes' tails (polled every 10 ms;
    hil_scan2/3 2026-10-19: 218 gaps, every one after the take's last event, none in the first ~10 min of a run).
    Also: the cuts from a full, wrapped log equal the old whole-log search"""
    rng = np.random.default_rng(7)
    sr = 48000
    r = h.Ring(sr, 1, secs=120)
    blk = 32
    nb = r.M + 5000                                                                 # the log full and wrapped
    adc = 7.0 + np.arange(nb) * blk / sr + rng.uniform(-3e-4, 3e-4, nb)
    x = np.ones((blk, 1), np.float32)
    for i in range(nb):
        r.push(x, adc[i])
    assert r.nlog == nb
    t_end = r.end_time()
    took = []
    for k in range(20):
        t0 = time.perf_counter()
        L, _ = r._snap(t_end - 3.6 + 0.01 * k)
        took.append(time.perf_counter() - t0)
        assert L is not None and len(L) < 6000
    took.sort()
    # ~0.1 ms each; the deque: ~18 ms every cut. The median, not the worst: one OS preemption on a loaded machine
    # (a Docker build alongside) once read 5.2 ms, while the bug makes every cut slow
    assert took[len(took) // 2] < 0.005, took
    # the old search over every entry held (the newest M), for a few cut starts
    keep = np.arange(nb - r.M, nb)
    full = np.stack([keep * blk, adc[keep]], 1).astype(float)
    for t0 in (t_end - 3.6, t_end - 50.0, adc[nb - r.M] + 0.5, adc[nb - r.M] - 1.0):
        k0 = max(0, int(np.searchsorted(full[:, 1], t0, "right")) - 1)
        L, _ = r._snap(t0)
        j = int(np.flatnonzero(L[:, 0] == full[k0, 0])[0])
        assert np.array_equal(L[j:], full[k0:]), t0
        assert L[max(0, int(np.searchsorted(L[:, 1], t0, "right")) - 1), 0] == full[k0, 0]   # cut's own start
        assert abs(r.frame_at(t0) - (full[0, 0] + (t0 - full[0, 1]) * sr if t0 <= full[0, 1] else
                                     np.interp(t0, full[:, 1], full[:, 0]))) < 1e-6
