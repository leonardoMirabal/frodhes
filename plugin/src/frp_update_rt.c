/* SPDX-License-Identifier: GPL-3.0-only */
/* CHECK FOR UPDATES, the runtime (plugin/src/frp_update.h): one state per process (every instance's window shares
 * it), cached in memory and kept in the settings file; the check and the package's download on a thread of their
 * own. The main thread never waits for the network: frp_update_available is a lock and a copy, frp_update_poke a
 * small file read and at most a thread start. */
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
#include <shlobj.h>
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
    int ready;                           /* st.file exists (checked at load and after the download) */
#if !defined(_WIN32)
    pthread_t th;
    int joinable;
#endif
} U = { .st = { .check = 1 }, .own = "0.0.0-dev" };

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
static int exists(const char *path)
{
    FILE *f = path[0] ? frp_fopen(path, "rb") : 0;
    if (f) fclose(f);
    return f != 0;
}
static void ensure_loaded(void)                  /* (main thread: the first call reads the file) */
{
    frp_update_state s;
    int loaded, r;
    LOCK(lk); loaded = U.loaded; UNLOCK(lk);
    if (loaded) return;
    file_load(&s);
    r = exists(s.file);
    LOCK(lk);
    if (!U.loaded) { U.st = s; U.loaded = 1; U.ready = r; }
    UNLOCK(lk);
}

/* ---- the Downloads folder */
int frp_update_downloads_dir(char *out, size_t n)
{
    const char *e = getenv("FRODHES_DOWNLOAD_DIR"), *h;
    int k;
    if (n) out[0] = 0;
    if (e && *e) { k = snprintf(out, n, "%s", e); return k < 0 || (size_t)k >= n ? -1 : 0; }
#if defined(_WIN32)
    {
        static const GUID dl = {0x374de290, 0x123f, 0x4565, {0x91, 0x64, 0x39, 0xc4, 0x92, 0x5e, 0x46, 0x7b}};   /* FOLDERID_Downloads */
        PWSTR p = 0;
        int ok = SHGetKnownFolderPath(&dl, 0, 0, &p) == S_OK && p &&
                 WideCharToMultiByte(CP_UTF8, 0, p, -1, out, (int)n, 0, 0) > 0;
        if (p) CoTaskMemFree(p);
        if (ok) return 0;
        h = getenv("USERPROFILE");
        if (!h || !*h) return -1;
        k = snprintf(out, n, "%s\\Downloads", h);
    }
#else
    h = getenv("HOME");
    if (!h || !*h) return -1;
#if !defined(__APPLE__)
    e = getenv("XDG_DOWNLOAD_DIR");              /* (the user-dirs setting, when the session exports it) */
    if (e && e[0] == '/') { k = snprintf(out, n, "%s", e); return k < 0 || (size_t)k >= n ? -1 : 0; }
#endif
    k = snprintf(out, n, "%s/Downloads", h);
    if (k >= 0 && (size_t)k < n && !frp_dir_usable(out)) k = snprintf(out, n, "%s", h);   /* (no Downloads folder: home) */
#endif
    return k < 0 || (size_t)k >= n ? -1 : 0;
}
/* this system's package of version ver, from the API's answer: downloaded, its sha256 checked against the answer's
 * digest, written into the Downloads folder (a .part file, then renamed): 0 and path, else -1 (nothing left) */
