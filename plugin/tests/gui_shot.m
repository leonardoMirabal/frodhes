/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window's test host (B-M2b), macOS: loads the built Frodhes.clap, opens its window in a real NSWindow
 * through CLAP's gui extension, drives it as a user does (the plugin's private gui-shot extension injects the same
 * input the NSView passes on), plays notes and automates parameters as a host does, and saves what the window shows.
 *
 *   gui_shot <Frodhes.clap> <out dir> [--headless]
 *
 * Each scene writes <out>/<scene>.png, read back from the window's own surface (the CGImage its layer shows), and
 * <scene>.model (the frui_model it drew); tests/py/test_plugin_gui.py redraws every model with the reference renderer
 * (plugin/ui/tools/frui_render.c) and compares the pixels. Checks print "ok ..." or "FAIL ..."; the exit code counts
 * the failures. --headless: no window server (a window core without a native view, painted by the test). */
#import <AppKit/AppKit.h>
#import <QuartzCore/QuartzCore.h>
#include <dlfcn.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>
#include <clap/clap.h>
#include "../gui_shot.h"
#include "../gui_platform.h"
#include "../frp_ui_iface.h"
#include "../ui/frui.h"

static int fails;
#define CHECK(c, ...) do { if (c) { printf("ok    "); } else { printf("FAIL  "); fails++; } printf(__VA_ARGS__); printf("\n"); } while (0)

/* ---- the host */
static int flush_req, cb_req;
static void h_rescan(const clap_host_t *h, clap_param_rescan_flags f) { (void)h; (void)f; }
static void h_clear(const clap_host_t *h, clap_id id, clap_param_clear_flags f) { (void)h; (void)id; (void)f; }
static void h_flush(const clap_host_t *h) { (void)h; flush_req = 1; }
static const clap_host_params_t HPARAMS = { h_rescan, h_clear, h_flush };
static void h_log(const clap_host_t *h, clap_log_severity s, const char *m) { (void)h; printf("log   [%d] %s\n", s, m); }
static const clap_host_log_t HLOG = { h_log };
static void hg_hints(const clap_host_t *h) { (void)h; }
static bool hg_resize(const clap_host_t *h, uint32_t w, uint32_t hh) { (void)h; (void)w; (void)hh; return false; }
static bool hg_show(const clap_host_t *h) { (void)h; return true; }
static void hg_closed(const clap_host_t *h, bool d) { (void)h; (void)d; }
static const clap_host_gui_t HGUI = { hg_hints, hg_resize, hg_show, hg_show, hg_closed };
static bool ht_yes(const clap_host_t *h) { (void)h; return true; }
static const clap_host_thread_check_t HTHREAD = { ht_yes, ht_yes };
static const void *h_ext(const clap_host_t *h, const char *id)
{
    (void)h;
    if (!strcmp(id, CLAP_EXT_PARAMS)) return &HPARAMS;
    if (!strcmp(id, CLAP_EXT_LOG)) return &HLOG;
    if (!strcmp(id, CLAP_EXT_GUI)) return &HGUI;
    if (!strcmp(id, CLAP_EXT_THREAD_CHECK)) return &HTHREAD;
    return 0;
}
static void h_req(const clap_host_t *h) { (void)h; }
static void h_cb(const clap_host_t *h) { (void)h; cb_req = 1; }
static const clap_host_t HOST = { CLAP_VERSION_INIT, 0, "frodhes gui_shot", "Frodhes", "", "1", h_ext, h_req, h_req, h_cb };

/* the events into the next block, and what the plugin sends back */
static union { clap_event_header_t h; clap_event_note_t n; clap_event_param_value_t p; } in_ev[64];
static uint32_t in_n;
static uint32_t ie_size(const clap_input_events_t *l) { (void)l; return in_n; }
static const clap_event_header_t *ie_get(const clap_input_events_t *l, uint32_t i) { (void)l; return &in_ev[i].h; }
static const clap_input_events_t IN = { 0, ie_size, ie_get };
static int out_begin[128], out_end[128], out_val[128];
static double out_last[128];
static bool oe_push(const clap_output_events_t *l, const clap_event_header_t *e)
{
    (void)l;
    if (e->type == CLAP_EVENT_PARAM_GESTURE_BEGIN || e->type == CLAP_EVENT_PARAM_GESTURE_END) {
        const clap_event_param_gesture_t *g = (const void *)e;
        if (g->param_id < 128) (e->type == CLAP_EVENT_PARAM_GESTURE_BEGIN ? out_begin : out_end)[g->param_id]++;
    } else if (e->type == CLAP_EVENT_PARAM_VALUE) {
        const clap_event_param_value_t *p = (const void *)e;
        if (p->param_id < 128) { out_val[p->param_id]++; out_last[p->param_id] = p->value; }
    }
    return true;
}
static const clap_output_events_t OUT = { 0, oe_push };
static void ev_note(int on, int key)
{
    clap_event_note_t *n = &in_ev[in_n++].n;
    memset(n, 0, sizeof *n);
    n->header.size = sizeof *n; n->header.space_id = CLAP_CORE_EVENT_SPACE_ID;
    n->header.type = on ? CLAP_EVENT_NOTE_ON : CLAP_EVENT_NOTE_OFF;
    n->note_id = -1; n->port_index = 0; n->channel = 0; n->key = (int16_t)key; n->velocity = 0.8;
}
static void ev_param(clap_id id, double v)
{
    clap_event_param_value_t *p = &in_ev[in_n++].p;
    memset(p, 0, sizeof *p);
    p->header.size = sizeof *p; p->header.space_id = CLAP_CORE_EVENT_SPACE_ID; p->header.type = CLAP_EVENT_PARAM_VALUE;
    p->param_id = id; p->note_id = -1; p->port_index = -1; p->channel = -1; p->key = -1; p->value = v;
}

