# SPDX-License-Identifier: GPL-3.0-only
"""The emulator in a browser (web/app.js ?selftest): C4 on RHODES, VIBES and MARIMBA sounds and the screen is drawn,
on the two-core image web/build.sh makes since 2026-11-15 (core 1's job on the page's thread, 128-frame interrupts,
two per 256-frame block). Needs web/dist (web/build.sh) and Playwright with Chromium (or the system Chrome);
skipped without them (ci.yml's web job runs it). The host's numbers 2026-11-15: -30.5 / -25.9 / -26.2 dB rms."""
import functools
import http.server
import os
import re
import socketserver
import threading

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DIST = os.environ.get("FRODHES_WEB_DIST") or os.path.join(ROOT, "web", "dist")
sync_api = pytest.importorskip("playwright.sync_api")
if not os.path.exists(os.path.join(DIST, "frodhes.wasm")):
    pytest.skip("no web/dist (web/build.sh)", allow_module_level=True)


def test_selftest_every_engine():
    local = os.path.join(ROOT, ".local", "playwright")
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and os.path.isdir(local):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = local
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DIST)
    h.log_message = lambda *a: None
    srv = socketserver.TCPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = {}
    try:
        with sync_api.sync_playwright() as p:
            b = None
            for kw in ({}, {"channel": "chrome"}):
                try:
                    b = p.chromium.launch(**kw)
                    break
                except Exception:                      # noqa: BLE001 (no browser installed)
                    continue
            if b is None:
                pytest.skip("no Chromium / Chrome for Playwright")
            for q in ("selftest", "selftest&vibes", "selftest&marimba"):
                pg = b.new_page()
                errs = []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html?{q}")
                pg.wait_for_function("document.title.startsWith('SELFTEST')", timeout=30000)
                m = re.search(r"rms=(-?[\d.]+)dB screen=(\d+)px", pg.title())
                out[q] = (float(m.group(1)), int(m.group(2)), errs)
                pg.close()
            b.close()
    finally:
        srv.shutdown()
    for q, (rms, lit, errs) in out.items():
        assert not errs, (q, errs)
        assert -36 < rms < -20, (q, rms)                # (a note, not silence or a clipped mess)
        assert lit > 5000, (q, lit)
