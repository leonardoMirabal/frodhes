/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window on Windows (gui_view.h): a child HWND that blits gui_core's canvas with SetDIBitsToDevice (a
 * top-down 32-bit DIB: the canvas's 0xFFRRGGBB words are BGRA bytes, as GDI wants them). Sizes are physical pixels
 * (CLAP's rule on Windows: the host's set_scale gives the factor; the canvas is drawn at the pixel size). The window
 * class is registered under a name unique to this module (two copies of the plugin in one host keep their own).
 *
 * B-M2b status: compiled by the Windows CI build; not yet run in a Windows host (the plan's B-M4). */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <windowsx.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <clap/clap.h>
#include "gui_view.h"

const char *const FRP_VIEW_API = CLAP_WINDOW_API_WIN32;

struct frp_view {
    frp_gui_t *core;
    HWND hwnd;
    uint32_t w, h;                   /* logical points */
    double scale;
    int tracking, captured;
};

static ATOM s_cls;
static wchar_t s_name[64];

static double now_s(void)
{
    static LARGE_INTEGER f;
    LARGE_INTEGER c;
    if (!f.QuadPart) QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&c);
    return (double)c.QuadPart / (double)f.QuadPart;
}
static void frame(frp_view_t *v, int force)
{
    int d[4];
    if (!v->hwnd) return;
    if (frp_gui_tick(v->core, now_s()) || force) {
        if (frp_gui_paint(v->core, d)) {
            RECT r = {d[0], d[1], d[2], d[3]};
            InvalidateRect(v->hwnd, &r, FALSE);
        } else if (force) InvalidateRect(v->hwnd, NULL, FALSE);
    }
}
static void layout(frp_view_t *v)
{
    RECT r;
    if (!v->hwnd) return;
    GetClientRect(v->hwnd, &r);
    if (r.right > 0 && r.bottom > 0) frp_gui_resize(v->core, r.right, r.bottom);
    frame(v, 1);
}
static int mods_now(void)
{
    return (GetKeyState(VK_SHIFT) < 0 ? FRP_MOD_SHIFT : 0) | (GetKeyState(VK_MENU) < 0 ? FRP_MOD_ALT : 0) |
           (GetKeyState(VK_CONTROL) < 0 ? FRP_MOD_CMD : 0);   /* (control: the command key's role, as on Windows) */
}
static void mouse(frp_view_t *v, int ev, LPARAM lp, int clicks)
{
    float k = frp_gui_px_to_logical(v->core);
    frp_gui_mouse(v->core, ev, (float)GET_X_LPARAM(lp) * k, (float)GET_Y_LPARAM(lp) * k, mods_now(), clicks);
    frame(v, 0);
}
static LRESULT CALLBACK wndproc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    frp_view_t *v = (frp_view_t *)GetWindowLongPtrW(hwnd, GWLP_USERDATA);
    if (!v) return DefWindowProcW(hwnd, msg, wp, lp);
    switch (msg) {
    case WM_ERASEBKGND: return 1;
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(hwnd, &ps);
        int w, h;
        const uint32_t *px = frp_gui_pixels(v->core, &w, &h);
        RECT cr;
        GetClientRect(hwnd, &cr);
        if (px) {
            BITMAPINFO bi;
            memset(&bi, 0, sizeof bi);
            bi.bmiHeader.biSize = sizeof bi.bmiHeader;
            bi.bmiHeader.biWidth = w;
            bi.bmiHeader.biHeight = -h;                  /* top-down */
            bi.bmiHeader.biPlanes = 1;
            bi.bmiHeader.biBitCount = 32;
            bi.bmiHeader.biCompression = BI_RGB;
            SetDIBitsToDevice(dc, 0, 0, (DWORD)w, (DWORD)h, 0, 0, 0, (UINT)h, px, &bi, DIB_RGB_COLORS);
        }
        if (!px || w < cr.right || h < cr.bottom) {     /* a host that keeps another aspect: a dark margin */
            HBRUSH b = (HBRUSH)GetStockObject(BLACK_BRUSH);
            RECT a = {px ? w : 0, 0, cr.right, cr.bottom}, c = {0, px ? h : 0, cr.right, cr.bottom};
            FillRect(dc, &a, b);
            FillRect(dc, &c, b);
        }
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_SIZE: layout(v); return 0;
    case WM_TIMER: frame(v, 0); return 0;
    case WM_GETDLGCODE: return DLGC_WANTALLKEYS | DLGC_WANTARROWS | DLGC_WANTTAB;
    case WM_LBUTTONDOWN: case WM_LBUTTONDBLCLK:
        SetFocus(hwnd);
        SetCapture(hwnd);
        v->captured = 1;
        mouse(v, FRP_MOUSE_DOWN, lp, msg == WM_LBUTTONDBLCLK ? 2 : 1);
        return 0;
    case WM_LBUTTONUP:
        if (v->captured) { ReleaseCapture(); v->captured = 0; }
        mouse(v, FRP_MOUSE_UP, lp, 1);
        return 0;
    case WM_RBUTTONDOWN: mouse(v, FRP_MOUSE_RDOWN, lp, 1); return 0;
    case WM_MOUSEMOVE:
        if (!v->tracking) {
            TRACKMOUSEEVENT t = {sizeof t, TME_LEAVE, hwnd, 0};
            TrackMouseEvent(&t);
            v->tracking = 1;
        }
        mouse(v, v->captured ? FRP_MOUSE_DRAG : FRP_MOUSE_MOVE, lp, 1);
        return 0;
    case WM_MOUSELEAVE:
        v->tracking = 0;
        frp_gui_mouse(v->core, FRP_MOUSE_LEAVE, -1.f, -1.f, 0, 1);
        frame(v, 0);
        return 0;
    case WM_MOUSEWHEEL: {
        POINT p = {GET_X_LPARAM(lp), GET_Y_LPARAM(lp)};
        float k = frp_gui_px_to_logical(v->core);
        ScreenToClient(hwnd, &p);
        frp_gui_wheel(v->core, (float)p.x * k, (float)p.y * k, (float)GET_WHEEL_DELTA_WPARAM(wp) / (float)WHEEL_DELTA,
                      mods_now());
        frame(v, 0);
        return 0;
    }
    case WM_KEYDOWN: {
        int key = 0;
        switch (wp) {
        case VK_TAB: key = FRP_KEY_TAB; break;
        case VK_ESCAPE: key = FRP_KEY_ESC; break;
        case VK_RETURN: key = FRP_KEY_ENTER; break;
        case VK_LEFT: key = FRP_KEY_LEFT; break;
        case VK_RIGHT: key = FRP_KEY_RIGHT; break;
        case VK_UP: key = FRP_KEY_UP; break;
        case VK_DOWN: key = FRP_KEY_DOWN; break;
        case VK_HOME: key = FRP_KEY_HOME; break;
        case VK_END: key = FRP_KEY_END; break;
        }
        if (key && frp_gui_key(v->core, key, mods_now())) { frame(v, 0); return 0; }
        break;                                           /* (the host's key) */
    }
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}
static HINSTANCE module_of(void)
{
    HMODULE m = 0;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCWSTR)(void *)&wndproc, &m);
    return (HINSTANCE)m;
}
static int register_class(void)
{
    WNDCLASSW wc;
    if (s_cls) return 1;
    swprintf(s_name, 64, L"FrodhesView_%p", (void *)&s_cls);   /* this module's own */
    memset(&wc, 0, sizeof wc);
    wc.style = CS_DBLCLKS;
    wc.lpfnWndProc = wndproc;
    wc.hInstance = module_of();
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.lpszClassName = s_name;
    s_cls = RegisterClassW(&wc);
    return s_cls != 0;
}

