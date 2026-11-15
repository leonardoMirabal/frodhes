/* SPDX-License-Identifier: GPL-3.0-only */
/* CHECK FOR UPDATES (plugin/src/frp_update.h) without the network: the pure part (versions, the response, the
 * assets, sha256, the 24 h rule, the settings file's text), then the runtime over a stand-in fetch (defined here) in a
 * scratch home: the thread, the throttle, the file, the off switch, a developer build, a failure, a cancel at
 * shutdown, the package's download (digest match: INSTALL; mismatch or no digest: nothing kept, the browser's key).
 *   cc -I plugin/src plugin/tests/test_update.c plugin/src/frp_update.c plugin/src/frp_update_rt.c \
 *      plugin/src/frp_paths.c -lpthread -o test_update && ./test_update SCRATCH_DIR
 * (tests/py/test_plugin_update.py builds and runs it) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "frp_paths.h"
#include "frp_update.h"
#if defined(_WIN32)
#include <windows.h>
static void nap(int ms) { Sleep((DWORD)ms); }
static void set_env(const char *k, const char *v) { SetEnvironmentVariableA(k, v); _putenv_s(k, v); }
#else
#include <unistd.h>
static void nap(int ms) { usleep((useconds_t)ms * 1000u); }
static void set_env(const char *k, const char *v) { setenv(k, v, 1); }
#endif

#define D1 "0000000000000000000000000000000000000000000000000000000000000001"
#define D2 "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
#define D3 "2D711642B726B04401627CA9FBAC32F5C8530FB1903CC4DB02258717921A4881"
static int fails;
#define EXPECT(c, ...) do { if (!(c)) { fails++; printf("FAIL %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

/* ---- the stand-in fetch: the API's answer (fetch_body), else the package (pkg_body) */
static volatile int n_fetch, fetch_status = 200, fetch_slow, cancelled, n_pkg, pkg_follow, n_reveal, n_open;
static const char *fetch_body = "{\"url\":\"x\",\"tag_name\":\"v0.9.13\",\"name\":\"Frodhes 0.9.13\"}";
static const char *pkg_body = "PKG";
static char fetch_url[256], pkg_url[512], revealed[1024], opened[256];
int frp_update_fetch(const char *url, char *buf, size_t cap, size_t *len, int timeout_ms, int follow)
{
    size_t n = strlen(fetch_body);
    int waited = 0;
    (void)timeout_ms;
    if (strcmp(url, FRP_UPDATE_URL)) {
        size_t m = strlen(pkg_body);
        n_pkg++;
        pkg_follow = follow;
        snprintf(pkg_url, sizeof pkg_url, "%s", url);
        if (m > cap - 1) m = cap - 1;
        memcpy(buf, pkg_body, m);
        buf[m] = 0;
        *len = m;
        return 200;
    }
    n_fetch++;
    if (follow) return -1;                       /* (the API: no redirects) */
    snprintf(fetch_url, sizeof fetch_url, "%s", url);
    while (fetch_slow && !cancelled && waited < 4000) { nap(5); waited += 5; }   /* (a slow network: until cancelled) */
    if (cancelled) return -1;
    if (fetch_status < 0) return -1;
    if (n > cap - 1) n = cap - 1;
    memcpy(buf, fetch_body, n);
    buf[n] = 0;
    *len = n;
    return fetch_status;
}
void frp_update_fetch_cancel(void) { cancelled = 1; }
int frp_update_open_url(const char *url) { n_open++; snprintf(opened, sizeof opened, "%s", url); return 0; }
int frp_update_reveal(const char *path) { n_reveal++; snprintf(revealed, sizeof revealed, "%s", path); return 0; }

static void wait_idle(void) { int i; for (i = 0; i < 400 && frp_update_busy(); i++) nap(5); }
static char settings[1024];
static char *slurp(void)
{
    static char t[8192];
    FILE *f = frp_fopen(settings, "rb");
    size_t n;
    if (!f) return 0;
    n = fread(t, 1, sizeof t - 1, f);
    fclose(f);
    t[n] = 0;
    return t;
}
static void put(const char *text)
{
    FILE *f = frp_fopen(settings, "wb");
    if (f) { fputs(text, f); fclose(f); }
}

