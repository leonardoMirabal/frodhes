/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window on Windows (plugin/gui_platform.h): a child HWND in the host's window.
 *   pixels   a top-down 32-bit DIB section whose bits are the frui canvas itself (frui's 0xFFRRGGBB in memory is
 *            GDI's B G R A byte order): frui draws straight into it, WM_PAINT blits the dirty rectangle (BitBlt),
 *            no conversion, no scaling. The canvas is the window's client size in physical pixels.
 *   DPI      per monitor: GetDpiForWindow (Windows 10 1607+, looked up at run time; older: the screen's LOGPIXELSX),
 *            WM_DPICHANGED_AFTERPARENT when the host's window moves to another monitor. The view never changes the
 *            thread's DPI awareness: it follows the host's (a host that is not DPI aware gets 96 and Windows
 *            stretches its whole window, as for every other plugin there).
 *   input    the mouse with capture during a drag (WM_LBUTTONDOWN .. UP, CS_DBLCLKS for the double-click), the wheel
 *            (WM_MOUSEWHEEL, screen coordinates), the keyboard (WM_GETDLGCODE asks for every key, so a host's
 *            dialog does not take the arrows / TAB; keys the controller does not use go to the parent window, so
 *            the host's own shortcuts keep working), WM_MOUSELEAVE through TrackMouseEvent
 *   timer    SetTimer 33 ms on the UI thread: the client's tick says whether anything drawn changed
 * The window class is registered once per module (the name carries the module's address: two copies of the plugin
 * in one host never share a class) and unregistered when the last view goes, so the DLL can unload. */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <windowsx.h>
#include <stdio.h>
#include <stdlib.h>
#include "gui_platform.h"
#include "ui/frui.h"

#ifndef WM_DPICHANGED_AFTERPARENT
#define WM_DPICHANGED_AFTERPARENT 0x02E3
#endif
#define FRG_TIMER_ID 1
#define FRG_TIMER_MS 33

struct frg_view {
    frg_client cl;
    HWND hwnd;
    int w, h;                      /* the client size, physical pixels */
    HDC mem;                       /* the DIB's memory DC */
    HBITMAP dib, old;
    uint32_t *bits;
    int bw, bh;                    /* the DIB's size */
    int dirty;                     /* the canvas must be redrawn before the next blit */
    float scale_override;          /* the host's set_scale, 0: the monitor's */
    int tracking;                  /* TrackMouseEvent armed */
    int captured;                  /* buttons down with the capture held (bit per button) */
    int num_used;                  /* the last number key's press was the window's (its WM_CHAR too) */
};

static LONG g_class_refs;
static ATOM g_class;
static wchar_t g_class_name[64];

static HINSTANCE module_handle(void)
{
    HMODULE m = 0;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCWSTR)(void *)module_handle, &m);
    return (HINSTANCE)m;
}

/* GetDpiForWindow: Windows 10 1607 and later (looked up, so the DLL still loads on 8.1) */
typedef UINT(WINAPI *dpi_for_window_fn)(HWND);
static UINT window_dpi(HWND hwnd)
{
    static dpi_for_window_fn fn;
    static int looked;
    UINT dpi = 0;
    if (!looked) {
        HMODULE u = GetModuleHandleW(L"user32.dll");
        fn = u ? (dpi_for_window_fn)(void *)GetProcAddress(u, "GetDpiForWindow") : 0;
        looked = 1;
    }
    if (fn && hwnd) dpi = fn(hwnd);
    if (!dpi) {
        HDC dc = GetDC(hwnd);
        dpi = (UINT)GetDeviceCaps(dc, LOGPIXELSX);
        ReleaseDC(hwnd, dc);
    }
    return dpi ? dpi : 96u;
}

float frg_view_scale(const frg_view *v)
{
    if (v && v->scale_override > 0.f) return v->scale_override;
    return (float)window_dpi(v ? v->hwnd : 0) / 96.f;
}
void frg_view_set_scale(frg_view *v, float scale) { v->scale_override = scale > 0.f ? scale : 0.f; }
void frg_view_default_size(const frg_view *v, int *w, int *h)
{
    float s = frg_view_scale(v);
    *w = (int)(FRUI_W * s + 0.5f);
    *h = (int)(FRUI_H * s + 0.5f);
}

static void dib_free(frg_view *v)
{
    if (v->mem) {
        if (v->old) SelectObject(v->mem, v->old);
        DeleteDC(v->mem);
    }
    if (v->dib) DeleteObject(v->dib);
    v->mem = 0; v->dib = 0; v->old = 0; v->bits = 0; v->bw = v->bh = 0;
}
static int dib_ensure(frg_view *v)
{
    BITMAPINFO bi;
    HDC screen;
    void *bits = 0;
    if (v->bits && v->bw == v->w && v->bh == v->h) return 0;
    dib_free(v);
    if (v->w <= 0 || v->h <= 0) return -1;
    memset(&bi, 0, sizeof bi);
    bi.bmiHeader.biSize = sizeof bi.bmiHeader;
    bi.bmiHeader.biWidth = v->w;
    bi.bmiHeader.biHeight = -v->h;                 /* top-down: row 0 first, as the canvas */
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    screen = GetDC(0);
    v->dib = CreateDIBSection(screen, &bi, DIB_RGB_COLORS, &bits, 0, 0);
    v->mem = CreateCompatibleDC(screen);
    ReleaseDC(0, screen);
    if (!v->dib || !v->mem || !bits) { dib_free(v); return -1; }
    v->old = (HBITMAP)SelectObject(v->mem, v->dib);
    v->bits = (uint32_t *)bits;
    v->bw = v->w; v->bh = v->h;
    v->dirty = 1;
    return 0;
}

static void redraw(frg_view *v)
{
    frui_canvas c;
    if (dib_ensure(v)) return;
    GdiFlush();                                    /* (GDI's pending work on the DIB before the CPU writes it) */
    frui_canvas_init(&c, v->bits, v->bw, v->bh);
    if (v->cl.paint) v->cl.paint(v->cl.ctx, &c, frg_view_scale(v));
    v->dirty = 0;
}

static int mods_now(void)
{
    int m = 0;
    if (GetKeyState(VK_SHIFT) & 0x8000) m |= FRG_SHIFT;
    if (GetKeyState(VK_CONTROL) & 0x8000) m |= FRG_CTRL;
    if (GetKeyState(VK_MENU) & 0x8000) m |= FRG_ALT;
    return m;
}
static void to_logical(const frg_view *v, int px, int py, float *x, float *y)
{
    float s = v->w > 0 ? (float)FRUI_W / (float)v->w : 1.f;    /* frui draws the layout at w / FRUI_W */
    *x = ((float)px + 0.5f) * s;
    *y = ((float)py + 0.5f) * s;
}
static void send(frg_view *v, frg_input *e)
{
    int r;
    if (!v->cl.input) return;
    r = v->cl.input(v->cl.ctx, e);
    if (r & 1) { v->dirty = 1; InvalidateRect(v->hwnd, 0, FALSE); }
}
static void mouse(frg_view *v, int kind, int button, LPARAM lp)
{
    frg_input e;
    memset(&e, 0, sizeof e);
    e.kind = kind; e.button = button; e.mods = mods_now();
    to_logical(v, GET_X_LPARAM(lp), GET_Y_LPARAM(lp), &e.x, &e.y);
    send(v, &e);
}
static int key_of(WPARAM vk)
{
    switch (vk) {
    case VK_LEFT: return FRK_LEFT;
    case VK_RIGHT: return FRK_RIGHT;
    case VK_UP: return FRK_UP;
    case VK_DOWN: return FRK_DOWN;
    case VK_HOME: return FRK_HOME;
    case VK_END: return FRK_END;
    case VK_PRIOR: return FRK_PGUP;
    case VK_NEXT: return FRK_PGDN;
    case VK_TAB: return FRK_TAB;
    case VK_RETURN: return FRK_RETURN;
    case VK_ESCAPE: return FRK_ESCAPE;
    case VK_DELETE: return FRK_DELETE;
    case VK_BACK: return FRK_BACKSPACE;
    default: return 0;
    }
}
/* the keys that type a value (a knob's field) or a name (SAVE's field), from the key itself: the window takes them on
 * WM_KEYDOWN when a field is open (so the host never sees the press) and skips their WM_CHAR; the letters as capitals
 * (the name field's), never with Control or Alt (the host's shortcuts) */
static int number_key(WPARAM vk)
{
    int chord = (GetKeyState(VK_CONTROL) & 0x8000) || (GetKeyState(VK_MENU) & 0x8000);
    if (vk >= 'A' && vk <= 'Z') return chord ? 0 : (int)vk;
    if (vk == VK_SPACE) return chord ? 0 : ' ';
    if (vk >= '0' && vk <= '9') return GetKeyState(VK_SHIFT) & 0x8000 ? 0 : (int)vk;
    if (vk >= VK_NUMPAD0 && vk <= VK_NUMPAD9) return '0' + (int)(vk - VK_NUMPAD0);
    switch (vk) {
    case VK_DECIMAL: case VK_OEM_PERIOD: return '.';
    case VK_OEM_COMMA: return ',';
    case VK_SUBTRACT: case VK_OEM_MINUS: return '-';
    case VK_ADD: return '+';
    default: return 0;
    }
}
static int number_char(WPARAM c)
{
    return (c >= '0' && c <= '9') || c == '.' || c == ',' || c == '-' || c == '+' || (c >= 'A' && c <= 'Z') ||
           (c >= 'a' && c <= 'z') || c == ' ';
}

static LRESULT CALLBACK wndproc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    frg_view *v = (frg_view *)GetWindowLongPtrW(hwnd, GWLP_USERDATA);
    if (msg == WM_NCCREATE) {
        CREATESTRUCTW *cs = (CREATESTRUCTW *)lp;
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, (LONG_PTR)cs->lpCreateParams);
        return DefWindowProcW(hwnd, msg, wp, lp);
    }
    if (!v) return DefWindowProcW(hwnd, msg, wp, lp);
    switch (msg) {
    case WM_ERASEBKGND: return 1;                  /* (the paint covers every pixel) */
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(hwnd, &ps);
        if (v->dirty || !v->bits || v->bw != v->w || v->bh != v->h) redraw(v);
        if (v->bits)
            BitBlt(dc, ps.rcPaint.left, ps.rcPaint.top, ps.rcPaint.right - ps.rcPaint.left,
                   ps.rcPaint.bottom - ps.rcPaint.top, v->mem, ps.rcPaint.left, ps.rcPaint.top, SRCCOPY);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_PRINTCLIENT:                           /* (PrintWindow: host thumbnails, the self-test) */
        if (v->dirty || !v->bits || v->bw != v->w || v->bh != v->h) redraw(v);
        if (v->bits) BitBlt((HDC)wp, 0, 0, v->bw, v->bh, v->mem, 0, 0, SRCCOPY);
        return 0;
    case WM_SIZE:
        v->w = LOWORD(lp); v->h = HIWORD(lp);
        v->dirty = 1;
        InvalidateRect(hwnd, 0, FALSE);
        return 0;
    case WM_TIMER:
        if (wp == FRG_TIMER_ID && v->cl.tick && v->cl.tick(v->cl.ctx)) { v->dirty = 1; InvalidateRect(hwnd, 0, FALSE); }
        return 0;
    case WM_DPICHANGED_AFTERPARENT:                /* the host resizes us; the canvas follows the new size */
        v->dirty = 1;
        InvalidateRect(hwnd, 0, FALSE);
        return 0;
    case WM_MOUSEMOVE:
        if (!v->tracking) {
            TRACKMOUSEEVENT t = { sizeof t, TME_LEAVE, hwnd, 0 };
            v->tracking = TrackMouseEvent(&t) ? 1 : 0;
        }
        mouse(v, FRG_MOVE, 0, lp);
        return 0;
    case WM_MOUSELEAVE: {
        frg_input e;
        v->tracking = 0;
        memset(&e, 0, sizeof e);
        e.kind = FRG_LEAVE;
        send(v, &e);
        return 0;
    }
    case WM_LBUTTONDOWN: case WM_RBUTTONDOWN: case WM_MBUTTONDOWN: {
        int b = msg == WM_LBUTTONDOWN ? FRG_LEFT : msg == WM_RBUTTONDOWN ? FRG_RIGHT : FRG_MIDDLE;
        SetFocus(hwnd);                            /* (the keyboard comes to the view once it is clicked) */
        if (!v->captured) SetCapture(hwnd);
        v->captured |= 1 << b;
        mouse(v, FRG_DOWN, b, lp);
        return 0;
    }
    case WM_LBUTTONDBLCLK: case WM_RBUTTONDBLCLK: case WM_MBUTTONDBLCLK: {
        int b = msg == WM_LBUTTONDBLCLK ? FRG_LEFT : msg == WM_RBUTTONDBLCLK ? FRG_RIGHT : FRG_MIDDLE;
        if (!v->captured) SetCapture(hwnd);
        v->captured |= 1 << b;
        mouse(v, FRG_DOUBLE, b, lp);
        return 0;
    }
    case WM_LBUTTONUP: case WM_RBUTTONUP: case WM_MBUTTONUP: {
        int b = msg == WM_LBUTTONUP ? FRG_LEFT : msg == WM_RBUTTONUP ? FRG_RIGHT : FRG_MIDDLE;
        v->captured &= ~(1 << b);
        if (!v->captured && GetCapture() == hwnd) ReleaseCapture();
        mouse(v, FRG_UP, b, lp);
        return 0;
    }
    case WM_CAPTURECHANGED:                        /* the capture taken away mid-drag (a host dialog, alt-tab) */
        if (v->captured && (HWND)lp != hwnd) {
            frg_input e;
            v->captured = 0;
            memset(&e, 0, sizeof e);
            e.kind = FRG_FOCUS_LOST;
            send(v, &e);
        }
        return 0;
    case WM_MOUSEWHEEL: {
        POINT p = { GET_X_LPARAM(lp), GET_Y_LPARAM(lp) };
        frg_input e;
        ScreenToClient(hwnd, &p);
        memset(&e, 0, sizeof e);
        e.kind = FRG_WHEEL; e.mods = mods_now();
        e.wheel = (float)GET_WHEEL_DELTA_WPARAM(wp) / (float)WHEEL_DELTA;
        to_logical(v, p.x, p.y, &e.x, &e.y);
        send(v, &e);
        return 0;
    }
    case WM_GETDLGCODE: return DLGC_WANTALLKEYS | DLGC_WANTARROWS | DLGC_WANTCHARS | DLGC_WANTTAB;
    case WM_KEYDOWN: case WM_KEYUP: case WM_CHAR: case WM_SYSKEYDOWN: case WM_SYSKEYUP: {
        frg_input e;
        int used = 0;
        memset(&e, 0, sizeof e);
        e.kind = FRG_KEY; e.mods = mods_now();
        if (msg == WM_KEYDOWN) e.key = key_of(wp) ? key_of(wp) : number_key(wp);
        else if (msg == WM_CHAR && wp >= 0x20 && wp < 0x7f && !number_char(wp)) e.key = (int)wp;
        else if (msg == WM_CHAR && number_char(wp) && v->num_used) { v->num_used = 0; return 0; }   /* (its press was ours) */
        if (e.key && v->cl.input) {
            int r = v->cl.input(v->cl.ctx, &e);
            if (r & 1) { v->dirty = 1; InvalidateRect(hwnd, 0, FALSE); }
            used = (r & 2) != 0;
        }
        if (msg == WM_KEYDOWN && !key_of(wp) && number_key(wp)) v->num_used = used;
        if (!used) {                               /* the host's shortcuts (space: play ...) */
            HWND parent = GetParent(hwnd);
            if (parent) return SendMessageW(parent, msg, wp, lp);
            return DefWindowProcW(hwnd, msg, wp, lp);
        }
        return 0;
    }
    case WM_KILLFOCUS: {
        frg_input e;
        memset(&e, 0, sizeof e);
        e.kind = FRG_FOCUS_LOST;
        send(v, &e);
        return 0;
    }
    case WM_NCDESTROY:                             /* (also when the host destroys its window before ours) */
        KillTimer(hwnd, FRG_TIMER_ID);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
        v->hwnd = 0;
        return DefWindowProcW(hwnd, msg, wp, lp);
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static int class_acquire(void)
{
    if (InterlockedIncrement(&g_class_refs) == 1 || !g_class) {
        WNDCLASSEXW wc;
        HINSTANCE inst = module_handle();
        swprintf(g_class_name, sizeof g_class_name / sizeof g_class_name[0], L"FrodhesView%p", (void *)inst);
        memset(&wc, 0, sizeof wc);
        wc.cbSize = sizeof wc;
        wc.style = CS_DBLCLKS;
        wc.lpfnWndProc = wndproc;
        wc.hInstance = inst;
        wc.hCursor = LoadCursorW(0, (LPCWSTR)IDC_ARROW);
        wc.lpszClassName = g_class_name;
        g_class = RegisterClassExW(&wc);
        if (!g_class && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) { InterlockedDecrement(&g_class_refs); return -1; }
    }
    return 0;
}
static void class_release(void)
{
    if (InterlockedDecrement(&g_class_refs) == 0 && g_class) {
        UnregisterClassW(g_class_name, module_handle());
        g_class = 0;
    }
}

frg_view *frg_view_create(const frg_client *cl, int w, int h)
{
    frg_view *v;
    if (class_acquire()) return 0;
    v = calloc(1, sizeof *v);
    if (!v) { class_release(); return 0; }
    v->cl = *cl;
    v->w = w; v->h = h;
    v->dirty = 1;
    return v;
}
void frg_view_destroy(frg_view *v)
{
    if (!v) return;
    if (v->hwnd) DestroyWindow(v->hwnd);           /* (WM_NCDESTROY clears v->hwnd) */
    dib_free(v);
    free(v);
    class_release();
}
int frg_view_set_parent(frg_view *v, void *native_parent)
{
    HWND parent = (HWND)native_parent;
    if (!parent) return -1;
    if (v->hwnd) { SetParent(v->hwnd, parent); return 0; }
    v->hwnd = CreateWindowExW(0, g_class_name, L"Frodhes", WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_TABSTOP,
                              0, 0, v->w, v->h, parent, 0, module_handle(), v);
    if (!v->hwnd) return -1;
    SetTimer(v->hwnd, FRG_TIMER_ID, FRG_TIMER_MS, 0);
    return 0;
}
void frg_view_show(frg_view *v, int visible)
{
    if (v->hwnd) ShowWindow(v->hwnd, visible ? SW_SHOWNA : SW_HIDE);
}
void frg_view_resize(frg_view *v, int w, int h)
{
    v->w = w; v->h = h;
    v->dirty = 1;
    if (v->hwnd) SetWindowPos(v->hwnd, 0, 0, 0, w, h, SWP_NOZORDER | SWP_NOMOVE | SWP_NOACTIVATE);
}
int frg_view_poll_fd(const frg_view *v) { (void)v; return -1; }   /* (the window's messages and SetTimer) */
void frg_view_poll(frg_view *v) { (void)v; }

void frg_view_repaint(frg_view *v)
{
    v->dirty = 1;
    if (v->hwnd) InvalidateRect(v->hwnd, 0, FALSE);
}

const char *const FRG_VIEW_API = "win32";
const uint32_t *frg_view_grab(frg_view *v, int *w, int *h)
{
    if (!v->bits) return 0;
    GdiFlush();
    *w = v->bw; *h = v->bh;
    return v->bits;
}
