#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Schwung's remote UI over its WebSocket (schwung-manager remote_ui.go): read what a slot holds and set its preset,
without ssh. The device tools (tools/move_hil.py d2) use it to check that slot 1 holds Frodhes and to load a preset
before a run.

    tools/move_ws.py info [--slot 1]            the slot's module and preset (index and name)
    tools/move_ws.py preset N [--slot 1]        load preset N (0-based: 0 = F01, 12 = F13, 14 = F15)

The protocol (remote_ui.go wsMessage / wsSlotInfo / wsParamUpdate): JSON {"type", "slot", "key", "value"}, the slot
0-based (slot 1 on the Move's screen = 0). "subscribe" answers with slot_info ({"synth": module id, ...}), then
param_update messages ({"params": {"<comp>:<key>": value}}: the slot settings, the preset browser's keys from
ui_hierarchy, or, for Frodhes with its own screen and no ui_hierarchy, the keys chain_params names as extra_keys:
preset, preset_name, preset_count; the parameters); "set_param" of "synth:preset" makes the server re-read and push the component's
values. An "error" message is raised. --url (default $MOVE_WS, else ws://move.local:7700/ws/remote-ui).

Never run from a test or a gate without the user's OK to use the Move (the device rules)."""
import argparse
import json
import os
import sys
import time

URL = os.environ.get("MOVE_WS", "ws://move.local:7700/ws/remote-ui")
COMP = "synth"
PIECE = 250                                     # a set_param value through the web set ring: under 256 B (shmwebring.go)


try:
    from websockets.exceptions import ConnectionClosed
except ImportError:                              # (the CLI's help without websockets installed)
    class ConnectionClosed(Exception):
        pass


class SchwungError(RuntimeError):
    pass


OPEN_TRIES = 3                                  # a connection that does not open is tried again, after OPEN_PAUSE s
OPEN_PAUSE = 0.5
_ANSWERED = {}                                  # (host, port) -> the address that last answered


def _sock(url, timeout):
    """a TCP connection to the url's host, its IPv4 addresses first (the one that answered last before them). The
    Move answers move.local on an IPv6 link-local address and on its USB-C network's IPv4 one; websockets tried the
    IPv6 one first, and a connect that hung there for the whole open timeout ended a d2 take with "timed out"
    before any message (the VIBES usb-host, 2026-10-28)"""
    import socket
    from urllib.parse import urlsplit
    u = urlsplit(url)
    host, port = u.hostname, u.port or (443 if u.scheme == "wss" else 80)
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    known = _ANSWERED.get((host, port))
    infos.sort(key=lambda i: (i[4] != known, i[0] != socket.AF_INET))
    end, err = time.monotonic() + timeout, None
    for fam, typ, proto, _, sa in infos:
        left = end - time.monotonic()
        if left <= 0:
            break
        s = socket.socket(fam, typ, proto)
        s.settimeout(left)
        try:
            s.connect(sa)
        except OSError as e:                    # (TimeoutError included)
            err = e
            s.close()
            continue
        s.settimeout(None)
        _ANSWERED[(host, port)] = sa
        return s
    raise err if err is not None else TimeoutError("no address of %s answered in %.1f s" % (host, timeout))


class Remote:
    """one connection; slots 0-based. The connection is opened up to OPEN_TRIES times"""

    def __init__(self, url=URL, timeout=5.0):
        from websockets.sync.client import connect
        self.url, self.timeout, self.last = url, timeout, {}
        for k in range(OPEN_TRIES):
            try:
                self._cm = connect(url, sock=_sock(url, timeout), open_timeout=timeout, max_size=None,
                                   ping_interval=20, ping_timeout=90)
                # (the manager answers a ping only between two messages it handles, and a set_param waits on the shim)
                self.ws = self._cm.__enter__()
                break
            except (OSError, TimeoutError):
                if k == OPEN_TRIES - 1:
                    raise
                time.sleep(OPEN_PAUSE)
        self.params = {}                         # slot -> {key: value}, everything seen
        self.info = {}                           # slot -> the last slot_info

    def close(self):
        self._cm.__exit__(None, None, None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def send(self, typ, slot=None, key=None, value=None):
        m = {"type": typ}
        if slot is not None:
            m["slot"] = int(slot)
        if key is not None:
            m["key"] = key
        if value is not None:
            m["value"] = str(value)
        self.ws.send(json.dumps(m))

    def _take(self, m):
        t, s = m.get("type"), m.get("slot", 0)
        if t == "error":
            raise SchwungError(m.get("message", "error"))
        if t == "slot_info":
            self.info[s] = m
        elif t == "param_update":
            self.params.setdefault(s, {}).update(m.get("params") or {})
        self.last = m
        return m

    def until(self, done, timeout=None):
        """read messages until done(self) is true; TimeoutError after timeout s"""
        end = time.monotonic() + (self.timeout if timeout is None else timeout)
        while not done(self):
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError("no answer from %s" % self.url)
            try:
                raw = self.ws.recv(timeout=left)
            except TimeoutError:
                raise TimeoutError("no answer from %s" % self.url) from None
            self._take(json.loads(raw))

    def subscribe(self, slot=0, timeout=None):
        """slot_info and the preset keys of the slot (when its synth has a preset browser), as the server sends them
        now"""
        self.info.pop(slot, None)
        self.params.get(slot, {}).pop(COMP + ":preset", None)
        self.send("subscribe", slot)

        def got(r):
            if slot not in r.info:
                return False
            if not r.info[slot].get("synth"):
                return True                      # (an empty slot: no params will come)
            return COMP + ":preset" in r.params.get(slot, {})
        self.until(got, timeout)
        return self.state(slot)

    def state(self, slot=0):
        p, i = self.params.get(slot, {}), self.info.get(slot, {})
        pr = p.get(COMP + ":preset")
        return {"slot": slot, "module": i.get("synth", ""), "preset": int(pr) if pr not in (None, "") else None,
                "preset_name": p.get(COMP + ":preset_name"), "preset_count": p.get(COMP + ":preset_count"),
                "receive_channel": p.get("slot:receive_channel"), "forward_channel": p.get("slot:forward_channel")}

    def set_param(self, slot, key, value):
        self.send("set_param", slot, key, value)

    def pump(self, secs):
        """read whatever comes for secs (an error message raises SchwungError)"""
        try:
            self.until(lambda r: False, secs)
        except TimeoutError:
            pass

    def play(self, slot, take, dur_ms, events, lead_ms=100, pace=0.003):
        """the plugin's dev player (move/move_bridge.h dev_play): events [(t_ms, status, d1, d2)] from the take's
        start, in pieces under the set ring's 256 B; the plugin starts on its 2560 ms grid and writes the tap
        <data dir>/tap/<take>.wav (dur_ms long) and <take>.json"""
        key = COMP + ":dev_play"
        self.set_param(slot, key, "begin %s %d" % (take, int(dur_ms)))
        k, chunk = 0, "#0 "                       # (each piece numbered: the plugin refuses one out of turn)
        for t, st, d1, d2 in sorted(events, key=lambda e: e[0]):
            ev = "%d %d %d %d;" % (int(t), int(st), int(d1) & 127, int(d2) & 127)
            if len(chunk) + len(ev) > PIECE:
                self.set_param(slot, key + "_add", chunk)
                time.sleep(pace)                  # (the shim drains the ring once a 2.9 ms frame)
                k += 1
                chunk = "#%d " % k
            chunk += ev
        if chunk.strip() != "#%d" % k:
            self.set_param(slot, key + "_add", chunk)
        self.set_param(slot, key, "go %d" % int(lead_ms))
        self.pump(0.2)

    def set_preset(self, slot, n, timeout=None):
        """load preset n and wait until the server reports it with its name. (The shim's notify ring carries the
        index alone, so the first update can come with the old name still cached; the manager's full re-read
        (remote_ui.go: 50 ms after a preset write) brings both, read together. No name after it: subscribe again)"""
        p = self.params.setdefault(slot, {})
        p.pop(COMP + ":preset", None)
        p.pop(COMP + ":preset_name", None)
        self.set_param(slot, COMP + ":preset", int(n))
        self.until(lambda r: r.params.get(slot, {}).get(COMP + ":preset") == str(int(n)), timeout)
        if COMP + ":preset_name" not in (self.last.get("params") or {}):
            p.pop(COMP + ":preset_name", None)               # (a name that came before the index: the old one)
        try:
            self.until(lambda r: COMP + ":preset_name" in r.params.get(slot, {}), 1.0)
        except TimeoutError:
            self.subscribe(slot, timeout)
        return self.state(slot)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("info", "preset"))
    ap.add_argument("n", nargs="?", type=int)
    ap.add_argument("--slot", type=int, default=1, help="the slot as on the Move's screen, 1-4 (default 1)")
    ap.add_argument("--url", default=URL)
    ap.add_argument("--timeout", type=float, default=5.0)
    a = ap.parse_args(argv)
    s = a.slot - 1
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import move_install
    try:
        with move_install.move_lock("move_ws " + a.cmd), Remote(a.url, a.timeout) as r:
            st = r.subscribe(s)
            if a.cmd == "preset":
                if a.n is None:
                    ap.error("preset needs N")
                st = r.set_preset(s, a.n)
                r.pump(1.5)                       # (the manager's re-read to every subscriber runs under this
                #                                   connection: closing it in the middle cuts the others off)
    except (OSError, TimeoutError, SchwungError) as e:
        print("schwung %s: %s" % (a.url, e))
        return 2
    print(json.dumps(st))
    return 0


if __name__ == "__main__":
    sys.exit(main())
