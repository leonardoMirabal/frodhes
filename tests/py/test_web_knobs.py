# SPDX-License-Identifier: GPL-3.0-only
"""The emulator's knobs (web/app.js knob()) in Chromium: a detent per distance of the hand (wheel 50 px, drag 10 px),
at most 2 detents per animation frame and 12 queued, so a trackpad's many small wheel events or a fast drag never
reach the firmware as a burst (before: one detent per wheel event, 30 for a gentle swipe). Needs
web/dist (web/build.sh) and Playwright with Chromium; skipped without them (ci.yml's web job runs it)."""
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DIST = os.environ.get("FRODHES_WEB_DIST") or os.path.join(ROOT, "web", "dist")
sync_api = pytest.importorskip("playwright.sync_api")
if not os.path.exists(os.path.join(DIST, "frodhes.wasm")):
    pytest.skip("no web/dist (web/build.sh)", allow_module_level=True)

# wheel events on the SELECT knob (encoder 0): [deltaY, deltaMode, ms after the previous one] ...; the detents the
# firmware got (the sum of web_enc(0, d)) once everything has settled
WHEEL = """async ev => {
  const k = document.querySelectorAll('.knob.endless')[0];
  window.__det = 0;
  k.dispatchEvent(new PointerEvent('pointerup'));            // (clears the wheel's remainder from the case before)
  for (const [dy, mode, ms] of ev) {
    if (ms) await new Promise(r => setTimeout(r, ms));
    k.dispatchEvent(new WheelEvent('wheel', {deltaY: dy, deltaMode: mode, cancelable: true, bubbles: true}));
  }
  await new Promise(r => setTimeout(r, 400));
  return window.__det;
}"""


@pytest.fixture(scope="module")
def page():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "-d", DIST],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    local = os.path.join(ROOT, ".local", "playwright")
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and os.path.isdir(local):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = local
    try:
        with sync_api.sync_playwright() as p:
            try:
                b = p.chromium.launch()
            except Exception as e:                     # noqa: BLE001 (no browser installed)
                pytest.skip(f"no Chromium for Playwright: {e}")
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(f"http://127.0.0.1:{port}/")
            pg.wait_for_function("window.Module && window.Module._web_enc && window.Module.calledRun", timeout=30000)
            # count what reaches the firmware (encoder 0), and the largest step of one call; a property, since the
            # Emscripten runtime assigns Module._web_enc again after start-up
            pg.evaluate("""() => { let real = Module._web_enc; window.__det = 0; window.__max = 0;
                Object.defineProperty(Module, "_web_enc", { configurable: true, set(v) { real = v; },
                  get() { return (id, d) => { if (id === 0) { window.__det += d;
                    window.__max = Math.max(window.__max, Math.abs(d)); } return real(id, d); }; } }); }""")
            yield pg, errs
            b.close()
    finally:
        srv.terminate()


def wheel(pg, ev):
    return pg.evaluate(WHEEL, ev)


def test_trackpad_swipe_is_a_few_detents(page):
    pg, errs = page
    # a gentle 120 px swipe up: 30 events of 4 px, 8 ms apart (the old handler: 30 detents)
    assert wheel(pg, [[-4, 0, 8]] * 30) == 2
    assert wheel(pg, [[4, 0, 8]] * 30) == -2                         # and down
    # a fling: 600 px in 30 fast events, then a decaying inertia tail (~420 px): about 20, not 100
    fling = [[-20, 0, 8]] * 30 + [[-max(1, 12 - i // 6), 0, 16] for i in range(70)]
    assert 12 <= wheel(pg, fling) <= 21
    assert pg.evaluate("window.__max") == 1 and not errs


def test_mouse_wheel_notches(page):
    pg, errs = page
    assert wheel(pg, [[-100, 0, 0]]) == 2                             # a pixel-mode notch (Chrome, Safari)
    assert wheel(pg, [[100, 0, 0], [100, 0, 150], [100, 0, 150]]) == -6
    assert wheel(pg, [[-1, 1, 0]]) == 1                               # deltaMode 1: 50 px a line
    assert wheel(pg, [[-1, 2, 0]]) == 8                               # deltaMode 2: 400 px a page
    assert not errs


def test_drag(page):
    pg, errs = page
    box = pg.locator(".knob.endless").first.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def drag(dy, steps, pause):
        pg.evaluate("() => { window.__det = 0; }")
        pg.mouse.move(x, y)
        pg.mouse.down()
        for i in range(steps):
            pg.mouse.move(x, y - dy * (i + 1) / steps)
            pg.wait_for_timeout(pause)
        pg.mouse.up()
        pg.wait_for_timeout(300)
        return pg.evaluate("window.__det")
    assert drag(90, 30, 20) == 9                                       # slowly: 10 px a detent, every one arrives
    assert drag(-90, 30, 20) == -9
    assert drag(300, 1, 0) == 12                                       # one jump of 300 px: at most 12 queued
    assert pg.evaluate("window.__max") == 1 and not errs
