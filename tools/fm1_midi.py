#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The MIDI channel the device tools send on: one default for every tool that talks to the FM-1.

The default is channel 8 (1-based). An FM-1 on CHANNEL ALL hears every channel, so 8 works there too, and an FM-1
set to CHANNEL 8 (the user's own set-up) hears it as well. FRODHES_MIDI_CH=1..16 overrides it.

    ChannelOut(port)      a mido output whose channel messages (notes, CCs, program change, bend ...) all go out on
                          the channel; clock, Start / Stop and SysEx pass unchanged. .raw is the port itself, for the
                          tools that pick their channels on purpose (the CHANNEL filter tests, the monkey).
    rotate(b)             raw MIDI bytes moved from channel c to c + (channel - 1) (mod 16): a stream whose main
                          channel is 1 (the monkey's) keeps its spread of channels but plays mainly on the channel.

The FM-1 answers its developer CCs (116 ...) on channel 1 whatever its CHANNEL: the readers ignore the channel."""
import os

DEFAULT = 8
ENV = "FRODHES_MIDI_CH"


def channel(env=None):
    """the channel, 1-based: FRODHES_MIDI_CH (1..16) or DEFAULT"""
    v = (os.environ if env is None else env).get(ENV, "").strip()
    if not v:
        return DEFAULT
    try:
        ch = int(v)
    except ValueError:
        ch = 0
    if not 1 <= ch <= 16:
        raise SystemExit(f"{ENV}={v!r}: a MIDI channel 1..16")
    return ch


class ChannelOut:
    """a MIDI output whose channel messages go out on one channel (1-based ch; default channel())"""

    def __init__(self, port, ch=None):
        self.raw = getattr(port, "raw", port)
        self.ch = channel() if ch is None else int(ch)
        self.name = getattr(self.raw, "name", "?")

    def send(self, msg):
        self.raw.send(msg.copy(channel=self.ch - 1) if hasattr(msg, "channel") else msg)

    def __getattr__(self, k):
        return getattr(self.raw, k)


def rotate(b, ch=None):
    """raw MIDI bytes (a list): a channel message moved by channel - 1; others unchanged"""
    ch = channel() if ch is None else int(ch)
    if b and 0x80 <= b[0] < 0xF0:
        return [(b[0] & 0xF0) | ((b[0] + ch - 1) & 0x0F)] + list(b[1:])
    return list(b)


def say(ch=None):
    """the line a tool prints when it opens the FM-1"""
    ch = channel() if ch is None else ch
    return f"MIDI channel {ch} (the FM-1 on CHANNEL {ch} or ALL; {ENV}=1..16 to change)"
