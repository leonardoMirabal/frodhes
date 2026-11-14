/* SPDX-License-Identifier: GPL-3.0-only */
/* CHECK FOR UPDATES: the plugin's window tells the user when a newer Frodhes release is out (docs/PLUGIN.md).
 *
 * What it contacts: one HTTPS GET of FRP_UPDATE_URL (GitHub's REST API, the repository's latest release: published,
 * not a draft, not a pre-release), its "tag_name" (vX.Y.Z) compared with the plugin's own version. Nothing is sent
 * but the request itself (no identifier, no telemetry); nothing is downloaded or installed. When: at most once per
 * FRP_UPDATE_PERIOD (24 h, per user account: the time is kept in the settings file), only when the plugin's window
 * opens, on a thread of its own (never the audio thread; the window and the host never wait for it), 5 s at most;
 * any failure is silent. A developer build (version 0.0.0-dev) never checks and never shows a notice. The check can
 * be turned off in SETTINGS (UPDATES OFF), which is kept in the settings file too, or for the whole process by
 * FRODHES_UPDATE_CHECK=0 in the environment (the test hosts set it).
 *
 * Per OS, no library beyond the system: macOS NSURLSession (Foundation), Windows WinHTTP (winhttp.dll), Linux
 * libcurl (libcurl.so.4) loaded with dlopen when the system has it, else no check. TLS is verified by each.
 *
 * Two layers: the pure functions (versions, the response, the 24 h rule, the settings file's text: frp_update.c,
 * tested on any OS by plugin/tests/test_update.c) and the runtime (frp_update_rt.c: the cache, the thread, the file,
 * the browser) over one fetch call per OS (frp_update_net.c, frp_update_mac.m). */
#ifndef FRP_UPDATE_H
#define FRP_UPDATE_H
#include <stddef.h>

#define FRP_UPDATE_URL "https://api.github.com/repos/leonardoMirabal/frodhes/releases/latest"
#define FRP_UPDATE_PAGE "https://github.com/leonardoMirabal/frodhes/releases/latest"
#define FRP_UPDATE_PERIOD 86400          /* seconds between two checks */
#define FRP_UPDATE_TIMEOUT_MS 5000       /* the whole request */
#define FRP_UPDATE_MAX_BODY (512 * 1024) /* the response is read up to this (the release's JSON: a few KB) */

/* ---- the pure part */
typedef struct {
    int check;                           /* UPDATES ON (1, the default) / OFF (0) */
    long long checked;                   /* the last check's time (Unix seconds; 0 never), done or failed */
    char latest[16];                     /* the latest release found ("0.9.13"; "" none yet) */
} frp_update_state;

/* "X.Y.Z" or "vX.Y.Z" (each 0..99999, nothing before or after): 0 and v[3]; else -1 */
int frp_ver_parse(const char *s, int v[3]);
int frp_ver_cmp(const int a[3], const int b[3]);    /* <0, 0, >0 */
/* own is a release's version: X.Y.Z, not 0.0.0 (a developer build: "0.0.0-dev", "dev", "": no) */
int frp_ver_is_release(const char *own);
/* 1 when latest is a newer release than own (own a release): out = latest as "X.Y.Z"; else 0 and out "" */
int frp_update_newer(const char *own, const char *latest, char out[16]);
/* a check is due: never checked, 24 h since the last, or the clock is a day behind the last check */
int frp_update_due(long long now, long long checked);
/* the response's "tag_name" ("v0.9.13") as "0.9.13": 0 ok, -1 none or not a version */
int frp_update_tag(const char *body, size_t n, char out[16]);
/* what a window opening does: 1 check now (on), 0 nothing */
int frp_update_plan(const frp_update_state *s, const char *own, long long now);
/* the settings file (settings.json, a flat JSON object): its keys "update_check" (true / false), "update_checked"
 * (seconds), "update_latest" ("X.Y.Z"); the other keys are the plugin's other settings, kept as they are */
void frp_update_state_default(frp_update_state *s);
int frp_update_state_read(const char *text, frp_update_state *s);    /* 0 ok (missing keys: defaults); -1 not a flat
                                                                       * object (s: the defaults) */
/* the file's new text: old (NULL or "": none) with the three keys set, every other key kept in its order; 0 ok, -1
 * old is not a flat object (it is then never overwritten) or out is too small */
int frp_update_state_write(const char *old, const frp_update_state *s, char *out, size_t n);

/* ---- the runtime (main thread unless said) */
void frp_update_init(const char *own_version);      /* the entry's init: the plugin's own version text */
void frp_update_shutdown(void);                     /* the entry's deinit: a check under way is cancelled, waited for */
void frp_update_poke(void);                         /* the window opened: check when due (on a thread of its own) */
int frp_update_available(char ver[16]);             /* 1: a newer release is known (and UPDATES ON): ver "X.Y.Z" */
int frp_update_enabled(void);
void frp_update_set_enabled(int on);                /* saved in the settings file; ON checks at once when due */
int frp_update_open_page(void);                     /* FRP_UPDATE_PAGE in the browser: 0 ok */
int frp_update_busy(void);                          /* a check is under way (the tests) */

/* ---- per OS: one HTTPS GET with TLS verified, timeout_ms for the whole request, nothing but the request (no
 * cookie, no cache); the body into buf (cut at cap - 1, NUL-terminated), *len its length. The HTTP status, -1 when
 * there is none (no network, no libcurl, cancelled, timed out). Called on the check's own thread */
int frp_update_fetch(const char *url, char *buf, size_t cap, size_t *len, int timeout_ms);
void frp_update_fetch_cancel(void);                 /* (any thread) a fetch under way ends at once with -1 */
#endif
