/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window as CLAP's gui extension (B-M2b); clap-wrapper shows the same window as the VST3 editor and the
 * Audio Unit's Cocoa view. Embedded only (no floating window). Sizes: logical points on macOS, physical pixels on
 * Windows (CLAP's rule: there the host's set_scale gives the factor). */
#include "gui_clap.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "gui_core.h"
#include "gui_shot.h"
#include "gui_view.h"

struct frp_gui_clap {
    const clap_host_t *host;
    frp_ui_host_t uh;
    frp_gui_t *core;
    frp_view_t *view;
    uint32_t w, h;                   /* logical points */
    double scale;                    /* the host's (Windows) */
    int win32;
};

frp_gui_clap_t *frp_gui_clap_new(const clap_host_t *host, const frp_ui_host_t *uh)
{
    frp_gui_clap_t *c = calloc(1, sizeof *c);
    if (!c) return 0;
    c->host = host;
    c->uh = *uh;
    c->w = FRP_GUI_W; c->h = FRP_GUI_H;
    c->scale = 1.0;
    return c;
}
static void close_window(frp_gui_clap_t *c)
{
    if (c->view) { frp_view_free(c->view); c->view = 0; }
    if (c->core) { frp_gui_free(c->core); c->core = 0; }
}
void frp_gui_clap_free(frp_gui_clap_t *c)
{
    if (!c) return;
    close_window(c);
    free(c);
}
static double px_scale(const frp_gui_clap_t *c) { return c->win32 ? c->scale : 1.0; }
static void saved_size(frp_gui_clap_t *c)        /* the size the state remembers (a backend: frp_ui_iface.h) */
{
    int w;
    if (c->uh.setting_get && c->uh.setting_get(c->uh.ctx, FRP_UI_WIN_W, &w) == 0 && w > 0) {
        uint32_t ww = (uint32_t)w, hh = (uint32_t)w;
        frp_gui_fit_size(&ww, &hh);
        c->w = ww; c->h = hh;
    }
}

/* ---- clap.gui */
#define GUI(plugin) frp_plugin_gui(plugin)
static bool g_api_ok(const char *api, bool floating)
{
    return !floating && api && FRP_VIEW_API && !strcmp(api, FRP_VIEW_API);
}
static bool g_is_api_supported(const clap_plugin_t *plugin, const char *api, bool is_floating)
{
    (void)plugin;
    return g_api_ok(api, is_floating);
}
static bool g_get_preferred_api(const clap_plugin_t *plugin, const char **api, bool *is_floating)
{
    (void)plugin;
    if (!FRP_VIEW_API) return false;
    *api = FRP_VIEW_API;
    *is_floating = false;
    return true;
}
static bool g_create(const clap_plugin_t *plugin, const char *api, bool is_floating)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !g_api_ok(api, is_floating) || c->core) return false;
    c->win32 = !strcmp(api, CLAP_WINDOW_API_WIN32);
    saved_size(c);
    c->core = frp_gui_new(&c->uh);
    if (!c->core) return false;
    c->view = frp_view_new(c->core, c->w, c->h);
    if (!c->view) { close_window(c); return false; }
    frp_view_scale(c->view, c->scale);
    return true;
}
static void g_destroy(const clap_plugin_t *plugin)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (c) close_window(c);
}
static bool g_set_scale(const clap_plugin_t *plugin, double scale)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !(scale > 0.0)) return false;
    if (!c->win32) return false;     /* macOS: the window's backing scale, read by the view itself */
    c->scale = scale;
    if (c->view) frp_view_scale(c->view, scale);
    return true;
}
static bool g_get_size(const clap_plugin_t *plugin, uint32_t *width, uint32_t *height)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c) return false;
    *width = (uint32_t)lround(c->w * px_scale(c));
    *height = (uint32_t)lround(c->h * px_scale(c));
    return true;
}
static bool g_can_resize(const clap_plugin_t *plugin) { (void)plugin; return true; }
static bool g_get_resize_hints(const clap_plugin_t *plugin, clap_gui_resize_hints_t *hints)
{
    (void)plugin;
    hints->can_resize_horizontally = true;
    hints->can_resize_vertically = true;
    hints->preserve_aspect_ratio = true;
    hints->aspect_ratio_width = FRP_GUI_W;
    hints->aspect_ratio_height = FRP_GUI_H;
    return true;
}
static bool g_adjust_size(const clap_plugin_t *plugin, uint32_t *width, uint32_t *height)
{
    frp_gui_clap_t *c = GUI(plugin);
    double k = c ? px_scale(c) : 1.0;
    uint32_t w = (uint32_t)lround(*width / k), h = (uint32_t)lround(*height / k);
    frp_gui_fit_size(&w, &h);
    *width = (uint32_t)lround(w * k);
    *height = (uint32_t)lround(h * k);
    return true;
}
static bool g_set_size(const clap_plugin_t *plugin, uint32_t width, uint32_t height)
{
    frp_gui_clap_t *c = GUI(plugin);
    double k;
    uint32_t w, h;
    if (!c) return false;
    k = px_scale(c);
    w = (uint32_t)lround(width / k); h = (uint32_t)lround(height / k);
    frp_gui_fit_size(&w, &h);
    c->w = w; c->h = h;
    if (c->uh.setting_set) c->uh.setting_set(c->uh.ctx, FRP_UI_WIN_W, (int)w);
    if (c->view) frp_view_size(c->view, w, h);
    return true;
}
static bool g_set_parent(const clap_plugin_t *plugin, const clap_window_t *window)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !c->view || !window) return false;
    return frp_view_attach(c->view, c->win32 ? window->win32 : window->cocoa) == 0;
}
static bool g_set_transient(const clap_plugin_t *plugin, const clap_window_t *window)
{
    (void)plugin; (void)window;
    return false;
}
static void g_suggest_title(const clap_plugin_t *plugin, const char *title) { (void)plugin; (void)title; }
static bool g_show(const clap_plugin_t *plugin)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !c->view) return false;
    frp_view_show(c->view, 1);
    return true;
}
static bool g_hide(const clap_plugin_t *plugin)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !c->view) return false;
    frp_view_show(c->view, 0);
    return true;
}
static const clap_plugin_gui_t EXT_GUI = {
    g_is_api_supported, g_get_preferred_api, g_create, g_destroy, g_set_scale, g_get_size, g_can_resize,
    g_get_resize_hints, g_adjust_size, g_set_size, g_set_parent, g_set_transient, g_suggest_title, g_show, g_hide,
};
const clap_plugin_gui_t *frp_gui_clap_ext(void) { return FRP_VIEW_API ? &EXT_GUI : 0; }

