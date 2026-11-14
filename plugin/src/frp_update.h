/* SPDX-License-Identifier: GPL-3.0-only */
/* CHECK FOR UPDATES: the plugin finds a newer Frodhes release, downloads this system's package into the user's
 * Downloads folder and offers it on an INSTALL key (docs/PLUGIN.md). The user installs it: nothing is installed or
 * replaced by the plugin.
 *
 * What it contacts: one HTTPS GET of FRP_UPDATE_URL (GitHub's REST API, the repository's latest release: published,
 * not a draft, not a pre-release); its "tag_name" (vX.Y.Z) is compared with the plugin's own version. When it is
 * newer, the answer's asset of this system (frp_update_asset_name) gives the package's URL, size and sha256
 * ("digest"); the package is downloaded (redirects followed, https only), its sha256 checked against the digest, and
 * written as <Downloads>/frodhes-plugin-<os>-X.Y.Z.<ext>. No digest, a mismatch or any failure: nothing is kept and
 * the key downloads through the browser instead (FRP_UPDATE_DL). Nothing is sent but the requests (no identifier, no
 * telemetry). When: at most once per FRP_UPDATE_PERIOD (24 h, per user account: the time is kept in the settings
 * file), only when the plugin's window opens, on a thread of its own (never the audio thread; the window and the host
 * never wait for it); 5 s for the check, 5 min for the package; any failure is silent. A developer build (version
 * 0.0.0-dev) never checks and never shows a notice. SETTINGS' UPDATES OFF turns all of it off (kept in the settings
 * file), as does FRODHES_UPDATE_CHECK=0 in the environment for a whole process (the test hosts set it).
 *
 * Per OS, no library beyond the system: macOS NSURLSession (Foundation), Windows WinHTTP (winhttp.dll), Linux
 * libcurl (libcurl.so.4) loaded with dlopen when the system has it, else no check. TLS is verified by each.
 *
 * Two layers: the pure functions (versions, the answer, sha256, the 24 h rule, the settings file's text:
 * frp_update.c, tested on any OS by plugin/tests/test_update.c) and the runtime (frp_update_rt.c: the cache, the
 * thread, the files) over a few calls per OS (frp_update_net.c, frp_update_mac.m). */
#ifndef FRP_UPDATE_H
#define FRP_UPDATE_H
#include <stddef.h>
#include <stdint.h>

#define FRP_UPDATE_URL "https://api.github.com/repos/leonardoMirabal/frodhes/releases/latest"
/* the latest release's package for each system, through the browser (the release's fixed asset names:
 * tools/release_assets.sh REQ) */
#define FRP_UPDATE_DL "https://github.com/leonardoMirabal/frodhes/releases/latest/download/"
/* a package URL from the API's answer must start so (the repository's own release assets) */
#define FRP_UPDATE_ASSET_PREFIX "https://github.com/leonardoMirabal/frodhes/releases/download/"
enum { FRP_UPD_ARCH_X86_64, FRP_UPD_ARCH_AARCH64 };
#if defined(__aarch64__) || defined(_M_ARM64)
#define FRP_UPD_ARCH_HERE FRP_UPD_ARCH_AARCH64
#else
#define FRP_UPD_ARCH_HERE FRP_UPD_ARCH_X86_64
#endif
#define FRP_UPDATE_PERIOD 86400          /* seconds between two checks */
#define FRP_UPDATE_TIMEOUT_MS 5000       /* the check's request */
#define FRP_UPDATE_DL_TIMEOUT_MS 300000  /* the package's download */
#define FRP_UPDATE_MAX_BODY (512 * 1024) /* the answer is read up to this (the release's JSON: about 40 KB) */
#define FRP_UPDATE_MAX_PKG (256u << 20)  /* a package larger than this is not downloaded (the .pkg: 34 MB) */
enum { FRP_UPD_NONE, FRP_UPD_AVAILABLE, FRP_UPD_READY };   /* frp_update_available: none, a newer release (the key
                                                            * downloads it), downloaded and checked (the key opens it) */