static void pure(void)
{
    int v[3], a[3], b[3];
    char o[16], o2[64], big[600], u[512], h[65];
    long long sz;
    frp_update_state s;
    long long now = 1800000000LL;
    /* versions */
    EXPECT(!frp_ver_parse("0.9.12", v) && v[0] == 0 && v[1] == 9 && v[2] == 12, "0.9.12");
    EXPECT(!frp_ver_parse("v1.20.300", v) && v[0] == 1 && v[1] == 20 && v[2] == 300, "v1.20.300");
    EXPECT(frp_ver_parse("0.9", v) && frp_ver_parse("0.9.1.2", v) && frp_ver_parse("0.9.1-rc1", v) &&
           frp_ver_parse(" 0.9.1", v) && frp_ver_parse("0.9.x", v) && frp_ver_parse("", v) && frp_ver_parse(0, v) &&
           frp_ver_parse("V0.9.1", v) && frp_ver_parse("0..1", v) && frp_ver_parse("123456.0.0", v), "not versions");
    frp_ver_parse("0.9.9", a); frp_ver_parse("0.9.10", b);
    EXPECT(frp_ver_cmp(a, b) < 0 && frp_ver_cmp(b, a) > 0 && frp_ver_cmp(a, a) == 0, "numeric compare");
    EXPECT(frp_ver_is_release("0.9.11") && !frp_ver_is_release("0.0.0-dev") && !frp_ver_is_release("0.0.0") &&
           !frp_ver_is_release("dev") && !frp_ver_is_release("DEV 1a2b") && !frp_ver_is_release("") &&
           !frp_ver_is_release(0) && !frp_ver_is_release("v0.9.11"), "releases and developer builds");
    EXPECT(frp_update_newer("0.9.11", "0.9.13", o) == 1 && !strcmp(o, "0.9.13"), "newer");
    EXPECT(frp_update_newer("0.9.9", "v0.9.10", o) == 1 && !strcmp(o, "0.9.10"), "numeric, not text: 0.9.10 > 0.9.9");
    EXPECT(frp_update_newer("0.9.13", "0.10.0", o) == 1 && !strcmp(o, "0.10.0"), "0.10.0 > 0.9.13");
    EXPECT(!frp_update_newer("0.9.13", "0.9.13", o) && !o[0], "the same: no notice");
    EXPECT(!frp_update_newer("0.9.13", "0.9.12", o) && !o[0], "older: no notice");
    EXPECT(!frp_update_newer("0.0.0-dev", "0.9.13", o) && !frp_update_newer("dev", "0.9.13", o) &&
           !frp_update_newer("0.0.0", "9.9.9", o), "a developer build never shows the notice");
    EXPECT(!frp_update_newer("0.9.11", "", o) && !frp_update_newer("0.9.11", "garbage", o), "no latest");
    /* the 24 h rule */
    EXPECT(frp_update_due(now, 0), "never checked: due");
    EXPECT(!frp_update_due(now, now - 1) && !frp_update_due(now, now - 86399), "within a day: not due");
    EXPECT(frp_update_due(now, now - 86400) && frp_update_due(now, now - 900000), "a day later: due");
    EXPECT(!frp_update_due(now, now + 3600) && frp_update_due(now, now + 86400), "the clock behind a day: due");
    /* the response */
    {
        static const char body[] =
            "{\n  \"url\": \"https://api.github.com/repos/o/r/releases/1\",\n  \"name\": \"Frodhes 0.9.12\",\n"
            "  \"body\": \"notes with \\\"tag_name\\\": \\\"v9.9.9\\\" quoted\",\n"
            "  \"tag_name\" :  \"v0.9.12\",\n  \"draft\": false\n}";
        EXPECT(!frp_update_tag(body, sizeof body - 1, o) && !strcmp(o, "0.9.12"), "the tag (%s)", o);
        EXPECT(frp_update_tag(body, 60, o) && !o[0], "a cut response: none");
        EXPECT(frp_update_tag("{\"tag_name\":\"nightly\"}", 22, o), "a tag that is no version");
        EXPECT(frp_update_tag("{\"message\":\"API rate limit exceeded\"}", 37, o), "an error: none");
        EXPECT(frp_update_tag("", 0, o) && frp_update_tag("\"tag_name\"", 10, o) && frp_update_tag("{\"tag_name\":12}", 15, o),
               "no tag");
        EXPECT(!frp_update_tag("{\"tag_name\":\"0.9.14\"}", 21, o) && !strcmp(o, "0.9.14"), "a tag without v");
    }
    /* the settings file's text */
    frp_update_state_default(&s);
    EXPECT(s.check == 1 && s.checked == 0 && !s.latest[0], "defaults: on, never checked");
    EXPECT(!frp_update_state_read("", &s) && s.check == 1, "an empty file");
    EXPECT(!frp_update_state_read("{\"update_check\": false, \"update_checked\": 1763000000, \"update_latest\": \"0.9.12\"}", &s) &&
           s.check == 0 && s.checked == 1763000000LL && !strcmp(s.latest, "0.9.12"), "read");
    EXPECT(!frp_update_state_read("{\"update_check\":true,\"update_checked\":-5,\"update_latest\":\"x\"}", &s) &&
           s.check == 1 && s.checked == 0 && !s.latest[0], "bad values: defaults");
    EXPECT(frp_update_state_read("{\"a\": {\"b\": 1}}", &s) && s.check == 1, "nested: not ours");
    EXPECT(frp_update_state_read("garbage", &s) && frp_update_state_read("{\"a\":1", &s) &&
           frp_update_state_read("{\"a\":1} x", &s), "garbage");
    s.check = 0; s.checked = 1763000001LL; snprintf(s.latest, sizeof s.latest, "0.9.13");
    EXPECT(!frp_update_state_write(0, &s, big, sizeof big) &&
           !strcmp(big, "{\n  \"update_check\": false,\n  \"update_checked\": 1763000001,\n  \"update_latest\": \"0.9.13\",\n"
                   "  \"update_file\": \"\",\n  \"update_file_version\": \"\"\n}\n"),
           "write: %s", big);
    {
        frp_update_state r;
        EXPECT(!frp_update_state_read(big, &r) && r.check == 0 && r.checked == s.checked && !strcmp(r.latest, "0.9.13"),
               "round trip");
    }
    EXPECT(!frp_update_state_write("{\"theme\": \"dark\", \"update_check\": true, \"n\": 3, \"s\": \"a,}b\"}", &s, big, sizeof big) &&
           strstr(big, "\"theme\": \"dark\",\n  \"n\": 3,\n  \"s\": \"a,}b\",\n  \"update_check\": false"), "other keys kept: %s", big);
    EXPECT(frp_update_state_write("{\"a\": [1]}", &s, big, sizeof big), "a file that is not flat is never rewritten");
    EXPECT(frp_update_state_write(0, &s, big, 20), "no room");
    /* the download per system: the release's fixed asset names (tools/release_assets.sh REQ) */
    EXPECT(!strcmp(frp_update_download_url(FRP_OS_MACOS, FRP_UPD_ARCH_AARCH64),
                   "https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes-plugin-macos.pkg") &&
           !strcmp(frp_update_download_url(FRP_OS_MACOS, FRP_UPD_ARCH_X86_64), frp_update_download_url(FRP_OS_MACOS, 1)),
           "macOS: the universal .pkg");
    EXPECT(!strcmp(frp_update_download_url(FRP_OS_WINDOWS, FRP_UPD_ARCH_X86_64),
                   "https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes-plugin-windows.zip"),
           "Windows: the zip");
    EXPECT(!strcmp(frp_update_download_url(FRP_OS_LINUX, FRP_UPD_ARCH_X86_64),
                   "https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes-plugin-linux-x86_64.zip") &&
           !strcmp(frp_update_download_url(FRP_OS_LINUX, FRP_UPD_ARCH_AARCH64),
                   "https://github.com/leonardoMirabal/frodhes/releases/latest/download/frodhes-plugin-linux-aarch64.zip"),
           "Linux: the zip of its architecture");
    /* the name in the Downloads folder */
    EXPECT(!frp_update_file_name(FRP_OS_MACOS, FRP_UPD_ARCH_AARCH64, "0.9.13", o2, sizeof o2) &&
           !strcmp(o2, "frodhes-plugin-macos-0.9.13.pkg"), "macOS file: %s", o2);
    EXPECT(!frp_update_file_name(FRP_OS_WINDOWS, FRP_UPD_ARCH_X86_64, "v0.10.0", o2, sizeof o2) &&
           !strcmp(o2, "frodhes-plugin-windows-0.10.0.zip"), "Windows file: %s", o2);
    EXPECT(!frp_update_file_name(FRP_OS_LINUX, FRP_UPD_ARCH_X86_64, "0.9.13", o2, sizeof o2) &&
           !strcmp(o2, "frodhes-plugin-linux-x86_64-0.9.13.zip") &&
           !frp_update_file_name(FRP_OS_LINUX, FRP_UPD_ARCH_AARCH64, "0.9.13", o2, sizeof o2) &&
           !strcmp(o2, "frodhes-plugin-linux-aarch64-0.9.13.zip"), "Linux files: %s", o2);
    EXPECT(frp_update_file_name(FRP_OS_LINUX, 0, "../x", o2, sizeof o2) && frp_update_file_name(FRP_OS_MACOS, 0, "0.9.13", o2, 10),
           "a bad version or no room: none");
    /* sha256 (FIPS 180-2 vectors) */
    frp_sha256_hex("", 0, h);
    EXPECT(!strcmp(h, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"), "sha256 of nothing: %s", h);
    frp_sha256_hex("abc", 3, h);
    EXPECT(!strcmp(h, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"), "sha256 of abc: %s", h);
    frp_sha256_hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq", 56, h);
    EXPECT(!strcmp(h, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"), "sha256 of 56 bytes: %s", h);
    {
        static char m[1000001];
        memset(m, 'a', 1000000);
        frp_sha256_hex(m, 1000000, h);
        EXPECT(!strcmp(h, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"), "sha256 of a million a: %s", h);
    }
    /* the answer's assets */
    {
        static const char body[] =
            "{\"tag_name\":\"v0.9.13\",\"author\":{\"name\":\"frodhes-plugin-macos.pkg\"},\"assets\":["
            "{\"name\":\"frodhes-plugin-windows.zip\",\"size\":10,\"digest\":\"sha256:" D1 "\","
            "\"browser_download_url\":\"https://github.com/leonardoMirabal/frodhes/releases/download/v0.9.13/frodhes-plugin-windows.zip\"},"
            "{\"uploader\":{\"name\":\"frodhes-plugin-linux-x86_64.zip\",\"id\":1},\"name\":\"frodhes-plugin-macos.pkg\","
            "\"label\":null,\"size\":34163587,\"digest\":\"sha256:" D2 "\","
            "\"browser_download_url\":\"https:\\/\\/github.com\\/leonardoMirabal\\/frodhes\\/releases\\/download\\/v0.9.13\\/frodhes-plugin-macos.pkg\"},"
            "{\"name\":\"frodhes-plugin-linux-aarch64.zip\",\"size\":5,\"digest\":null,"
            "\"browser_download_url\":\"https://github.com/leonardoMirabal/frodhes/releases/download/v0.9.13/frodhes-plugin-linux-aarch64.zip\"},"
            "{\"name\":\"frodhes-plugin-linux-x86_64.zip\",\"size\":5,\"digest\":\"sha256:" D1 "\","
            "\"browser_download_url\":\"https://example.com/releases/download/v0.9.13/frodhes-plugin-linux-x86_64.zip\"}"
            "],\"body\":\"x\"}";
        EXPECT(!frp_update_asset(body, sizeof body - 1, "frodhes-plugin-macos.pkg", u, &sz, h) && sz == 34163587 &&
               !strcmp(h, D2) &&
               !strcmp(u, "https://github.com/leonardoMirabal/frodhes/releases/download/v0.9.13/frodhes-plugin-macos.pkg"),
               "the macOS asset (a nested name ignored): %s %lld %s", u, sz, h);
        EXPECT(!frp_update_asset(body, sizeof body - 1, "frodhes-plugin-windows.zip", u, &sz, h) && sz == 10 && !strcmp(h, D1),
               "the Windows asset");
        EXPECT(frp_update_asset(body, sizeof body - 1, "frodhes-plugin-linux-aarch64.zip", u, &sz, h) && !u[0] && !h[0],
               "no digest: none");
        EXPECT(frp_update_asset(body, sizeof body - 1, "frodhes-plugin-linux-x86_64.zip", u, &sz, h),
               "a URL elsewhere: none");
        EXPECT(frp_update_asset(body, sizeof body - 1, "frodhes-plugin-nothing.zip", u, &sz, h), "no such asset");
        EXPECT(frp_update_asset(body, 200, "frodhes-plugin-macos.pkg", u, &sz, h), "a cut answer: none");
        EXPECT(frp_update_asset("{\"assets\":[{\"name\":\"a\",\"size\":1,\"digest\":\"sha256:" D3 "\","
                                "\"browser_download_url\":\"https://github.com/leonardoMirabal/frodhes/releases/download/v1/a\"}]}",
                                250, "a", u, &sz, h), "an upper-case digest: none");
    }
    /* the plan */
    frp_update_state_default(&s);
    EXPECT(frp_update_plan(&s, "0.9.11", now), "on, never checked, a release: check");
    EXPECT(!frp_update_plan(&s, "0.0.0-dev", now), "a developer build: never");
    s.check = 0;
    EXPECT(!frp_update_plan(&s, "0.9.11", now), "off: never");
    s.check = 1; s.checked = now - 100;
    EXPECT(!frp_update_plan(&s, "0.9.11", now), "checked an hour ago: not again");
}

static void runtime(const char *scratch)
{
    char ver[16], *t;
    long long now = (long long)time(0);
    frp_env_t e;
    char buf[400];
    set_env("HOME", scratch);
    set_env("XDG_DATA_HOME", scratch);
    set_env("APPDATA", scratch);
    frp_env_here(&e);
    frp_path_settings(FRP_OS_HERE, &e, settings, sizeof settings);
    frp_remove(settings);

    frp_update_init("0.9.11");
    EXPECT(!frp_update_available(ver) && !ver[0] && frp_update_enabled(), "before any check: nothing, on");
    frp_update_poke();
    wait_idle();
    EXPECT(n_fetch == 1 && !strcmp(fetch_url, FRP_UPDATE_URL), "the window opened: one check of %s", fetch_url);
    EXPECT(frp_update_available(ver) && !strcmp(ver, "0.9.13"), "newer known: %s", ver);
    t = slurp();
    EXPECT(t && strstr(t, "\"update_latest\": \"0.9.13\"") && strstr(t, "\"update_check\": true"), "saved: %s", t ? t : "(none)");
    frp_update_poke(); wait_idle();
    frp_update_poke(); wait_idle();
    EXPECT(n_fetch == 1, "opened again within a day: no check (%d)", n_fetch);

    /* a day later (the file says so), with another key of the plugin's: it is kept */
    snprintf(buf, sizeof buf, "{\"theme\": \"dark\", \"update_check\": true, \"update_checked\": %lld, \"update_latest\": \"0.9.13\"}",
             now - 86401);
    put(buf);
    fetch_body = "{\"tag_name\":\"v0.9.14\"}";
    frp_update_poke(); wait_idle();
    EXPECT(n_fetch == 2 && frp_update_available(ver) && !strcmp(ver, "0.9.14"), "a day later: checked again (%d, %s)", n_fetch, ver);
    t = slurp();
    EXPECT(t && strstr(t, "\"theme\": \"dark\"") && strstr(t, "0.9.14"), "the other key kept: %s", t ? t : "(none)");

    /* off: no notice, no check, saved */
    frp_update_set_enabled(0);
    EXPECT(!frp_update_enabled() && !frp_update_available(ver) && !ver[0], "off: no notice");
    t = slurp();
    EXPECT(t && strstr(t, "\"update_check\": false"), "off saved: %s", t ? t : "(none)");
    snprintf(buf, sizeof buf, "{\"update_check\": false, \"update_checked\": %lld, \"update_latest\": \"0.9.14\"}", now - 200000);
    put(buf);
    frp_update_poke(); wait_idle();
    EXPECT(n_fetch == 2, "off: no check (%d)", n_fetch);
    /* on again: checks at once (due) */
    fetch_body = "{\"tag_name\":\"v0.9.15\"}";
    frp_update_set_enabled(1);
    wait_idle();
    EXPECT(n_fetch == 3 && frp_update_available(ver) && !strcmp(ver, "0.9.15"), "on again: checked (%d, %s)", n_fetch, ver);

    /* a failure: silent, the time kept (no retry until a day later), the last result kept */
    snprintf(buf, sizeof buf, "{\"update_checked\": %lld, \"update_latest\": \"0.9.15\"}", now - 90000);
    put(buf);
    fetch_status = -1;
    frp_update_poke(); wait_idle();
    EXPECT(n_fetch == 4 && frp_update_available(ver) && !strcmp(ver, "0.9.15"), "a failure: the last result kept");
    frp_update_poke(); wait_idle();
    EXPECT(n_fetch == 4, "a failure: no retry within the day (%d)", n_fetch);
    snprintf(buf, sizeof buf, "{\"update_checked\": %lld, \"update_latest\": \"0.9.15\"}", now - 90000);
    put(buf);
    fetch_status = 403;                           /* (the API's rate limit: an answer without a tag) */
    fetch_body = "{\"message\":\"API rate limit exceeded\"}";
    frp_update_poke(); wait_idle();
    EXPECT(n_fetch == 5 && frp_update_available(ver) && !strcmp(ver, "0.9.15"), "403: the last result kept");
    fetch_status = 200;

    /* FRODHES_UPDATE_CHECK=0: never checks, never shows */
    snprintf(buf, sizeof buf, "{\"update_checked\": %lld, \"update_latest\": \"0.9.15\"}", now - 90000);
    put(buf);
    set_env("FRODHES_UPDATE_CHECK", "0");
    frp_update_init("0.9.11");
    frp_update_poke(); wait_idle();
    EXPECT(n_fetch == 5 && !frp_update_available(ver) && !ver[0], "FRODHES_UPDATE_CHECK=0: no check, no notice");
    set_env("FRODHES_UPDATE_CHECK", "1");
    frp_update_init("0.9.11");
    EXPECT(frp_update_available(ver) && !strcmp(ver, "0.9.15"), "the environment's switch read at init");

    /* a developer build: never checks, never shows */
    snprintf(buf, sizeof buf, "{\"update_checked\": %lld, \"update_latest\": \"0.9.15\"}", now - 90000);
    put(buf);
    frp_update_init("0.0.0-dev");
    frp_update_poke(); wait_idle();
    EXPECT(n_fetch == 5 && !frp_update_available(ver) && !ver[0], "a developer build: no check, no notice");
    /* this release is the latest: no notice */
    frp_update_init("0.9.15");
    EXPECT(!frp_update_available(ver), "up to date: no notice");

    /* a file that is not ours (nested): read as defaults, never rewritten */
    put("{\"window\": {\"w\": 960}}");
    frp_update_init("0.9.11");
    frp_update_poke(); wait_idle();
    t = slurp();
    EXPECT(t && !strcmp(t, "{\"window\": {\"w\": 960}}"), "not ours: untouched: %s", t ? t : "(none)");

    /* shutdown while a check hangs: cancelled, waited for, nothing saved */
    snprintf(buf, sizeof buf, "{\"update_checked\": %lld, \"update_latest\": \"0.9.15\"}", now - 90000);
    put(buf);
    {
        double el;
        int n0 = n_fetch;
        frp_update_init("0.9.11");
        fetch_slow = 1;
        frp_update_poke();
        nap(50);
        {
            time_t w0 = time(0);
            frp_update_shutdown();
            el = difftime(time(0), w0);
        }
        EXPECT(n_fetch == n0 + 1 && cancelled && el <= 2.0, "shutdown cancels the check (%.0f s)", el);
#if !defined(_WIN32)
        EXPECT(!frp_update_busy(), "shutdown waited for the thread");
#endif
        wait_idle();
        t = slurp();
        EXPECT(t && !strcmp(t, buf), "a cancelled check saves nothing: %s", t ? t : "(none)");
        frp_update_poke(); wait_idle();
        EXPECT(n_fetch == n0 + 1, "after shutdown: no new check");
        fetch_slow = 0;
    }
}

/* the package: downloaded into FRODHES_DOWNLOAD_DIR when its sha256 matches the answer's digest */
static void downloads(const char *scratch)
{
    static char body[2048];
    /* path: dir + the separator + name[96] + NUL, d: path + ".part" (sized so no snprintf can truncate: gcc's
       -Wformat-truncation with fortify on Linux, the CI's -Werror) */
    char ver[16], dir[1024], path[1024 + 1 + 96], want[65], d[1024 + 1 + 96 + 8], *t;
    long long now = (long long)time(0);
    FILE *f;
    snprintf(dir, sizeof dir, "%s%cdl", scratch, frp_path_sep(FRP_OS_HERE));
    frp_mkdirs(dir);
    set_env("FRODHES_DOWNLOAD_DIR", dir);
    EXPECT(!frp_update_downloads_dir(d, sizeof d) && !strcmp(d, dir), "the folder: FRODHES_DOWNLOAD_DIR (%s)", d);
    {
        char name[96];
        frp_update_file_name(FRP_OS_HERE, FRP_UPD_ARCH_HERE, "0.9.20", name, sizeof name);
        snprintf(path, sizeof path, "%s%c%s", dir, frp_path_sep(FRP_OS_HERE), name);
    }
    pkg_body = "the package's bytes";
    frp_sha256_hex(pkg_body, strlen(pkg_body), want);
#define ANSWER(dig) snprintf(body, sizeof body, "{\"tag_name\":\"v0.9.20\",\"assets\":[{\"name\":\"%s\",\"size\":%u," \
        "\"digest\":%s,\"browser_download_url\":\"" FRP_UPDATE_ASSET_PREFIX "v0.9.20/%s\"}]}", \
        frp_update_asset_name(FRP_OS_HERE, FRP_UPD_ARCH_HERE), (unsigned)strlen(pkg_body), dig, \
        frp_update_asset_name(FRP_OS_HERE, FRP_UPD_ARCH_HERE))
#define DUE() do { snprintf(d, sizeof d, "{\"update_checked\": %lld, \"update_latest\": \"0.9.15\"}", now - 90000); put(d); } while (0)
    fetch_body = body;
    fetch_status = 200;
    cancelled = 0;
    frp_update_init("0.9.11");

    /* a mismatch: nothing kept, the browser's key */
    {
        char bad[80];
        snprintf(bad, sizeof bad, "\"sha256:%s\"", D1);
        ANSWER(bad);
    }
    DUE(); n_pkg = 0;
    frp_update_poke(); wait_idle();
    EXPECT(n_pkg == 1 && pkg_follow && !strncmp(pkg_url, FRP_UPDATE_ASSET_PREFIX, strlen(FRP_UPDATE_ASSET_PREFIX)),
           "a newer release: its package fetched, redirects followed (%d %s)", n_pkg, pkg_url);
    EXPECT(frp_update_available(ver) == FRP_UPD_AVAILABLE && !strcmp(ver, "0.9.20"), "a mismatch: AVAILABLE (%s)", ver);
    EXPECT(!(f = frp_fopen(path, "rb")), "a mismatch: no file");
    if (f) fclose(f);
    snprintf(d, sizeof d, "%s.part", path);
    EXPECT(!(f = frp_fopen(d, "rb")), "a mismatch: no .part file");
    if (f) fclose(f);
    n_open = n_reveal = 0;
    frp_update_open_download();
    EXPECT(n_open == 1 && !n_reveal && !strcmp(opened, frp_update_download_url(FRP_OS_HERE, FRP_UPD_ARCH_HERE)),
           "AVAILABLE: the key opens the browser's download (%s)", opened);

    /* no digest: not downloaded at all */
    ANSWER("null");
    DUE(); n_pkg = 0;
    frp_update_poke(); wait_idle();
    EXPECT(!n_pkg && frp_update_available(ver) == FRP_UPD_AVAILABLE, "no digest: no download, AVAILABLE (%d)", n_pkg);
    /* a cut download (the size differs): nothing kept */
    {
        char good[80];
        snprintf(good, sizeof good, "\"sha256:%s\"", want);
        ANSWER(good);
    }
    pkg_body = "the package's byte";
    DUE(); n_pkg = 0;
    frp_update_poke(); wait_idle();
    EXPECT(n_pkg == 1 && frp_update_available(ver) == FRP_UPD_AVAILABLE && !(f = frp_fopen(path, "rb")), "a cut file: nothing");
    if (f) fclose(f);
    pkg_body = "the package's bytes";

    /* a match: the file in Downloads, READY, saved, the key reveals it */
    DUE(); n_pkg = 0;
    frp_update_poke(); wait_idle();
    EXPECT(n_pkg == 1 && frp_update_available(ver) == FRP_UPD_READY && !strcmp(ver, "0.9.20"), "a match: READY (%d)", n_pkg);
    EXPECT((f = frp_fopen(path, "rb")) != 0, "the file: %s", path);
    if (f) {
        char got[64] = "";
        size_t n = fread(got, 1, sizeof got - 1, f);
        fclose(f);
        got[n] = 0;
        EXPECT(!strcmp(got, pkg_body), "its bytes: %s", got);
    }
    t = slurp();
    EXPECT(t && strstr(t, "\"update_file_version\": \"0.9.20\"") && strstr(t, "\"update_file\": \""), "saved: %s", t ? t : "");
    n_open = n_reveal = 0;
    frp_update_open_download();
    EXPECT(n_reveal == 1 && !n_open && !strcmp(revealed, path), "READY: the key opens the file (%s)", revealed);
    /* a day later, the same release: not downloaded again */
    t = slurp();
    if (t) {
        char *c = strstr(t, "\"update_checked\": ");
        if (c) { snprintf(d, sizeof d, "%lld", now - 90000); memcpy(c + 18, d, strlen(d)); }
        put(t);
    }
    n_pkg = 0;
    frp_update_poke(); wait_idle();
    EXPECT(!n_pkg && frp_update_available(ver) == FRP_UPD_READY, "downloaded once (%d)", n_pkg);
    /* the file deleted: the key falls back to the browser; next window: AVAILABLE */
    frp_remove(path);
    n_open = n_reveal = 0;
    frp_update_open_download();
    EXPECT(n_open == 1 && !n_reveal, "the file gone: the browser");
    frp_update_poke(); wait_idle();
    EXPECT(frp_update_available(ver) == FRP_UPD_AVAILABLE, "the file gone: AVAILABLE");
    /* OFF: no notice and no download */
    frp_update_set_enabled(0);
    snprintf(d, sizeof d, "{\"update_check\": false, \"update_checked\": %lld, \"update_latest\": \"0.9.20\"}", now - 90000);
    put(d); n_pkg = 0;
    frp_update_poke(); wait_idle();
    EXPECT(!n_pkg && frp_update_available(ver) == FRP_UPD_NONE, "off: nothing");
    frp_update_set_enabled(1);
    wait_idle();
    frp_update_shutdown();
    set_env("FRODHES_DOWNLOAD_DIR", "");
}

int main(int argc, char **argv)
{
    pure();
    if (argc > 1) runtime(argv[1]);
    if (argc > 1) downloads(argv[1]);
    if (fails) { printf("%d failed\n", fails); return 1; }
    printf("ok    update check: versions, the response, the 24 h rule, the settings file%s\n",
           argc > 1 ? "; the runtime (thread, throttle, off switch, developer build, failures, shutdown, the package)" : "");
    return 0;
}