frp_view_t *frp_view_new(frp_gui_t *core, uint32_t w, uint32_t h)
{
    frp_view_t *v;
    if (!register_class()) return 0;
    v = calloc(1, sizeof *v);
    if (!v) return 0;
    v->core = core; v->w = w; v->h = h; v->scale = 1.0;
    return v;
}
void frp_view_free(frp_view_t *v)
{
    if (!v) return;
    if (v->hwnd) {
        KillTimer(v->hwnd, 1);
        SetWindowLongPtrW(v->hwnd, GWLP_USERDATA, 0);
        DestroyWindow(v->hwnd);
    }
    free(v);
}
int frp_view_attach(frp_view_t *v, void *parent)
{
    int pw = (int)(v->w * v->scale + 0.5), ph = (int)(v->h * v->scale + 0.5);
    if (!parent || v->hwnd) return -1;
    v->hwnd = CreateWindowExW(0, s_name, L"Frodhes", WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS, 0, 0, pw, ph, (HWND)parent,
                              NULL, module_of(), NULL);
    if (!v->hwnd) return -1;
    SetWindowLongPtrW(v->hwnd, GWLP_USERDATA, (LONG_PTR)v);
    SetTimer(v->hwnd, 1, 16, NULL);                      /* ~60 Hz; a frame paints only what changed */
    layout(v);
    return 0;
}
void frp_view_size(frp_view_t *v, uint32_t w, uint32_t h)
{
    v->w = w; v->h = h;
    if (v->hwnd) SetWindowPos(v->hwnd, NULL, 0, 0, (int)(w * v->scale + 0.5), (int)(h * v->scale + 0.5),
                              SWP_NOZORDER | SWP_NOMOVE | SWP_NOACTIVATE);
}
void frp_view_show(frp_view_t *v, int show)
{
    if (v->hwnd) ShowWindow(v->hwnd, show ? SW_SHOWNA : SW_HIDE);
    if (show) frame(v, 1);
}
void frp_view_scale(frp_view_t *v, double scale)
{
    v->scale = scale > 0.0 ? scale : 1.0;
    frp_view_size(v, v->w, v->h);
}
const uint32_t *frp_view_grab(frp_view_t *v, int *w, int *h) { return frp_gui_pixels(v->core, w, h); }
