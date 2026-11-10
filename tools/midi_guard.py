#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""MIDI port open / close under a watchdog, and a process exit that skips rtmidi's destructors.

Why: python-rtmidi (1.5.8, mido's backend) never releases the GIL. Its extension imports PyGILState_Ensure (the input
callback, run on CoreMIDI's receive thread) and no PyEval_SaveThread, so open_port / close_port sit in CoreMIDI
(MIDIPortConnectSource, MIDIPortDispose ...) with the GIL held. mido's Input always installs a Python callback, so a
reply arriving on an open input port (a poller's CC 116) parks CoreMIDI's receive thread on the GIL, and a PortAudio
input callback (sounddevice, Python) parks the audio thread on it too. A CoreMIDI call that then waits for its receive
thread (or for a wedged MIDIServer) never returns: 2026-11-03/04, three runs sat for hours in MidiIn.close_port /
MidiBase.open_port (one with AudioIOProc -> PaUtil_EndBufferProcessing waiting on the audio thread).

So the rules every device tool follows:
  - the MIDI ports open before any audio stream and stay open for the whole run (no reopen per scenario);
  - a port closes only after every audio stream is stopped and every thread using it is joined;
  - every open / close runs under watch(): a native timer (libdispatch on macOS: no Python, no GIL needed, so it
    fires while rtmidi holds the GIL) prints HANG_MSG, flushes and _exit(HANG_EXIT) after TIMEOUT_S;
  - the process ends with exit(): stdout / stderr flushed, the atexit handlers (the audio streams' stops) run, then
    os._exit, so no rtmidi destructor runs at interpreter teardown;
  - unbuffered(): line-buffered stdout / stderr, so a stall watcher sees every line as it is printed.

FRODHES_MIDI_TIMEOUT (seconds) overrides TIMEOUT_S (the host tests use 1 s)."""
import ctypes
import faulthandler
import os
import sys

HANG_EXIT = 75                     # EX_TEMPFAIL: restart MIDIServer and run again (1 = FAIL, 2 = incomplete)
TIMEOUT_S = 20.0                   # an open / close takes milliseconds; MIDIClientCreate at most a second or so
HANG_MSG = "MIDI open/close hung: CoreMIDI; restart with `killall MIDIServer`"
_KEEP = []                         # the C strings and suspended queues of every armed timer (never freed)


def timeout_s():
    v = os.environ.get("FRODHES_MIDI_TIMEOUT", "").strip()
    try:
        return float(v) if v else TIMEOUT_S
    except ValueError:
        return TIMEOUT_S


def flush():
    for f in (sys.stdout, sys.stderr):
        try:
            f.flush()
        except Exception:
            pass


class _Native:
    """a one-shot timer on a private serial dispatch queue: puts(line), fflush(NULL), _exit(code), each a plain C
    function called by libdispatch's own thread (context = the line / NULL / the code). cancel(): the queue suspended,
    so the timer's items never run"""
    lib = None

    @classmethod
    def load(cls):
        if cls.lib is None:
            if sys.platform != "darwin":
                cls.lib = False
                return False
            try:
                L = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
                L.dispatch_queue_create.restype = ctypes.c_void_p
                L.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
                L.dispatch_time.restype = ctypes.c_uint64
                L.dispatch_time.argtypes = [ctypes.c_uint64, ctypes.c_int64]
                L.dispatch_after_f.restype = None
                L.dispatch_after_f.argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
                L.dispatch_suspend.restype = None
                L.dispatch_suspend.argtypes = [ctypes.c_void_p]
                cls.fn = dict(puts=ctypes.cast(L.puts, ctypes.c_void_p).value,
                              fflush=ctypes.cast(L.fflush, ctypes.c_void_p).value,
                              exit=ctypes.cast(L._exit, ctypes.c_void_p).value)
                cls.lib = L
            except Exception:
                cls.lib = False
        return cls.lib

    def __init__(self, secs, line, code):
        L = self.load()
        self.q = L.dispatch_queue_create(b"frodhes.midi_guard", None)
        msg = ctypes.create_string_buffer(line.encode())
        _KEEP.append((msg, self.q))
        ns = int(secs * 1e9)                          # (20 ms apart: timers due together run in any order)
        L.dispatch_after_f(L.dispatch_time(0, ns), self.q, ctypes.addressof(msg), self.fn["puts"])
        L.dispatch_after_f(L.dispatch_time(0, ns + 20_000_000), self.q, None, self.fn["fflush"])
        L.dispatch_after_f(L.dispatch_time(0, ns + 40_000_000), self.q, code, self.fn["exit"])

    def cancel(self):
        self.lib.dispatch_suspend(self.q)


class _Fallback:
    """not macOS: faulthandler's C watchdog (no GIL needed either): the tracebacks, then _exit(1)"""

    def __init__(self, secs, line, code):
        print(f"(watchdog {secs:g} s; on a hang: {line})", file=sys.stderr, flush=True)
        # (the process' own stderr: faulthandler needs a real file descriptor, and a captured sys.stderr, pytest's
        # capsys, has none: 'io.UnsupportedOperation: fileno' on Linux)
        faulthandler.dump_traceback_later(secs, exit=True, file=sys.__stderr__ or sys.stderr)

    def cancel(self):
        faulthandler.cancel_dump_traceback_later()


class watch:
    """with watch("open 'Frodhes' (input)"): the block, or after timeout s HANG_MSG + what, flushed, _exit(HANG_EXIT)"""

    def __init__(self, what, timeout=None):
        self.what, self.secs = what, timeout_s() if timeout is None else float(timeout)

    def __enter__(self):
        flush()                                       # (what was printed so far reaches the log first)
        line = f"{HANG_MSG} ({self.what}, over {self.secs:g} s); exit {HANG_EXIT}"
        self.t = (_Native if _Native.load() else _Fallback)(self.secs, line, HANG_EXIT)
        return self

    def __exit__(self, *a):
        self.t.cancel()
        return False


def open_input(mido, name):
    with watch(f"open {name!r} (input)"):
        return mido.open_input(name)


def open_output(mido, name):
    with watch(f"open {name!r} (output)"):
        return mido.open_output(name)


def close(port, what=None):
    """port.close() under the watchdog; its errors ignored (a port already gone)"""
    if port is None:
        return
    with watch(f"close {what or getattr(port, 'name', '?')!r}"):
        try:
            port.close()
        except Exception:
            pass


def unbuffered():
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(line_buffering=True)
        except Exception:
            pass


def exit(code):
    """the process's end: flushed, the atexit handlers run (the audio streams stopped: hil_scan's Capture, PortAudio's
    terminate) under the watchdog, then os._exit(code): no interpreter teardown, so no rtmidi port destructor runs"""
    code = 0 if code is None else code
    if not isinstance(code, int):                     # SystemExit("message"): printed, exit 1 (as Python does)
        print(code, file=sys.stderr)
        code = 1
    flush()
    import atexit
    with watch("exit (the audio streams' stops)"):
        try:
            atexit._run_exitfuncs()
        except Exception:
            pass
    flush()
    os._exit(code)


def run(main, *args):
    """exit(main(*args)) with unbuffered output; SystemExit's code kept, an error's traceback printed (exit 1)"""
    unbuffered()
    try:
        code = main(*args)
    except SystemExit as e:
        code = e.code
    except BaseException:
        import traceback
        traceback.print_exc()
        code = 1
    exit(code)