static int download(const char *body, size_t n, const char *ver, char path[1024])
{
    char url[512], sha[65], got[65], dir[1024], name[96], part[1100];
    long long size;
    size_t len = 0;
    char *buf;
    FILE *f;
    int st, ok;
    path[0] = 0;
    if (frp_update_asset(body, n, frp_update_asset_name(FRP_OS_HERE, FRP_UPD_ARCH_HERE), url, &size, sha) ||
        size > (long long)FRP_UPDATE_MAX_PKG || frp_update_downloads_dir(dir, sizeof dir) ||
        frp_update_file_name(FRP_OS_HERE, FRP_UPD_ARCH_HERE, ver, name, sizeof name) ||
        snprintf(path, 1024, "%s%c%s", dir, frp_path_sep(FRP_OS_HERE), name) >= 1024)
        return path[0] = 0, -1;
    if (!(buf = malloc((size_t)size + 1))) return path[0] = 0, -1;
    LOCK(lk); st = U.stop; UNLOCK(lk);
    if (st) { free(buf); return path[0] = 0, -1; }
    st = frp_update_fetch(url, buf, (size_t)size + 1, &len, FRP_UPDATE_DL_TIMEOUT_MS, 1);
    ok = st == 200 && len == (size_t)size;
    if (ok) { frp_sha256_hex(buf, len, got); ok = !strcmp(got, sha); }   /* (a cut or changed file: refused) */
    if (ok) {
        snprintf(part, sizeof part, "%s.part", path);
        ok = (f = frp_fopen(part, "wb")) != 0;
        if (ok) {
            ok = fwrite(buf, 1, len, f) == len;
            if (fclose(f) || !ok || frp_replace(part, path)) { frp_remove(part); ok = 0; }
        }
    }
    free(buf);
    if (!ok) path[0] = 0;
    return ok ? 0 : -1;
}

/* ---- the check's thread */
static void check(void)
{
    char *buf = malloc(FRP_UPDATE_MAX_BODY), tag[16] = "";
    size_t len = 0;
    int stop, st = -1;
    LOCK(lk); stop = U.stop; UNLOCK(lk);
    char path[1024] = "", newer[16];
    if (buf && !stop) st = frp_update_fetch(FRP_UPDATE_URL, buf, FRP_UPDATE_MAX_BODY, &len, FRP_UPDATE_TIMEOUT_MS, 0);
    if (st == 200) frp_update_tag(buf, len, tag);
    if (tag[0]) {                                /* a newer release: its package into Downloads, unless it is there */
        int have, want;
        LOCK(lk);
        want = !U.stop && U.st.check && frp_update_newer(U.own, tag, newer);
        have = !strcmp(U.st.file_ver, tag) && U.ready;
        UNLOCK(lk);
        if (want && !have) download(buf, len, tag, path);
    }
    free(buf);
    LOCK(lk);
    stop = U.stop;
    if (!stop) {                                 /* (done or failed: the next check a day later either way) */
        U.st.checked = (long long)time(0);
        if (tag[0]) memcpy(U.st.latest, tag, sizeof tag);
        if (path[0]) {
            memcpy(U.st.file, path, sizeof path);
            memcpy(U.st.file_ver, tag, sizeof tag);
            U.ready = 1;
        }
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
    {   /* cancel until the thread is done: a cancel between the check's two requests (the answer, then the package)
         * would be missed by the next one, which starts afresh (bounded: 3 s; then the request's own timeout) */
        int i, b = 1;
        for (i = 0; b && i < 600; i++) {
            frp_update_fetch_cancel();
            LOCK(lk); b = U.busy; UNLOCK(lk);
            if (b) {
#if defined(_WIN32)
                Sleep(5);
#else
                struct timespec t = { 0, 5000000 };
                nanosleep(&t, 0);
#endif
            }
        }
    }
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
        if (!f.file[0]) { memcpy(f.file, U.st.file, sizeof f.file); memcpy(f.file_ver, U.st.file_ver, sizeof f.file_ver); }
    }
    U.st = f;
    U.loaded = 1;
    UNLOCK(lk);
    have = exists(f.file);                       /* (the package may have been moved or deleted since) */
    LOCK(lk);
    U.ready = have && !strcmp(U.st.file, f.file);
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
    if (r) r = U.ready && !strcmp(U.st.file_ver, ver) ? FRP_UPD_READY : FRP_UPD_AVAILABLE;
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
int frp_update_open_download(void)
{
    char ver[16], file[1024] = "";
    if (frp_update_available(ver) == FRP_UPD_READY) {
        LOCK(lk); memcpy(file, U.st.file, sizeof file); UNLOCK(lk);
        if (exists(file) && !frp_update_reveal(file)) return 0;
        LOCK(lk); U.ready = 0; UNLOCK(lk);       /* (gone since: the browser's download instead) */
    }
    return frp_update_open_url(frp_update_download_url(FRP_OS_HERE, FRP_UPD_ARCH_HERE));
}
int frp_update_busy(void)
{
    int r;
    LOCK(lk); r = U.busy; UNLOCK(lk);
    return r;
}