/* ---- the tests' extension (gui_shot.h) */
static bool s_tick(const clap_plugin_t *plugin)
{
    frp_gui_clap_t *c = GUI(plugin);
    int d[4];
    struct timespec ts;
    if (!c || !c->core) return false;
    timespec_get(&ts, TIME_UTC);
    frp_gui_tick(c->core, (double)ts.tv_sec + ts.tv_nsec * 1e-9);
    frp_gui_paint(c->core, d);
    return true;
}
static bool s_grab(const clap_plugin_t *plugin, int view, const uint32_t **px, int *w, int *h, const void **model,
                   uint32_t *model_size)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !c->core) return false;
    *px = view && c->view ? frp_view_grab(c->view, w, h) : frp_gui_pixels(c->core, w, h);
    if (!*px) return false;
    *model = frp_gui_model(c->core);
    *model_size = (uint32_t)sizeof(frui_model);
    return true;
}
static bool s_mouse(const clap_plugin_t *plugin, int ev, float x, float y, int mods, int clicks)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !c->core) return false;
    frp_gui_mouse(c->core, ev, x, y, mods, clicks);
    return true;
}
static bool s_wheel(const clap_plugin_t *plugin, float x, float y, float notches, int mods)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !c->core) return false;
    frp_gui_wheel(c->core, x, y, notches, mods);
    return true;
}
static bool s_key(const clap_plugin_t *plugin, int key, int mods)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !c->core) return false;
    return frp_gui_key(c->core, key, mods) != 0;
}
static bool s_headless(const clap_plugin_t *plugin, int pw, int ph)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || c->core) return false;
    c->core = frp_gui_new(&c->uh);
    return c->core && frp_gui_resize(c->core, pw, ph) == 0;
}
static bool s_find(const clap_plugin_t *plugin, int kind, int id, float r[4])
{
    frp_gui_clap_t *c = GUI(plugin);
    return c && c->core && frp_gui_find(c->core, kind, id, r);
}
static const frp_gui_shot_t EXT_SHOT = { s_tick, s_grab, s_mouse, s_wheel, s_key, s_headless, s_find };
const void *frp_gui_clap_shot_ext(void) { return &EXT_SHOT; }