static const clap_plugin_t *pl;
static const clap_plugin_params_t *params;
static const clap_plugin_gui_t *gui;
static const frp_gui_shot_t *shot;
static int headless;
static NSWindow *win;

static void process_block(void)
{
    static float l[256], r[256];
    float *ch[2] = {l, r};
    clap_audio_buffer_t ob = {0};
    clap_process_t pr = {0};
    ob.data32 = ch; ob.channel_count = 2;
    pr.steady_time = -1; pr.frames_count = 256; pr.audio_outputs = &ob; pr.audio_outputs_count = 1;
    pr.in_events = &IN; pr.out_events = &OUT;
    pl->process(pl, &pr);
    in_n = 0;
}
/* the host's loop for t seconds: audio blocks, the window's run loop (its timer paints), callbacks */
static void pump(double t)
{
    NSDate *end = [NSDate dateWithTimeIntervalSinceNow:t];
    while ([end timeIntervalSinceNow] > 0) {
        process_block();
        if (cb_req) { cb_req = 0; pl->on_main_thread(pl); }
        if (headless) shot->tick(pl);
        else [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.005]];
    }
    if (headless) shot->tick(pl);
}
static double pval(clap_id id)
{
    double v = -1;
    params->get_value(pl, id, &v);
    return v;
}

/* ---- PNG (8-bit RGB, zlib), as frui_render writes it */
static uint32_t crc_tab[256];
static uint32_t crc(uint32_t c, const uint8_t *p, size_t n)
{
    if (!crc_tab[1])
        for (uint32_t k = 0; k < 256; k++) { uint32_t v = k; for (int j = 0; j < 8; j++) v = v & 1 ? 0xEDB88320u ^ (v >> 1) : v >> 1; crc_tab[k] = v; }
    for (size_t i = 0; i < n; i++) c = crc_tab[(c ^ p[i]) & 255u] ^ (c >> 8);
    return c;
}
static void chunk(FILE *f, const char *t, const uint8_t *d, uint32_t n)
{
    uint8_t b[4] = {(uint8_t)(n >> 24), (uint8_t)(n >> 16), (uint8_t)(n >> 8), (uint8_t)n};
    uint32_t c;
    fwrite(b, 1, 4, f); fwrite(t, 1, 4, f); if (n) fwrite(d, 1, n, f);
    c = crc(crc(0xFFFFFFFFu, (const uint8_t *)t, 4), d, n) ^ 0xFFFFFFFFu;
    b[0] = (uint8_t)(c >> 24); b[1] = (uint8_t)(c >> 16); b[2] = (uint8_t)(c >> 8); b[3] = (uint8_t)c;
    fwrite(b, 1, 4, f);
}
static int write_png(const char *path, const uint32_t *px, int w, int h)
{
    size_t rl = (size_t)w * 3 + 1, raw_n = rl * (size_t)h;
    uint8_t *raw = malloc(raw_n), hdr[13] = {0};
    uLongf zn = compressBound((uLong)raw_n);
    uint8_t *z = malloc(zn);
    FILE *f;
    for (int y = 0; y < h; y++) {
        uint8_t *r = raw + rl * (size_t)y;
        r[0] = 0;
        for (int x = 0; x < w; x++) {
            uint32_t c = px[(size_t)y * (size_t)w + (size_t)x];
            r[1 + 3 * x] = (uint8_t)(c >> 16); r[2 + 3 * x] = (uint8_t)(c >> 8); r[3 + 3 * x] = (uint8_t)c;
        }
    }
    compress2(z, &zn, raw, (uLong)raw_n, 6);
    if (!(f = fopen(path, "wb"))) { free(raw); free(z); return -1; }
    fwrite("\x89PNG\r\n\x1a\n", 1, 8, f);
    hdr[0] = (uint8_t)(w >> 24); hdr[1] = (uint8_t)(w >> 16); hdr[2] = (uint8_t)(w >> 8); hdr[3] = (uint8_t)w;
    hdr[4] = (uint8_t)(h >> 24); hdr[5] = (uint8_t)(h >> 16); hdr[6] = (uint8_t)(h >> 8); hdr[7] = (uint8_t)h;
    hdr[8] = 8; hdr[9] = 2;
    chunk(f, "IHDR", hdr, 13); chunk(f, "IDAT", z, (uint32_t)zn); chunk(f, "IEND", 0, 0);
    fclose(f);
    free(raw); free(z);
    return 0;
}

