/* SPDX-License-Identifier: GPL-3.0-only */
/* The user's folders per OS and the file calls that differ on Windows (plugin/src/frp_paths.h). */
#include "frp_paths.h"
#include <stdlib.h>
#include <string.h>
#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <dirent.h>
#include <errno.h>
#include <pwd.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

static int set(const char *s) { return s && *s; }
char frp_path_sep(int os) { return os == FRP_OS_WINDOWS ? '\\' : '/'; }

/* out = a + sep + b (+ sep + c): -1 when it does not fit */
static int join(char *out, size_t n, char sep, const char *a, const char *b, const char *c)
{
    size_t la = strlen(a);
    int k;
    while (la > 1 && (a[la - 1] == '/' || (sep == '\\' && a[la - 1] == '\\'))) la--;   /* (no doubled separator) */
    k = c ? snprintf(out, n, "%.*s%c%s%c%s", (int)la, a, sep, b, sep, c) : snprintf(out, n, "%.*s%c%s", (int)la, a, sep, b);
    if (k < 0 || (size_t)k >= n) { if (n) out[0] = 0; return -1; }
    return 0;
}

int frp_path_data(int os, const frp_env_t *e, char *out, size_t n)
{
    if (n) out[0] = 0;
    if (os == FRP_OS_WINDOWS) {
        if (set(e->appdata)) return join(out, n, '\\', e->appdata, "Frodhes", 0);
        if (set(e->home)) return join(out, n, '\\', e->home, "AppData\\Roaming", "Frodhes");
        return -1;
    }
    if (os == FRP_OS_LINUX) {
        if (set(e->xdg_data) && e->xdg_data[0] == '/') return join(out, n, '/', e->xdg_data, "frodhes", 0);   /* (the
                                          * spec: a relative XDG_DATA_HOME is ignored) */
        if (set(e->home)) return join(out, n, '/', e->home, ".local/share", "frodhes");
        return -1;
    }
    if (set(e->sandbox) && set(e->real_home)) {   /* the App Sandbox: the account's own folder if this host may use it */
        char real[1024];
        if (!join(real, sizeof real, '/', e->real_home, "Library/Application Support", "Frodhes") && e->usable &&
            e->usable(real)) {
            if (strlen(real) >= n) return -1;
            memcpy(out, real, strlen(real) + 1);
            return 0;
        }
    }
    if (set(e->home)) return join(out, n, '/', e->home, "Library/Application Support", "Frodhes");   /* (in a sandbox:
                                          * HOME is the container's) */
    return -1;
}
int frp_path_presets(int os, const frp_env_t *e, char *out, size_t n)
{
    char d[1024];
    if (set(e->preset_dir)) {
        if (strlen(e->preset_dir) >= n) { if (n) out[0] = 0; return -1; }
        memcpy(out, e->preset_dir, strlen(e->preset_dir) + 1);
        return 0;
    }
    if (frp_path_data(os, e, d, sizeof d)) { if (n) out[0] = 0; return -1; }
    return join(out, n, frp_path_sep(os), d, os == FRP_OS_LINUX ? "presets" : "Presets", 0);
}
int frp_path_settings(int os, const frp_env_t *e, char *out, size_t n)
{
    char d[1024];
    if (frp_path_data(os, e, d, sizeof d)) { if (n) out[0] = 0; return -1; }
    return join(out, n, frp_path_sep(os), d, "settings.json", 0);
}
size_t frp_path_root(int os, const char *p)
{
    if (os != FRP_OS_WINDOWS) return p[0] == '/' ? 1u : 0u;
#define SEP(c) ((c) == '\\' || (c) == '/')
    if (SEP(p[0]) && SEP(p[1])) {        /* \\server\share\ (or \\?\C:\) */
        size_t i = 2, parts = 0;
        while (p[i] && parts < 2) { if (SEP(p[i])) parts++; i++; }
        return i;
    }
    if (((p[0] | 32) >= 'a' && (p[0] | 32) <= 'z') && p[1] == ':') return SEP(p[2]) ? 3u : 2u;
    return SEP(p[0]) ? 1u : 0u;
#undef SEP
}

