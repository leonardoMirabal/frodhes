/* SPDX-License-Identifier: GPL-3.0-only */
/* CHECK FOR UPDATES (plugin/src/frp_update.h) without the network: the pure part (versions, the response, the 24 h
 * rule, the settings file's text), then the runtime over a stand-in fetch (defined here) in a scratch home: the
 * thread, the throttle, the file, the off switch, a developer build, a failure, a cancel at shutdown.
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

static int fails;
#define EXPECT(c, ...) do { if (!(c)) { fails++; printf("FAIL %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

/* ---- the stand-in fetch */
static volatile int n_fetch, fetch_status = 200, fetch_slow, cancelled;
static const char *fetch_body = "{\"url\":\"x\",\"tag_name\":\"v0.9.13\",\"name\":\"Frodhes 0.9.13\"}";
static char fetch_url[256];
int frp_update_fetch(const char *url, char *buf, size_t cap, size_t *len, int timeout_ms)
{
    size_t n = strlen(fetch_body);
    int waited = 0;
    (void)timeout_ms;
    n_fetch++;
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
int frp_update_open_page(void) { return 0; }

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
    char o[16], big[600];
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
           !strcmp(big, "{\n  \"update_check\": false,\n  \"update_checked\": 1763000001,\n  \"update_latest\": \"0.9.13\"\n}\n"),
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

int main(int argc, char **argv)
{
    pure();
    if (argc > 1) runtime(argv[1]);
    if (fails) { printf("%d failed\n", fails); return 1; }
    printf("ok    update check: versions, the response, the 24 h rule, the settings file%s\n",
           argc > 1 ? "; the runtime (thread, throttle, off switch, developer build, failures, shutdown)" : "");
    return 0;
}
