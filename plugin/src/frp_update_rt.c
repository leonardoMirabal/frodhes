/* SPDX-License-Identifier: GPL-3.0-only */
/* CHECK FOR UPDATES, the runtime (plugin/src/frp_update.h): one state per process (every instance's window shares
 * it), cached in memory and kept in the settings file; the check on a thread of its own. The main thread never
 * waits for the network: frp_update_available is a lock and a copy, frp_update_poke a small file read and at most
 * a thread start. */
#include "frp_update.h"
#include "frp_paths.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
static SRWLOCK lk = SRWLOCK_INIT, flk = SRWLOCK_INIT;
#define LOCK(l) AcquireSRWLockExclusive(&(l))
#define UNLOCK(l) ReleaseSRWLockExclusive(&(l))
#define PID() ((long)GetCurrentProcessId())
#else
#include <pthread.h>
#include <unistd.h>
static pthread_mutex_t lk = PTHREAD_MUTEX_INITIALIZER, flk = PTHREAD_MUTEX_INITIALIZER;
#define LOCK(l) pthread_mutex_lock(&(l))
#define UNLOCK(l) pthread_mutex_unlock(&(l))
#define PID() ((long)getpid())
#endif

static struct {
    int loaded;                          /* st holds the file's (or the defaults when there is none) */
    frp_update_state st;
    char own[24];                        /* the plugin's version text */
    int busy;                            /* a check under way */
    int stop;                            /* the entry is going away: no new check, the one under way is dropped */
    int unsaved;                         /* the file could not be written: this process's own state counts */
    int env_off;                         /* FRODHES_UPDATE_CHECK=0 in the environment: never a check, never a notice */
#if !defined(_WIN32)
    pthread_t th;
    int joinable;
#endif
} U = { .st = { 1, 0, "" }, .own = "0.0.0-dev" };

/* ---- the settings file (lk is never held across file calls; flk orders the writers) */
static int settings_path(char *out, size_t n)
{
    frp_env_t e;
    frp_env_here(&e);
    return frp_path_settings(FRP_OS_HERE, &e, out, n);
}
static char *read_text(const char *path)        /* the file's text (64 KB at most), NULL: none */
{
    FILE *f = frp_fopen(path, "rb");
    char *t;
    size_t n;
    if (!f) return 0;
    t = malloc(65537);
    n = t ? fread(t, 1, 65536, f) : 0;
    fclose(f);
    if (!t) return 0;
    if (n == 65536) { free(t); return 0; }       /* (too long to be ours: not read, not written) */
    t[n] = 0;
    return t;
}
/* the file's state: 1 read, 0 no file (s: the defaults), -1 a file that is not ours to rewrite */
static int file_load(frp_update_state *s)
{
    char path[1024], *t;
    int r;
    frp_update_state_default(s);
    if (settings_path(path, sizeof path) || !(t = read_text(path))) return 0;
    r = frp_update_state_read(t, s) ? -1 : 1;
    free(t);
    return r;
}
static int file_save(void)                       /* the state in memory now into the file (the last writer has the
                                                  * latest: it reads the state under flk): 0 ok */
{
    frp_update_state s;
    char path[1024], dir[1024], tmp[1100], *old, *out;
    frp_env_t e;
    FILE *f;
    int r = -1;
    frp_env_here(&e);
    if (frp_path_settings(FRP_OS_HERE, &e, path, sizeof path) || frp_path_data(FRP_OS_HERE, &e, dir, sizeof dir)) return -1;
    LOCK(flk);
    LOCK(lk); s = U.st; UNLOCK(lk);
    old = read_text(path);
    out = malloc(70000);
    if (out && !frp_update_state_write(old, &s, out, 70000) && !frp_mkdirs(dir)) {
        snprintf(tmp, sizeof tmp, "%s.%ld.tmp", path, PID());
        if ((f = frp_fopen(tmp, "wb"))) {
            size_t n = strlen(out);
            int ok = fwrite(out, 1, n, f) == n;
            if (fclose(f) || !ok || frp_replace(tmp, path)) frp_remove(tmp);
            else r = 0;
        }
    }
    free(old);
    free(out);
    LOCK(lk); U.unsaved = r != 0; UNLOCK(lk);
    UNLOCK(flk);
    return r;
}
static void ensure_loaded(void)                  /* (main thread: the first call reads the file) */
{
    frp_update_state s;
    int loaded;
    LOCK(lk); loaded = U.loaded; UNLOCK(lk);
    if (loaded) return;
    file_load(&s);
    LOCK(lk);
    if (!U.loaded) { U.st = s; U.loaded = 1; }
    UNLOCK(lk);
}

