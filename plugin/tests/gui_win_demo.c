/* SPDX-License-Identifier: GPL-3.0-only */
/* The Windows view (plugin/gui_win.c) outside a host: a top-level window holding the view over the stand-in plugin
 * (gui_fake_host.h), sized for the monitor's DPI.
 *   frp_gui_demo.exe              the window, to try by hand (drag, SHIFT, wheel, double-click, right-click, keys)
 *   frp_gui_demo.exe --selftest   off screen: drives the view with real window messages (press / move / release on
 *                                 a knob, the wheel, a double-click, TAB + an arrow), reads its pixels back through
 *                                 PrintWindow, prints PASS / FAIL lines to frp_gui_demo.log and stdout, exits 0 / 1 */
#include <windows.h>
#include <stdlib.h>
#include "gui_fake_host.h"

static fake_host h;
static frg_ctl *ctl;
static frg_view *view;
static FILE *logf;
static int fails;

static void say(int ok, const char *what, int a, int b)
{
    char line[200];
    snprintf(line, sizeof line, "%s %s (%d, %d)\n", ok ? "PASS" : "FAIL", what, a, b);
    if (!ok) fails++;
    fputs(line, stdout);
    if (logf) fputs(line, logf);
}

static LRESULT CALLBACK top_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_SIZE: if (view) frg_view_resize(view, LOWORD(lp), HIWORD(lp)); return 0;
    case WM_DESTROY: PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static void pump(void)
{
    MSG m;
    while (PeekMessageW(&m, 0, 0, 0, PM_REMOVE)) { TranslateMessage(&m); DispatchMessageW(&m); }
}
static HWND child_of(HWND top) { return GetWindow(top, GW_CHILD); }
static LPARAM at(int w, float lx, float ly)      /* a logical point as the child's client coordinates */
{
    float s = (float)w / (float)FRUI_W;
    return MAKELPARAM((int)(lx * s), (int)(ly * s));
}