static const char *outdir;
static const frui_model *snap(const char *name)
{
    const uint32_t *px;
    const void *model;
    uint32_t msz;
    int w, h;
    char path[1024];
    FILE *f;
    if (!shot->grab(pl, !headless, &px, &w, &h, &model, &msz)) { CHECK(0, "%s: the window's picture", name); return 0; }
    snprintf(path, sizeof path, "%s/%s.png", outdir, name);
    write_png(path, px, w, h);
    snprintf(path, sizeof path, "%s/%s.model", outdir, name);
    if ((f = fopen(path, "wb"))) { fwrite(model, 1, msz, f); fclose(f); }
    printf("shot  %s %dx%d\n", name, w, h);
    return model;
}
static int find(int kind, int id, float *cx, float *cy, float r[4])
{
    float rr[4];
    if (!shot->find(pl, kind, id, rr)) return 0;
    if (r) memcpy(r, rr, sizeof rr);
    *cx = rr[0] + rr[2] * 0.5f; *cy = rr[1] + rr[3] * 0.5f;
    return 1;
}
/* input as a view sends it (gui_platform.h) */
enum { FRP_MOUSE_DOWN, FRP_MOUSE_DRAG, FRP_MOUSE_UP, FRP_MOUSE_RDOWN };
static int sh_send(int kind, int button, int mods, float x, float y, float wheel, int key)
{
    frg_input e;
    memset(&e, 0, sizeof e);
    e.kind = kind; e.button = button; e.mods = mods; e.x = x; e.y = y; e.wheel = wheel; e.key = key;
    return shot->input(pl, &e);
}
static void sh_mouse(int ev, float x, float y, int mods, int clicks)
{
    switch (ev) {
    case FRP_MOUSE_DOWN: sh_send(clicks >= 2 ? FRG_DOUBLE : FRG_DOWN, FRG_LEFT, mods, x, y, 0.f, 0); break;
    case FRP_MOUSE_DRAG: sh_send(FRG_MOVE, 0, mods, x, y, 0.f, 0); break;
    case FRP_MOUSE_UP: sh_send(FRG_UP, FRG_LEFT, mods, x, y, 0.f, 0); break;
    case FRP_MOUSE_RDOWN: sh_send(FRG_DOWN, FRG_RIGHT, mods, x, y, 0.f, 0); sh_send(FRG_UP, FRG_RIGHT, mods, x, y, 0.f, 0); break;
    }
}
static void sh_wheel(float x, float y, float n, int mods) { sh_send(FRG_WHEEL, 0, mods, x, y, n, 0); }
static int sh_key(int key, int mods) { return sh_send(FRG_KEY, 0, mods, 0.f, 0.f, 0.f, key); }
static void click(float x, float y, int clicks)
{
    sh_mouse(FRP_MOUSE_DOWN, x, y, 0, clicks);
    sh_mouse(FRP_MOUSE_UP, x, y, 0, clicks);
}
/* the plugin's state (CLAP state) through memory streams */
static char st_buf[8192];
static size_t st_n, st_r;
static int64_t os_write(const clap_ostream_t *s, const void *b, uint64_t n)
{
    (void)s;
    if (st_n + n >= sizeof st_buf) return -1;
    memcpy(st_buf + st_n, b, n); st_n += n; st_buf[st_n] = 0;
    return (int64_t)n;
}
static int64_t is_read(const clap_istream_t *s, void *b, uint64_t n)
{
    (void)s;
    if (n > st_n - st_r) n = st_n - st_r;
    memcpy(b, st_buf + st_r, n); st_r += n;
    return (int64_t)n;
}
static int state_save(void)
{
    const clap_plugin_state_t *se = pl->get_extension(pl, CLAP_EXT_STATE);
    clap_ostream_t os = {0, os_write};
    st_n = 0; st_buf[0] = 0;
    return se && se->save(pl, &os);
}
static int state_load(void)
{
    const clap_plugin_state_t *se = pl->get_extension(pl, CLAP_EXT_STATE);
    clap_istream_t is = {0, is_read};
    st_r = 0;
    return se && se->load(pl, &is);
}