/* ---- the pure part */
typedef struct {
    int check;                           /* UPDATES ON (1, the default) / OFF (0) */
    long long checked;                   /* the last check's time (Unix seconds; 0 never), done or failed */
    char latest[16];                     /* the latest release found ("0.9.13"; "" none yet) */
    char file[1024];                     /* its package, downloaded and checked ("" none) */
    char file_ver[16];                   /* the version of that package */
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
/* the answer's "tag_name" ("v0.9.13") as "0.9.13": 0 ok, -1 none or not a version */
int frp_update_tag(const char *body, size_t n, char out[16]);
/* this system's package: its asset name (macOS the universal .pkg, Windows the zip, Linux the zip of its
 * architecture; os: frp_paths.h FRP_OS_*), the browser's direct download of the latest one, and the name it gets in
 * the Downloads folder (with the version: "frodhes-plugin-macos-0.9.13.pkg") */
const char *frp_update_asset_name(int os, int arch);
const char *frp_update_download_url(int os, int arch);
int frp_update_file_name(int os, int arch, const char *ver, char *out, size_t n);
/* the answer's asset called name: its download URL (FRP_UPDATE_ASSET_PREFIX...), size and sha256 digest (64 lower-case
 * hex digits, from "digest": "sha256:..."): 0 ok; -1 no such asset, no sha256 digest or a URL elsewhere */
int frp_update_asset(const char *body, size_t n, const char *name, char url[512], long long *size, char sha[65]);
/* SHA-256 of n bytes, as 64 lower-case hex digits */
void frp_sha256_hex(const void *data, size_t n, char hex[65]);
/* what a window opening does: 1 check now (on), 0 nothing */
int frp_update_plan(const frp_update_state *s, const char *own, long long now);
/* the settings file (settings.json, a flat JSON object): its keys "update_check" (true / false), "update_checked"
 * (seconds), "update_latest" ("X.Y.Z"), "update_file" (a path), "update_file_version"; the other keys are the
 * plugin's other settings, kept as they are */
void frp_update_state_default(frp_update_state *s);
int frp_update_state_read(const char *text, frp_update_state *s);    /* 0 ok (missing keys: defaults); -1 not a flat
                                                                       * object (s: the defaults) */
/* the file's new text: old (NULL or "": none) with the keys set, every other key kept in its order; 0 ok, -1 old is
 * not a flat object (it is then never overwritten) or out is too small */
int frp_update_state_write(const char *old, const frp_update_state *s, char *out, size_t n);

/* ---- the runtime (main thread unless said) */
void frp_update_init(const char *own_version);      /* the entry's init: the plugin's own version text */
void frp_update_shutdown(void);                     /* the entry's deinit: a check under way is cancelled, waited for */
void frp_update_poke(void);                         /* the window opened: check when due (on a thread of its own) */
int frp_update_available(char ver[16]);             /* FRP_UPD_* (UPDATES ON only): ver "X.Y.Z" or "" */
int frp_update_enabled(void);
void frp_update_set_enabled(int on);                /* saved in the settings file; ON checks at once when due */
/* the key: READY opens the downloaded package (macOS: the .pkg in Installer; Windows / Linux: its folder, the zip
 * selected where the system can), else the browser downloads FRP_UPDATE_DL...: 0 ok */
int frp_update_open_download(void);
int frp_update_busy(void);                          /* a check is under way (the tests) */
/* the folder the package goes to: FRODHES_DOWNLOAD_DIR (the tests), else the user's Downloads folder: 0 ok */
int frp_update_downloads_dir(char *out, size_t n);

/* ---- per OS: one HTTPS GET with TLS verified, timeout_ms for the whole request, nothing but the request (no
 * cookie, no cache); follow: redirects followed (https only; the package's GitHub storage), else none (the API).
 * The body into buf (cut at cap - 1, NUL-terminated), *len its length. The HTTP status, -1 when there is none (no
 * network, no libcurl, cancelled, timed out). Called on the check's own thread */
int frp_update_fetch(const char *url, char *buf, size_t cap, size_t *len, int timeout_ms, int follow);
void frp_update_fetch_cancel(void);                 /* (any thread) a fetch under way ends at once with -1 */
int frp_update_open_url(const char *url);           /* (main thread) an https URL in the default browser: 0 ok */
int frp_update_reveal(const char *path);            /* (main thread) open / show a downloaded package: 0 ok */
#endif