static int selftest(HWND top, int w, int hgt)
{
    HWND c = child_of(top);
    float x, y, s = (float)w / (float)FRUI_W;
    int v, i, lit = 0;
    say(c != 0, "the view is a child window", w, hgt);
    if (!c) return 1;
    UpdateWindow(c);
    pump();
    {   /* (a window with no visible area gets no WM_PAINT: lay the view out through WM_PRINTCLIENT too) */
        HDC dc = GetDC(c), mem = CreateCompatibleDC(dc);
        HBITMAP bm = CreateCompatibleBitmap(dc, w, hgt), old = (HBITMAP)SelectObject(mem, bm);
        SendMessageW(c, WM_PRINTCLIENT, (WPARAM)mem, PRF_CLIENT);
        SelectObject(mem, old); DeleteObject(bm); DeleteDC(mem); ReleaseDC(c, dc);
    }
    say(fh_find(ctl, frg_ctl_frui(ctl), FRW_KNOB, FRP_BARK, &x, &y) >= 0, "the first paint laid out the BARK knob", 0, 0);
    /* drag: press, two moves up 100 logical px, release */
    v = h.v[FRP_BARK];
    SendMessageW(c, WM_LBUTTONDOWN, MK_LBUTTON, at(w, x, y));
    SendMessageW(c, WM_MOUSEMOVE, MK_LBUTTON, at(w, x, y - 50.f));
    SendMessageW(c, WM_MOUSEMOVE, MK_LBUTTON, at(w, x, y - 100.f));
    SendMessageW(c, WM_LBUTTONUP, 0, at(w, x, y - 100.f));
    say(h.v[FRP_BARK] >= v + 62 && h.gestures_open == 0, "drag up 100 logical px: half the range, gesture closed", v, h.v[FRP_BARK]);
    /* the wheel (screen coordinates) */
    v = h.v[FRP_BARK];
    {
        POINT p = { (int)(x * s), (int)(y * s) };
        ClientToScreen(c, &p);
        SendMessageW(c, WM_MOUSEWHEEL, MAKEWPARAM(0, (WORD)(short)-WHEEL_DELTA), MAKELPARAM(p.x, p.y));
    }
    say(h.v[FRP_BARK] == v - 2, "a wheel notch down: -2", v, h.v[FRP_BARK]);
    /* double-click: the default */
    SendMessageW(c, WM_LBUTTONDOWN, MK_LBUTTON, at(w, x, y));
    SendMessageW(c, WM_LBUTTONUP, 0, at(w, x, y));
    SendMessageW(c, WM_LBUTTONDBLCLK, MK_LBUTTON, at(w, x, y));
    SendMessageW(c, WM_LBUTTONUP, 0, at(w, x, y));
    say(h.v[FRP_BARK] == 64 && h.gestures_open == 0, "double-click: the default", h.v[FRP_BARK], h.gestures_open);
    /* the keyboard */
    SetFocus(c);
    say(SendMessageW(c, WM_GETDLGCODE, 0, 0) & DLGC_WANTARROWS, "the view asks for the arrows and TAB", 0, 0);
    SendMessageW(c, WM_KEYDOWN, VK_TAB, 0);
    i = frg_ctl_model(ctl)->focus & 0xff;
    v = frui_value(frg_ctl_model(ctl), i);
    SendMessageW(c, WM_KEYDOWN, v < frui_range(i) ? VK_UP : VK_DOWN, 0);
    say(frui_value(frg_ctl_model(ctl), i) != v, "TAB + an arrow moves the focused control", v, frui_value(frg_ctl_model(ctl), i));
    /* the pixels, through PrintWindow (WM_PRINTCLIENT) */
    {
        BITMAPINFO bi;
        void *bits = 0;
        HDC dc = CreateCompatibleDC(0);
        HBITMAP bm;
        memset(&bi, 0, sizeof bi);
        bi.bmiHeader.biSize = sizeof bi.bmiHeader;
        bi.bmiHeader.biWidth = w; bi.bmiHeader.biHeight = -hgt; bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32; bi.bmiHeader.biCompression = BI_RGB;
        bm = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, 0, 0);
        SelectObject(dc, bm);
        SendMessageW(c, WM_PRINTCLIENT, (WPARAM)dc, PRF_CLIENT);
        GdiFlush();
        for (i = 0; bits && i < w * hgt; i += 97) lit += (((uint32_t *)bits)[i] & 0xffffff) != 0;
        say(lit > 500, "the view's pixels read back (sampled pixels not black)", lit, w * hgt / 97);
        DeleteDC(dc); DeleteObject(bm);
    }
    say(frg_view_scale(view) >= 1.f, "the DPI scale x100", (int)(frg_view_scale(view) * 100.f), 0);
    return fails != 0;
}

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show)
{
    frg_ctl_host ch;
    frui_font *f = frui_font_load(frp_font_text, frp_font_text_len), *d = frui_font_load(frp_font_display, frp_font_display_len);
    WNDCLASSW wc;
    HWND top;
    RECT r;
    int test = cmd && strstr(cmd, "--selftest") != 0, w, hh, rc = 0;
    (void)prev;
    if (test) logf = fopen("frp_gui_demo.log", "w");
    /* per-monitor DPI for this demo process (a host sets its own) */
    {
        HMODULE u = GetModuleHandleW(L"user32.dll");
        typedef BOOL(WINAPI * set_ctx_fn)(HANDLE);
        set_ctx_fn fn = u ? (set_ctx_fn)(void *)GetProcAddress(u, "SetProcessDpiAwarenessContext") : 0;
        if (fn) fn((HANDLE)-4);               /* DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 */
    }
    say(f && d, "the embedded fonts load", (int)frp_font_text_len, (int)frp_font_display_len);
    if (!f || !d) return 1;
    fh_init(&h, &ch);
    ctl = frg_ctl_new(&ch, f, d, FRUI_LOOK_A3);
    view = frg_view_create(frg_ctl_client(ctl), 1, 1);
    frg_view_default_size(view, &w, &hh);
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = top_proc; wc.hInstance = inst; wc.lpszClassName = L"FrodhesDemo";
    wc.hCursor = LoadCursorW(0, (LPCWSTR)IDC_ARROW);
    RegisterClassW(&wc);
    r.left = 0; r.top = 0; r.right = w; r.bottom = hh;
    AdjustWindowRect(&r, WS_OVERLAPPEDWINDOW, FALSE);
    top = CreateWindowW(L"FrodhesDemo", L"Frodhes (view demo)", WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
                        test ? 0 : CW_USEDEFAULT, test ? 0 : CW_USEDEFAULT, r.right - r.left, r.bottom - r.top,
                        0, 0, inst, 0);
    frg_view_resize(view, w, hh);
    say(frg_view_set_parent(view, top) == 0, "the view goes into the window", w, hh);
    ShowWindow(top, test ? SW_SHOWNOACTIVATE : show);
    if (test) rc = selftest(top, w, hh);
    else {
        MSG m;
        while (GetMessageW(&m, 0, 0, 0) > 0) { TranslateMessage(&m); DispatchMessageW(&m); }
    }
    frg_view_destroy(view);
    if (IsWindow(top)) DestroyWindow(top);
    frg_ctl_free(ctl);
    frui_font_free(f); frui_font_free(d);
    if (test) { say(!fails, "selftest", fails, 0); if (logf) fclose(logf); }
    return rc;
}
