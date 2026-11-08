/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window as CLAP's gui extension (B-M2b); clap-wrapper shows the same window as the VST3 editor and the
 * Audio Unit's Cocoa view. Embedded only (no floating window). Sizes: logical points on macOS, physical pixels on
 * Windows (CLAP's rule: there the host's set_scale gives the factor). */
#include "gui_clap.h"
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "gui_core.h"
#include "gui_platform.h"
#include "gui_shot.h"

struct frp_gui_clap {
    const clap_host_t *host;
    frp_ui_host_t uh;
    frp_gui_t *core;
    frg_view *view;
    uint32_t w, h;                   /* logical points */
    double scale;                    /* the host's (Windows, X11) */
    int px_api;                      /* sizes in physical pixels (win32, x11) */
    /* X11: the host's event loop */
    const clap_host_posix_fd_support_t *hfd;
    const clap_host_timer_support_t *htm;
    int fd;
    clap_id timer;
};

frp_gui_clap_t *frp_gui_clap_new(const clap_host_t *host, const frp_ui_host_t *uh)
{
    frp_gui_clap_t *c = calloc(1, sizeof *c);
    if (!c) return 0;
    c->host = host;
    c->uh = *uh;
    c->w = FRP_GUI_W; c->h = FRP_GUI_H;
    c->scale = 0.0;
    c->fd = -1;
    c->timer = CLAP_INVALID_ID;
    return c;
}
static void loop_release(frp_gui_clap_t *c)
{
    if (c->fd >= 0 && c->hfd) c->hfd->unregister_fd(c->host, c->fd);
    if (c->timer != CLAP_INVALID_ID && c->htm) c->htm->unregister_timer(c->host, c->timer);
    c->fd = -1;
    c->timer = CLAP_INVALID_ID;
}
static void close_window(frp_gui_clap_t *c)
{
    loop_release(c);
    if (c->view) { frg_view_destroy(c->view); c->view = 0; }
    if (c->core) { frp_gui_free(c->core); c->core = 0; }
}
void frp_gui_clap_free(frp_gui_clap_t *c)
{
    if (!c) return;
    close_window(c);
    free(c);
}
static double px_scale(const frp_gui_clap_t *c)
{
    if (!c->px_api) return 1.0;
    if (c->scale > 0.0) return c->scale;
    return c->view ? (double)frg_view_scale(c->view) : 1.0;
}
static void saved_size(frp_gui_clap_t *c)        /* the size the state remembers (a backend: frp_ui_iface.h) */
{
    int w;
    if (c->uh.setting_get && c->uh.setting_get(c->uh.ctx, FRP_UI_WIN_W, &w) == 0 && w > 0) {
        uint32_t ww = (uint32_t)w, hh = (uint32_t)w;
        frp_gui_fit_size(&ww, &hh);
        c->w = ww; c->h = hh;
    }
}
static int phys(const frp_gui_clap_t *c, uint32_t v) { return (int)lround(v * px_scale(c)); }