/* ---- the check's thread */
static void check(void)
{
    char *buf = malloc(FRP_UPDATE_MAX_BODY), tag[16] = "";
    size_t len = 0;
    int stop, st = -1;
    LOCK(lk); stop = U.stop; UNLOCK(lk);
    if (buf && !stop) st = frp_update_fetch(FRP_UPDATE_URL, buf, FRP_UPDATE_MAX_BODY, &len, FRP_UPDATE_TIMEOUT_MS);
    if (st == 200) frp_update_tag(buf, len, tag);
    free(buf);
    LOCK(lk);
    stop = U.stop;
    if (!stop) {                                 /* (done or failed: the next check a day later either way) */
        U.st.checked = (long long)time(0);
        if (tag[0]) memcpy(U.st.latest, tag, sizeof tag);
    }
    UNLOCK(lk);
    if (!stop) file_save();
    LOCK(lk); U.busy = 0; UNLOCK(lk);
}
#if defined(_WIN32)
/* the thread holds a reference on this module (GetModuleHandleEx) and gives it back as it ends
 * (FreeLibraryAndExitThread): a host that unloads the plugin while a check runs never pulls the code from under it */
static DWORD WINAPI thread_main(LPVOID mod)
{
    check();
    FreeLibraryAndExitThread((HMODULE)mod, 0);
    return 0;
}
static int start_thread(void)
{
    HMODULE mod = 0;
    HANDLE h;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS, (LPCWSTR)(void *)&thread_main, &mod)) return -1;
    if (!(h = CreateThread(0, 256 * 1024, thread_main, mod, 0, 0))) { FreeLibrary(mod); return -1; }
    CloseHandle(h);
    return 0;
}
#else
static void *thread_main(void *a) { (void)a; check(); return 0; }
static int start_thread(void)                    /* (lk held) */
{
    pthread_attr_t at;
    int r;
    if (U.joinable) { pthread_join(U.th, 0); U.joinable = 0; }   /* (the last check's thread: it has ended, busy is 0) */
    pthread_attr_init(&at);
    pthread_attr_setstacksize(&at, 256 * 1024);
    r = pthread_create(&U.th, &at, thread_main, 0);
    pthread_attr_destroy(&at);
    if (r) return -1;
    U.joinable = 1;
    return 0;
}
#endif

/* ---- the calls */
void frp_update_init(const char *own_version)
{
    const char *e = getenv("FRODHES_UPDATE_CHECK");
    LOCK(lk);
    snprintf(U.own, sizeof U.own, "%s", own_version ? own_version : "");
    U.stop = 0;
    U.env_off = e && !strcmp(e, "0");
    UNLOCK(lk);
}
void frp_update_shutdown(void)
{
#if !defined(_WIN32)
    int join;
#endif
    LOCK(lk);
    U.stop = 1;
#if !defined(_WIN32)
    join = U.joinable;
    U.joinable = 0;
#endif
    UNLOCK(lk);
    frp_update_fetch_cancel();
#if !defined(_WIN32)
    if (join) pthread_join(U.th, 0);             /* (cancelled: at once; at worst the request's own timeout) */
#endif
}
void frp_update_poke(void)
{
    frp_update_state f;
    int have;
    LOCK(lk);
    have = U.busy || U.stop || U.env_off || !frp_ver_is_release(U.own);   /* (a developer build never checks) */
    UNLOCK(lk);
    if (have) return;
    have = file_load(&f);                        /* (another process may have checked since: its result counts) */
    LOCK(lk);
    if (U.loaded && (have <= 0 || U.unsaved)) {  /* (no file, not ours, or not writable: this process's own state;
                                                  * else the file is the truth, another process's check included) */
        f.check = U.st.check;
        if (U.st.checked > f.checked) { f.checked = U.st.checked; memcpy(f.latest, U.st.latest, sizeof f.latest); }
    }
    U.st = f;
    U.loaded = 1;
    if (!U.busy && !U.stop && frp_update_plan(&U.st, U.own, (long long)time(0))) {
        U.busy = 1;
        if (start_thread()) U.busy = 0;
    }
    UNLOCK(lk);
}
int frp_update_available(char ver[16])
{
    int r;
    ensure_loaded();
    LOCK(lk);
    r = U.st.check && !U.env_off && frp_update_newer(U.own, U.st.latest, ver);
    UNLOCK(lk);
    if (!r) ver[0] = 0;
    return r;
}
int frp_update_enabled(void)
{
    int r;
    ensure_loaded();
    LOCK(lk); r = U.st.check; UNLOCK(lk);
    return r;
}
void frp_update_set_enabled(int on)
{
    ensure_loaded();
    LOCK(lk);
    U.st.check = on != 0;
    UNLOCK(lk);
    file_save();
    if (on) frp_update_poke();
}
int frp_update_busy(void)
{
    int r;
    LOCK(lk); r = U.busy; UNLOCK(lk);
    return r;
}