int main(int argc, char **argv)
{
    char exe[1024];
    void *lib;
    const clap_plugin_entry_t *entry;
    const clap_plugin_factory_t *fac;
    const frui_model *m;
    float x, y, r[4];
    double v0;
    if (argc < 3) { fprintf(stderr, "usage: gui_shot <Frodhes.clap> <out dir> [--headless]\n"); return 2; }
    outdir = argv[2];
    headless = argc > 3 && !strcmp(argv[3], "--headless");
    if (!getenv("FRODHES_PRESET_DIR")) {             /* (SAVE writes a user preset: never into the user's own folder) */
        char pd[1100];
        snprintf(pd, sizeof pd, "%s/user-presets", outdir);
        setenv("FRODHES_PRESET_DIR", pd, 1);
    }
    snprintf(exe, sizeof exe, "%s/Contents/MacOS/Frodhes", argv[1]);
    if (!(lib = dlopen(exe, RTLD_LOCAL | RTLD_NOW))) { fprintf(stderr, "%s\n", dlerror()); return 1; }
    entry = dlsym(lib, "clap_entry");
    /* the gui-shot extension is the tests' only: absent unless FRP_TEST_EXT=1 at load (a release load) */
    unsetenv("FRP_TEST_EXT");
    if (!entry || !entry->init(argv[1])) return 1;
    fac = entry->get_factory(CLAP_PLUGIN_FACTORY_ID);
    pl = fac->create_plugin(fac, &HOST, fac->get_plugin_descriptor(fac, 0)->id);
    if (!pl || !pl->init(pl)) return 1;
    CHECK(!pl->get_extension(pl, FRP_EXT_GUI_SHOT), "no gui-shot extension without FRP_TEST_EXT=1");
    pl->destroy(pl);
    entry->deinit();
    setenv("FRP_TEST_EXT", "1", 1);
    if (!entry->init(argv[1])) return 1;
    fac = entry->get_factory(CLAP_PLUGIN_FACTORY_ID);
    pl = fac->create_plugin(fac, &HOST, fac->get_plugin_descriptor(fac, 0)->id);
    if (!pl || !pl->init(pl)) return 1;
    params = pl->get_extension(pl, CLAP_EXT_PARAMS);
    gui = pl->get_extension(pl, CLAP_EXT_GUI);
    shot = pl->get_extension(pl, FRP_EXT_GUI_SHOT);
    CHECK(gui && shot, "the plugin has the gui extension (and the tests' gui-shot)");
    if (!gui || !shot) return 1;
    CHECK(gui->is_api_supported(pl, CLAP_WINDOW_API_COCOA, false) && !gui->is_api_supported(pl, CLAP_WINDOW_API_COCOA, true) &&
          !gui->is_api_supported(pl, CLAP_WINDOW_API_X11, false), "embedded Cocoa only");
    pl->activate(pl, 44100.0, 1, 4096);
    pl->start_processing(pl);

    @autoreleasepool {
        if (!headless) {
            uint32_t w = 0, h = 0;
            [NSApplication sharedApplication];
            [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
            [NSApp finishLaunching];
            CHECK(gui->create(pl, CLAP_WINDOW_API_COCOA, false), "gui create");
            gui->set_scale(pl, 2.0);
            CHECK(gui->get_size(pl, &w, &h) && w == 960 && h == 600, "default size 960 x 600 (got %u x %u)", w, h);
            win = [[NSWindow alloc] initWithContentRect:NSMakeRect(80, 80, w, h)
                                              styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskResizable
                                                backing:NSBackingStoreBuffered
                                                  defer:NO];
            [win setTitle:@"Frodhes (gui_shot)"];
            clap_window_t cw = {CLAP_WINDOW_API_COCOA, {.cocoa = [win contentView]}};
            CHECK(gui->set_parent(pl, &cw), "gui set_parent");
            CHECK(gui->show(pl), "gui show");
            [win orderFrontRegardless];
        } else CHECK(shot->headless(pl, 1920, 1200), "a window core without a view, 1920 x 1200");
        pump(0.5);
        m = snap("open");
        CHECK(m && m->p[0] == (uint8_t)pval(0), "the window shows the app's TYPE (%d)", m ? m->p[0] : -1);

        /* the host automates and plays: the knobs and the keys follow */
        ev_param(14, 90.0);                          /* DL_DEPTH (DELAY's MIX) */
        ev_param(13, 2.0);                           /* DL_SW */
        ev_param(20, 64.0);                          /* TONE at its middle: the drags below need room both ways (F01's
                                                      * TONE is 112 since FINAL19 v2) */
        ev_note(1, 60); ev_note(1, 64); ev_note(1, 67);
        if (find(FRW_TAB_R, 4, &x, &y, 0)) click(x, y, 1);   /* the DELAY tab */
        pump(0.4);
        m = snap("automation");
        if (!headless) {   /* the window's layer tree as the compositor draws it: the picture in place, upright, the
                            * plate's corners rounded (the host's window shows there); everything else identical */
            {
                NSView *cv = [win contentView];
                CALayer *L = cv.layer;
                double sc = [win backingScaleFactor];
                int W = (int)(cv.bounds.size.width * sc), H = (int)(cv.bounds.size.height * sc);
                uint32_t *buf = calloc((size_t)W * H, 4);
                CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
                CGContextRef cg = CGBitmapContextCreate(buf, W, H, 8, (size_t)W * 4, cs,
                                                        kCGBitmapByteOrder32Little | (CGBitmapInfo)kCGImageAlphaPremultipliedFirst);
                CGContextSetRGBFillColor(cg, 1, 0, 1, 1);      /* magenta: what the window leaves uncovered */
                CGContextFillRect(cg, CGRectMake(0, 0, W, H));
                CGContextScaleCTM(cg, sc, sc);
                [L renderInContext:cg];
                CGContextRelease(cg);
                CGColorSpaceRelease(cs);
                {
                    char path[1024];
                    const uint32_t *px;
                    const void *mm;
                    uint32_t msz;
                    int gw, gh, bad = 0, corner = 0, c = (int)(24 * sc);
                    snprintf(path, sizeof path, "%s/layers-automation.png", outdir);
                    write_png(path, buf, W, H);
                    if (shot->grab(pl, 1, &px, &gw, &gh, &mm, &msz) && gw == W && gh == H) {
                        for (int yy = 0; yy < H; yy++)
                            for (int xx = 0; xx < W; xx++) {
                                int inc = (xx < c || xx >= W - c) && (yy < c || yy >= H - c);
                                uint32_t a = buf[(size_t)yy * W + xx] | 0xFF000000u, b2 = px[(size_t)yy * W + xx] | 0xFF000000u;
                                if (inc) { if (a == 0xFFFF00FFu) corner++; }
                                else if (a != b2) bad++;
                            }
                        CHECK(bad == 0 && corner > 0, "the window's layers show the picture upright and in place (%d pixels differ), "
                              "the corners rounded (%d host pixels)", bad, corner);
                    } else CHECK(0, "the window's layers: size %dx%d", W, H);
                }
                free(buf);
            }
        }
        CHECK(m && m->p[14] == 90 && m->p[13] == 2, "host automation reaches the knobs (MIX %d, TYPE %d)", m ? m->p[14] : -1, m ? m->p[13] : -1);
        CHECK(m && m->notes[60] && m->notes[64] && m->notes[67], "the host's notes light the keys");
        CHECK(m && m->tab_r == 4, "a click on the DELAY tab opens it");
        CHECK(m && m->out_db[0] > -50.f, "the output meter moves (%.1f dB)", m ? m->out_db[0] : 0.f);

        /* a knob dragged up 40 px: one gesture, values, the app's value */
        v0 = pval(20);                               /* TONE */
        if (find(FRW_KNOB, 20, &x, &y, 0)) {
            sh_mouse(FRP_MOUSE_DOWN, x, y, 0, 1);
            for (int k = 1; k <= 8; k++) { sh_mouse(FRP_MOUSE_DRAG, x, y - 5.f * k, 0, 1); pump(0.02); }
            sh_mouse(FRP_MOUSE_UP, x, y - 40.f, 0, 1);
        }
        pump(0.3);
        CHECK(out_begin[20] == 1 && out_end[20] == 1 && out_val[20] >= 1, "the drag is one gesture to the host (begin %d, values %d, end %d)",
              out_begin[20], out_val[20], out_end[20]);
        CHECK(fabs(pval(20) - (v0 + 40.0 * 127.0 / 200.0)) <= 1.0, "TONE %.0f -> %.0f (40 px of 200 for the range)", v0, pval(20));
        m = snap("drag");
        CHECK(m && m->p[20] == (uint8_t)pval(20) && m->focus == FRUI_WID(FRW_KNOB, 20), "the knob shows it, with the focus ring");
        /* shift: ten times finer */
        v0 = pval(20);
        sh_mouse(FRP_MOUSE_DOWN, x, y, FRG_SHIFT, 1);
        sh_mouse(FRP_MOUSE_DRAG, x, y + 40.f, FRG_SHIFT, 1);
        sh_mouse(FRP_MOUSE_UP, x, y + 40.f, FRG_SHIFT, 1);
        pump(0.2);
        CHECK(fabs(pval(20) - (v0 - 40.0 * 127.0 / 2000.0)) <= 1.0, "shift-drag is fine (%.0f -> %.0f)", v0, pval(20));
        /* the wheel, the arrow keys */
        v0 = pval(20);
        sh_wheel(x, y, 3.f, 0);
        pump(0.6);
        CHECK(pval(20) == v0 + 6.0, "three wheel notches: +6 (%.0f -> %.0f)", v0, pval(20));
        v0 = pval(20);
        sh_key(FRK_DOWN, 0);
        pump(0.2);
        CHECK(pval(20) == v0 - 1.0, "the down arrow on the focused knob: -1");
        /* double click: the default */
        sh_mouse(FRP_MOUSE_DOWN, x, y, 0, 2);
        sh_mouse(FRP_MOUSE_UP, x, y, 0, 2);
        pump(0.2);
        {
            clap_param_info_t inf;
            params->get_info(pl, 20, &inf);
            CHECK(pval(20) == inf.default_value, "double click: TONE back to its default (%.0f, default %.0f)", pval(20), inf.default_value);
        }

        /* typing a value: a double-click on TONE's value text (-5.0 .. +5.0), "2.5", RETURN (one gesture); ESC leaves it */
        if (find(FRW_KNOB, 20, &x, &y, r)) {
            float vx = r[0] + r[2] * 0.5f, vy = r[1] + r[3] - 10.f;   /* (the value's baseline: 4 above the bottom) */
            int b0 = out_begin[20], e0 = out_end[20];
            char txt[64] = "";
            double shown;
            sh_mouse(FRP_MOUSE_DOWN, vx, vy, 0, 1); sh_mouse(FRP_MOUSE_UP, vx, vy, 0, 1);
            sh_mouse(FRP_MOUSE_DOWN, vx, vy, 0, 2); sh_mouse(FRP_MOUSE_UP, vx, vy, 0, 2);
            sh_key('2', 0); sh_key('.', 0); sh_key('5', 0);
            pump(0.2);
            m = snap("value-entry");
            CHECK(m && m->editing == 21 && !strcmp(m->edit_text, "2.5"), "a double-click on the value opens a field; typed \"%s\"",
                  m ? m->edit_text : "");
            b0 = out_begin[20]; e0 = out_end[20];
            sh_key(FRK_RETURN, 0);
            pump(0.3);
            params->value_to_text(pl, 20, pval(20), txt, sizeof txt);
            shown = atof(txt);
            CHECK(fabs(shown - 2.5) <= 0.05 && out_begin[20] == b0 + 1 && out_end[20] == e0 + 1,
                  "RETURN: TONE shows %s (typed 2.5), one gesture to the host", txt);
            v0 = pval(20);
            sh_mouse(FRP_MOUSE_DOWN, vx, vy, 0, 2); sh_mouse(FRP_MOUSE_UP, vx, vy, 0, 2);
            sh_key('9', 0);
            sh_key(FRK_ESCAPE, 0);
            pump(0.2);
            m = snap("value-escape");
            CHECK(m && !m->editing && pval(20) == v0, "ESC closes the field, TONE unchanged (%.0f)", pval(20));
        }

        /* a menu: open, pick */
        if (find(FRW_MENU, 16, &x, &y, 0)) click(x, y, 1);   /* DELAY's SYNC */
        pump(0.2);
        m = snap("menu");
        CHECK(m && m->menu == FRUI_WID(FRW_MENU, 16), "the SYNC menu opens");
        if (find(FRW_ITEM, 5, &x, &y, 0)) click(x, y, 1);
        pump(0.3);
        CHECK(pval(16) == 5.0, "an item sets the value (SYNC %.0f)", pval(16));

        /* the keyboard: a key plays (the app hears it: the meter, the key lit) */
        if (find(FRW_KEYS, 0, &x, &y, r)) {
            float kx = r[0] + r[2] / 36.f * 14.5f, ky = r[1] + r[3] * 0.85f;   /* C3 (MIDI 48), a white key */
            sh_mouse(FRP_MOUSE_DOWN, kx, ky, 0, 1);
            pump(0.3);
            m = snap("keyboard");
            CHECK(m && m->notes[48], "a click on C3 plays MIDI note 48");
            sh_mouse(FRP_MOUSE_UP, kx, ky, 0, 1);
            pump(0.2);
        }
        /* the knob's menu (right click) and SETTINGS (the gear) */
        if (find(FRW_KNOB, 20, &x, &y, 0)) sh_mouse(FRP_MOUSE_RDOWN, x, y, 0, 1);
        pump(0.2);
        m = snap("knob-menu");
        CHECK(m && m->ctx_knob == 20, "right click: the knob's menu (MIDI LEARN, FORGET, DEFAULT)");
        sh_key(FRK_ESCAPE, 0);
        if (find(FRW_BUTTON, FRB_GEAR, &x, &y, 0)) click(x, y, 1);
        pump(0.2);
        m = snap("settings");
        CHECK(m && m->settings, "the gear opens SETTINGS");
        sh_key(FRK_ESCAPE, 0);
        pump(0.1);

        /* the window's boxes are the plugin core's (frp_ui_iface.h, read back through its extension): the preset,
         * NEXT / UNDO, MIDI LEARN, FX, SUSTAIN, a setting */
        {
            const frp_ui_vtable *vt = pl->get_extension(pl, FRP_UI_EXT_ID);
            frp_core *pc = vt ? vt->core_of(pl) : 0;
            frp_preset_info pi, p2;
            frp_settings st;
            int ed = 0, ch0;
            CHECK(pc != 0, "the core's UI extension");
            if (pc) {
                vt->preset_current(pc, &pi, &ed);
                m = snap("preset");
                CHECK(m && !strcmp(m->preset_tag, pi.tag) && !strcmp(m->preset_name, pi.name),
                      "the preset box shows the core's preset (%s %s / %s %s)", m ? m->preset_tag : "", m ? m->preset_name : "", pi.tag, pi.name);
                if (find(FRW_BUTTON, FRB_NEXT, &x, &y, 0)) click(x, y, 1);
                pump(0.3);
                vt->preset_current(pc, &p2, &ed);
                m = snap("preset-next");
                CHECK(strcmp(pi.tag, p2.tag) != 0 && m && !strcmp(m->preset_tag, p2.tag),
                      "NEXT loads the next preset (%s -> %s, shown %s)", pi.tag, p2.tag, m ? m->preset_tag : "");
                if (find(FRW_BUTTON, FRB_UNDO, &x, &y, 0)) click(x, y, 1);
                pump(0.3);
                vt->preset_current(pc, &p2, &ed);
                CHECK(!strcmp(pi.tag, p2.tag), "UNDO goes back (%s)", p2.tag);
                if (find(FRW_KNOB, 20, &x, &y, 0)) sh_mouse(FRP_MOUSE_RDOWN, x, y, 0, 1);
                pump(0.2);
                if (find(FRW_CTX, FRC_LEARN, &x, &y, 0)) click(x, y, 1);
                pump(0.2);
                CHECK(vt->learning(pc) == 20, "MIDI LEARN arms the core (%d)", vt->learning(pc));
                m = snap("learning");
                CHECK(m && m->learning == 20, "the window shows the knob learning");
                sh_key(FRK_ESCAPE, 0);
                click(5.f, 300.f, 1);                    /* (any click cancels a learn) */
                pump(0.2);
                CHECK(vt->learning(pc) < 0, "a click cancels the learn (%d)", vt->learning(pc));
                if (find(FRW_BUTTON, FRB_FX, &x, &y, 0)) click(x, y, 1);
                pump(0.2);
                CHECK(vt->fx_bypass(pc) == 1, "FX bypasses every effect in the core");
                if (find(FRW_BUTTON, FRB_FX, &x, &y, 0)) click(x, y, 1);
                pump(0.2);
                CHECK(vt->fx_bypass(pc) == 0, "FX again: the effects back");
                if (find(FRW_BUTTON, FRB_SUSTAIN, &x, &y, 0)) click(x, y, 1);
                pump(0.2);
                CHECK(vt->sustain(pc) == 1, "SUSTAIN latches the core's sustain");
                if (find(FRW_BUTTON, FRB_SUSTAIN, &x, &y, 0)) click(x, y, 1);
                pump(0.2);
                CHECK(vt->sustain(pc) == 0, "SUSTAIN again lets it go");
                vt->settings_get(pc, &st);
                ch0 = st.midi_ch;
                if (find(FRW_BUTTON, FRB_GEAR, &x, &y, 0)) click(x, y, 1);
                pump(0.2);
                if (find(FRW_MENU, FRS_CHANNEL, &x, &y, 0)) click(x, y, 1);
                pump(0.2);
                if (find(FRW_ITEM, 3, &x, &y, 0)) click(x, y, 1);
                pump(0.3);
                vt->settings_get(pc, &st);
                CHECK(st.midi_ch == 3, "SETTINGS: MIDI CHANNEL 3 reaches the core (%d)", st.midi_ch);
                st.midi_ch = ch0;
                vt->settings_set(pc, &st);
                pump(0.2);
                m = snap("settings-core");
                CHECK(m && m->midi_ch == ch0, "a core change shows in the window (CHANNEL %d)", m ? m->midi_ch : -1);
                sh_key(FRK_ESCAPE, 0);
                pump(0.1);

                /* PRESETS: a click on the preset's name opens every preset (the core's list), grouped by instrument; a
                 * click loads one (F13 VIBES) and the sheet stays open; ESC closes it */
                {
                    int n0 = vt->preset_count(pc), ok = 1, k;
                    if (find(FRW_PRESET, 0, &x, &y, 0)) click(x, y, 1);
                    pump(0.3);
                    m = snap("presets");
                    CHECK(m && m->presets && m->pl_n == n0, "the preset's name opens PRESETS (%d entries, the core has %d)",
                          m ? m->pl_n : -1, n0);
                    for (k = 0; m && k < m->pl_n && k < 57; k++) {
                        frp_preset_info q;
                        vt->preset_info(pc, k, &q);
                        ok &= !strcmp(m->pl[k].name, q.name) && m->pl[k].type == vt->preset_type(pc, k) && !m->pl[k].user;
                    }
                    CHECK(ok, "every factory entry: the core's name and instrument");
                    if (find(FRW_PITEM, 12, &x, &y, 0)) click(x, y, 1);
                    pump(0.4);
                    vt->preset_current(pc, &p2, &ed);
                    m = snap("presets-pick");
                    CHECK(!strcmp(p2.tag, "F13") && m && m->presets && m->pl_cur == 12 && !strcmp(m->preset_tag, "F13"),
                          "a click loads F13 (%s), the sheet stays open, the entry marked", p2.tag);
                    sh_key(FRK_ESCAPE, 0);
                    pump(0.2);
                    m = snap("presets-closed");
                    CHECK(m && !m->presets, "ESC closes PRESETS");
                    /* SAVE: the name field (the preset's name selected), typed, RETURN: a user preset of that name */
                    if (find(FRW_BUTTON, FRB_SAVE, &x, &y, 0)) click(x, y, 1);
                    pump(0.2);
                    m = snap("save-field");
                    CHECK(m && m->saving && m->save_sel && !strcmp(m->save_text, "VIBES"), "SAVE: the name field (\"%s\")",
                          m ? m->save_text : "");
                    for (const char *t = "night vibes"; *t; t++) sh_key(*t, 0);
                    pump(0.2);
                    m = snap("save-typed");
                    CHECK(m && !strcmp(m->save_text, "NIGHT VIBES"), "typed: \"%s\"", m ? m->save_text : "");
                    sh_key(FRK_RETURN, 0);
                    pump(0.4);
                    vt->preset_current(pc, &p2, &ed);
                    CHECK(vt->preset_count(pc) == n0 + 1 && !strcmp(p2.name, "NIGHT VIBES") && p2.user && vt->preset_type(pc, n0) == 4,
                          "RETURN saved the user preset %s %s (instrument %d)", p2.tag, p2.name, vt->preset_type(pc, n0));
                    m = snap("saved");
                    CHECK(m && !m->saving && !strcmp(m->preset_name, "NIGHT VIBES"), "the box shows it");
                    if (find(FRW_PRESET, 0, &x, &y, 0)) click(x, y, 1);
                    pump(0.3);
                    m = snap("presets-user");
                    CHECK(m && m->pl_n == n0 + 1 && m->pl[n0].user && m->pl_cur == n0, "PRESETS lists it with the user's, marked");
                    sh_key(FRK_ESCAPE, 0);
                    CHECK(vt->preset_delete(pc, n0) == 0, "(the test's user preset removed)");
                    pump(0.2);
                    /* EFFECTS: BYPASS draws the effect knobs inactive */
                    if (find(FRW_BUTTON, FRB_FX, &x, &y, 0)) click(x, y, 1);
                    pump(0.3);
                    m = snap("fx-bypass");
                    CHECK(m && m->fx_bypass, "BYPASS shown (the effect knobs drawn inactive)");
                    if (find(FRW_BUTTON, FRB_FX, &x, &y, 0)) click(x, y, 1);
                    pump(0.2);
                }
                /* the window's size in the project: the state carries "win" once it was set, and brings it back */
                vt->win_w_set(pc, 1200);
                CHECK(state_save() && strstr(st_buf, ",\"win\":\"1200\"}") != 0, "the state carries the window's size (1200)");
                vt->win_w_set(pc, 0);
                CHECK(state_load() && vt->win_w(pc) == 1200, "a state brings the size back (%d)", vt->win_w(pc));
                {
                    char *w = strstr(st_buf, ",\"win\":");
                    if (w) strcpy(w, "}");
                    vt->win_w_set(pc, 1440);
                    CHECK(state_load() && vt->win_w(pc) == 1440, "a state without it (older) keeps the size (%d)", vt->win_w(pc));
                }
                vt->win_w_set(pc, 0);
            }
        }

        /* resize (the host drags the window): 75 % and 150 % */
        if (!headless) {
            uint32_t w = 700, h = 500;
            gui->adjust_size(pl, &w, &h);
            CHECK(w == 720 && h == 450, "adjust_size keeps 16:10 within the minimum (700 x 500 -> %u x %u)", w, h);
            w = 1440; h = 900;
            CHECK(gui->set_size(pl, w, h), "set_size 1440 x 900");
            [win setContentSize:NSMakeSize(w, h)];
            pump(0.4);
            m = snap("size-1440x900");
            gui->set_size(pl, 960, 600);
            [win setContentSize:NSMakeSize(960, 600)];
            pump(0.2);
            /* close and open again, twice */
            for (int k = 0; k < 2; k++) {
                gui->destroy(pl);
                CHECK(gui->create(pl, CLAP_WINDOW_API_COCOA, false) && gui->set_parent(pl, &(clap_window_t){CLAP_WINDOW_API_COCOA, {.cocoa = [win contentView]}}) &&
                      gui->show(pl), "close and open again (%d)", k + 1);
                pump(0.2);
            }
            gui->destroy(pl);
            {   /* a project saved with the window at 1200 x 750 opens it at that size */
                const frp_ui_vtable *vt = pl->get_extension(pl, FRP_UI_EXT_ID);
                uint32_t gw = 0, gh = 0;
                vt->win_w_set(vt->core_of(pl), 1200);
                state_save();
                vt->win_w_set(vt->core_of(pl), 0);
                state_load();
                CHECK(gui->create(pl, CLAP_WINDOW_API_COCOA, false) && gui->get_size(pl, &gw, &gh) && gw == 1200 && gh == 750,
                      "a loaded state's size opens the window at it (%u x %u)", gw, gh);
                gui->destroy(pl);
                vt->win_w_set(vt->core_of(pl), 0);
            }
            [win close];
        }
    }
    pl->stop_processing(pl);
    pl->deactivate(pl);
    pl->destroy(pl);
    entry->deinit();
    printf("%s: %d failed\n", fails ? "FAIL" : "ok", fails);
    return fails;
}