/* ---- clap.gui */
#define GUI(plugin) frp_plugin_gui(plugin)
static bool g_api_ok(const char *api, bool floating) { return !floating && api && !strcmp(api, FRG_VIEW_API); }
static bool g_is_api_supported(const clap_plugin_t *plugin, const char *api, bool is_floating)
{
    (void)plugin;
    return g_api_ok(api, is_floating);
}
static bool g_get_preferred_api(const clap_plugin_t *plugin, const char **api, bool *is_floating)
{
    (void)plugin;
    *api = FRG_VIEW_API;
    *is_floating = false;
    return true;
}
static bool g_create(const clap_plugin_t *plugin, const char *api, bool is_floating)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !g_api_ok(api, is_floating) || c->core) return false;
    c->px_api = strcmp(api, CLAP_WINDOW_API_COCOA) != 0;
    saved_size(c);
    c->core = frp_gui_new(&c->uh);
    if (!c->core) return false;
    c->view = frg_view_create(frp_gui_client(c->core), 1, 1);
    if (!c->view) { close_window(c); return false; }
    if (c->scale > 0.0) frg_view_set_scale(c->view, (float)c->scale);
    frg_view_resize(c->view, phys(c, c->w), phys(c, c->h));
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
    if (!c->px_api) return false;    /* macOS: the window's backing scale, read by the view itself */
    c->scale = scale;
    if (c->view) frg_view_set_scale(c->view, (float)scale);
    return true;
}
static bool g_get_size(const clap_plugin_t *plugin, uint32_t *width, uint32_t *height)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c) return false;
    *width = (uint32_t)phys(c, c->w);
    *height = (uint32_t)phys(c, c->h);
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
    if (c->view) frg_view_resize(c->view, (int)width, (int)height);   /* (the host's size: the view fits 16:10 in it) */
    return true;
}
static bool g_set_parent(const clap_plugin_t *plugin, const clap_window_t *window)
{
    frp_gui_clap_t *c = GUI(plugin);
    void *parent;
    if (!c || !c->view || !window) return false;
    parent = !strcmp(FRG_VIEW_API, CLAP_WINDOW_API_X11) ? (void *)(uintptr_t)window->x11
           : !strcmp(FRG_VIEW_API, CLAP_WINDOW_API_WIN32) ? window->win32 : window->cocoa;
    if (frg_view_set_parent(c->view, parent)) return false;
    if (frg_view_poll_fd(c->view) >= 0 && c->fd < 0) {   /* X11: the host's loop watches the display */
        c->hfd = c->host->get_extension(c->host, CLAP_EXT_POSIX_FD_SUPPORT);
        c->htm = c->host->get_extension(c->host, CLAP_EXT_TIMER_SUPPORT);
        if (c->hfd && c->hfd->register_fd(c->host, frg_view_poll_fd(c->view), CLAP_POSIX_FD_READ))
            c->fd = frg_view_poll_fd(c->view);
        if (c->htm && !c->htm->register_timer(c->host, 33, &c->timer)) c->timer = CLAP_INVALID_ID;
    }
    return true;
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
    frg_view_show(c->view, 1);
    return true;
}
static bool g_hide(const clap_plugin_t *plugin)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !c->view) return false;
    frg_view_show(c->view, 0);
    return true;
}
static const clap_plugin_gui_t EXT_GUI = {
    g_is_api_supported, g_get_preferred_api, g_create, g_destroy, g_set_scale, g_get_size, g_can_resize,
    g_get_resize_hints, g_adjust_size, g_set_size, g_set_parent, g_set_transient, g_suggest_title, g_show, g_hide,
};
const clap_plugin_gui_t *frp_gui_clap_ext(void) { return &EXT_GUI; }

/* ---- X11: the host's loop */
static void on_fd(const clap_plugin_t *plugin, int fd, clap_posix_fd_flags_t flags)
{
    frp_gui_clap_t *c = GUI(plugin);
    (void)fd; (void)flags;
    if (c && c->view) frg_view_poll(c->view);
}
static void on_timer(const clap_plugin_t *plugin, clap_id id)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (c && c->view && id == c->timer) frg_view_poll(c->view);
}
static const clap_plugin_posix_fd_support_t EXT_FD = { on_fd };
static const clap_plugin_timer_support_t EXT_TIMER = { on_timer };
const clap_plugin_posix_fd_support_t *frp_gui_clap_fd_ext(void)
{
    return strcmp(FRG_VIEW_API, CLAP_WINDOW_API_X11) ? 0 : &EXT_FD;
}
const clap_plugin_timer_support_t *frp_gui_clap_timer_ext(void)
{
    return strcmp(FRG_VIEW_API, CLAP_WINDOW_API_X11) ? 0 : &EXT_TIMER;
}

/* ---- the tests' extension (gui_shot.h) */
static bool s_tick(const clap_plugin_t *plugin)
{
    frp_gui_clap_t *c = GUI(plugin);
    int d[4];
    if (!c || !c->core) return false;
    if (c->view) {                   /* (the view paints the core: the view's copy needs the core's dirty area) */
        frg_view_repaint(c->view);
        frg_view_poll(c->view);
        return true;
    }
    frp_gui_client(c->core)->tick(c->core);
    frp_gui_paint(c->core, d);
    return true;
}
static bool s_grab(const clap_plugin_t *plugin, int view, const uint32_t **px, int *w, int *h, const void **model,
                   uint32_t *model_size)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !c->core) return false;
    *px = view && c->view ? frg_view_grab(c->view, w, h) : frp_gui_pixels(c->core, w, h);
    if (!*px) return false;
    *model = frp_gui_model(c->core);
    *model_size = (uint32_t)sizeof(frui_model);
    return true;
}
static int s_input(const clap_plugin_t *plugin, const frg_input *e)
{
    frp_gui_clap_t *c = GUI(plugin);
    if (!c || !c->core) return -1;
    return frp_gui_input(c->core, e);
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
static const frp_gui_shot_t EXT_SHOT = { s_tick, s_grab, s_input, s_headless, s_find };
const void *frp_gui_clap_shot_ext(void) { return &EXT_SHOT; }