#if defined(_WIN32)
/* UTF-8 -> UTF-16 (a static buffer per call site is not enough: two at once for the replace) */
static int wide(const char *s, wchar_t *w, int n) { return MultiByteToWideChar(CP_UTF8, 0, s, -1, w, n) > 0 ? 0 : -1; }
static const char *utf8_env(const wchar_t *name)
{
    wchar_t *v = _wgetenv(name);
    char *o;
    int k;
    if (!v || !*v) return 0;
    k = WideCharToMultiByte(CP_UTF8, 0, v, -1, 0, 0, 0, 0);
    if (k <= 0 || !(o = malloc((size_t)k))) return 0;
    WideCharToMultiByte(CP_UTF8, 0, v, -1, o, k, 0, 0);
    return o;                            /* (kept for the process's life, as getenv's) */
}
void frp_env_here(frp_env_t *e)
{
    memset(e, 0, sizeof *e);
    e->home = utf8_env(L"USERPROFILE");
    e->appdata = utf8_env(L"APPDATA");
    e->preset_dir = utf8_env(L"FRODHES_PRESET_DIR");
    e->usable = frp_dir_usable;
}
int frp_dir_usable(const char *dir)
{
    wchar_t w[1024];
    DWORD a;
    if (wide(dir, w, 1024)) return 0;
    a = GetFileAttributesW(w);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY) && !(a & FILE_ATTRIBUTE_READONLY);
}
static int mk1(const char *p)
{
    wchar_t w[1024];
    if (wide(p, w, 1024)) return -1;
    if (CreateDirectoryW(w, 0) || GetLastError() == ERROR_ALREADY_EXISTS) return 0;
    return -1;
}
FILE *frp_fopen(const char *path, const char *mode)
{
    wchar_t w[1024], m[8];
    if (wide(path, w, 1024) || wide(mode, m, 8)) return 0;
    return _wfopen(w, m);
}
int frp_replace(const char *tmp, const char *path)
{
    wchar_t a[1024], b[1024];
    if (wide(tmp, a, 1024) || wide(path, b, 1024)) return -1;
    return MoveFileExW(a, b, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) ? 0 : -1;
}
int frp_remove(const char *path)
{
    wchar_t w[1024];
    if (wide(path, w, 1024)) return -1;
    return DeleteFileW(w) ? 0 : -1;
}
int frp_list_dir(const char *dir, int (*fn)(const char *name, void *u), void *u)
{
    char pat[1100];
    wchar_t w[1100];
    WIN32_FIND_DATAW fd;
    HANDLE h;
    if (snprintf(pat, sizeof pat, "%s\\*", dir) >= (int)sizeof pat || wide(pat, w, 1100)) return -1;
    if ((h = FindFirstFileW(w, &fd)) == INVALID_HANDLE_VALUE) {   /* (no folder yet: no files) */
        DWORD er = GetLastError();
        return er == ERROR_FILE_NOT_FOUND || er == ERROR_PATH_NOT_FOUND ? 0 : -1;
    }
    do {
        char nm[1024];
        if (!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
        if (WideCharToMultiByte(CP_UTF8, 0, fd.cFileName, -1, nm, sizeof nm, 0, 0) <= 0) continue;
        if (fn(nm, u)) break;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return 0;
}
#else
void frp_env_here(frp_env_t *e)
{
    memset(e, 0, sizeof *e);
    e->home = getenv("HOME");
    e->xdg_data = getenv("XDG_DATA_HOME");
    e->sandbox = getenv("APP_SANDBOX_CONTAINER_ID");
    e->preset_dir = getenv("FRODHES_PRESET_DIR");
    e->usable = frp_dir_usable;
    {
        struct passwd *pw = getpwuid(getuid());   /* (the account's home: outside a sandbox's container) */
        static char real[1024];
        if (pw && pw->pw_dir && strlen(pw->pw_dir) < sizeof real) { strcpy(real, pw->pw_dir); e->real_home = real; }
    }
    if (!e->home || !*e->home) e->home = e->real_home;
}
int frp_dir_usable(const char *dir)
{
    struct stat st;
    return !stat(dir, &st) && S_ISDIR(st.st_mode) && !access(dir, W_OK | X_OK);
}
static int mk1(const char *p)
{
    struct stat st;
    if (!mkdir(p, 0755)) return 0;
    return errno == EEXIST && !stat(p, &st) && S_ISDIR(st.st_mode) ? 0 : -1;
}
FILE *frp_fopen(const char *path, const char *mode) { return fopen(path, mode); }
int frp_replace(const char *tmp, const char *path) { return rename(tmp, path) ? -1 : 0; }
int frp_remove(const char *path) { return remove(path) ? -1 : 0; }
int frp_list_dir(const char *dir, int (*fn)(const char *name, void *u), void *u)
{
    DIR *d = opendir(dir);
    struct dirent *de;
    if (!d) return errno == ENOENT ? 0 : -1;   /* (no folder yet: no files) */
    while ((de = readdir(d)) != 0) {
        if (!strcmp(de->d_name, ".") || !strcmp(de->d_name, "..")) continue;
        if (fn(de->d_name, u)) break;
    }
    closedir(d);
    return 0;
}
#endif

int frp_mkdirs(const char *path)
{
    char t[1024];
    size_t n = strlen(path), i;
    int win = FRP_OS_HERE == FRP_OS_WINDOWS;
    if (!n || n >= sizeof t) return -1;
    memcpy(t, path, n + 1);
    for (i = frp_path_root(FRP_OS_HERE, t); i <= n; i++)
        if (t[i] == '/' || (win && t[i] == '\\') || t[i] == 0) {
            char c = t[i];
            if (i == 0 || t[i - 1] == '/' || (win && t[i - 1] == '\\')) continue;   /* (a doubled separator) */
            t[i] = 0;
            if (mk1(t)) return -1;
            t[i] = c;
        }
    return 0;
}
