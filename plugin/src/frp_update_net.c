/* SPDX-License-Identifier: GPL-3.0-only */
/* CHECK FOR UPDATES on Windows and Linux (plugin/src/frp_update.h; macOS: frp_update_mac.m).
 *   Windows  WinHTTP (winhttp.dll, part of Windows): TLS verified by the system (Schannel), the system's proxy;
 *            no cookie; redirects only for the package (never https to http). The browser and the package's folder:
 *            ShellExecuteW (shell32).
 *   Linux    libcurl, loaded with dlopen when the system has it (libcurl.so.4, else Debian's libcurl-gnutls.so.4):
 *            the plugin links nothing new and needs nothing installed; without it there is no check. TLS verified
 *            (peer and host name, curl's defaults, set again); redirects only for the package, https only. The browser
 *            and the package's folder: xdg-open, through sh so that neither is a child of the host. */
#include "frp_update.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <shellapi.h>
#include <winhttp.h>

static volatile LONG cancel_flag;

int frp_update_fetch(const char *url, char *buf, size_t cap, size_t *len, int timeout_ms, int follow)
{
    wchar_t wurl[512], host[256], path[512];
    URL_COMPONENTS uc;
    HINTERNET s = 0, c = 0, r = 0;
    DWORD code = 0, sz = sizeof code, dis = WINHTTP_DISABLE_COOKIES | WINHTTP_DISABLE_AUTHENTICATION |
                                             (follow ? 0 : WINHTTP_DISABLE_REDIRECTS);   /* (the redirect policy's
                                                                                          * default: never to http) */
    ULONGLONG deadline = GetTickCount64() + (ULONGLONG)timeout_ms;
    int status = -1;
    size_t got = 0;
    *len = 0;
    if (!cap) return -1;
    buf[0] = 0;
    InterlockedExchange(&cancel_flag, 0);
    if (!MultiByteToWideChar(CP_UTF8, 0, url, -1, wurl, 512)) return -1;
    memset(&uc, 0, sizeof uc);
    uc.dwStructSize = sizeof uc;
    uc.lpszHostName = host; uc.dwHostNameLength = 256;
    uc.lpszUrlPath = path; uc.dwUrlPathLength = 512;
    if (!WinHttpCrackUrl(wurl, 0, 0, &uc) || uc.nScheme != INTERNET_SCHEME_HTTPS) return -1;
    s = WinHttpOpen(L"Frodhes", 4 /* WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY: Windows 8.1 and later */, WINHTTP_NO_PROXY_NAME,
                    WINHTTP_NO_PROXY_BYPASS, 0);
    if (!s) s = WinHttpOpen(L"Frodhes", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!s) return -1;
    WinHttpSetTimeouts(s, timeout_ms, timeout_ms, timeout_ms, timeout_ms);
    if (!(c = WinHttpConnect(s, host, uc.nPort, 0))) goto done;
    if (!(r = WinHttpOpenRequest(c, L"GET", path, 0, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE)))
        goto done;
    WinHttpSetOption(r, WINHTTP_OPTION_DISABLE_FEATURE, &dis, sizeof dis);
    if (cancel_flag || GetTickCount64() > deadline) goto done;
    if (!WinHttpSendRequest(r, follow ? L"Accept: application/octet-stream\r\n" : L"Accept: application/vnd.github+json\r\n",
                            (DWORD)-1L, WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(r, 0))
        goto done;
    if (!WinHttpQueryHeaders(r, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX, &code,
                             &sz, WINHTTP_NO_HEADER_INDEX))
        goto done;
    for (;;) {
        DWORD n = 0;
        if (cancel_flag || GetTickCount64() > deadline) goto done;
        if (got + 1 >= cap) break;               /* (cut: the tag comes first in GitHub's answer) */
        if (!WinHttpReadData(r, buf + got, (DWORD)(cap - 1 - got > 65536 ? 65536 : cap - 1 - got), &n)) goto done;
        if (!n) break;
        got += n;
    }
    buf[got] = 0;
    *len = got;
    status = (int)code;
done:
    if (r) WinHttpCloseHandle(r);
    if (c) WinHttpCloseHandle(c);
    WinHttpCloseHandle(s);
    if (status < 0) buf[0] = 0;
    return status;
}
void frp_update_fetch_cancel(void) { InterlockedExchange(&cancel_flag, 1); }
int frp_update_open_url(const char *url)
{
    wchar_t w[512];
    if (strncmp(url, "https://", 8) || !MultiByteToWideChar(CP_UTF8, 0, url, -1, w, 512)) return -1;
    return (INT_PTR)ShellExecuteW(0, L"open", w, 0, 0, SW_SHOWNORMAL) > 32 ? 0 : -1;
}
int frp_update_reveal(const char *path)          /* Explorer, the zip selected */
{
    wchar_t p[1024], a[1100];
    if (!MultiByteToWideChar(CP_UTF8, 0, path, -1, p, 1024) || wcschr(p, L'"')) return -1;
    _snwprintf(a, 1100, L"/select,\"%ls\"", p);
    a[1099] = 0;
    return (INT_PTR)ShellExecuteW(0, L"open", L"explorer.exe", a, 0, SW_SHOWNORMAL) > 32 ? 0 : -1;
}

#else /* Linux (and any other POSIX system) */
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <spawn.h>
#include <sys/wait.h>

extern char **environ;

/* libcurl's few calls and constants (curl/curl.h; stable ABI since libcurl 7.x, soname 4) */
typedef void CURL;
enum { O_WRITEDATA = 10001, O_URL = 10002, O_WRITEFUNCTION = 20011, O_USERAGENT = 10018, O_HTTPHEADER = 10023,
       O_NOPROGRESS = 43, O_XFERINFODATA = 10057, O_SSL_VERIFYPEER = 64, O_SSL_VERIFYHOST = 81, O_NOSIGNAL = 99,
       O_TIMEOUT_MS = 155, O_CONNECTTIMEOUT_MS = 156, O_XFERINFOFUNCTION = 20219, O_FOLLOWLOCATION = 52,
       O_MAXREDIRS = 68, O_REDIR_PROTOCOLS = 182, O_REDIR_PROTOCOLS_STR = 10319,
       I_RESPONSE_CODE = 0x200002, G_DEFAULT = 3 };
struct curl_slist;
static struct {
    int ok;
    int (*global_init)(long);
    CURL *(*easy_init)(void);
    int (*setopt)(CURL *, int, ...);
    int (*perform)(CURL *);
    int (*getinfo)(CURL *, int, ...);
    void (*cleanup)(CURL *);
    struct curl_slist *(*slist_append)(struct curl_slist *, const char *);
    void (*slist_free)(struct curl_slist *);
} C;
static pthread_once_t once = PTHREAD_ONCE_INIT;
static volatile int cancel_flag;
static void load(void)                           /* (libcurl stays loaded once found: never dlclosed) */
{
    void *h = dlopen("libcurl.so.4", RTLD_NOW | RTLD_LOCAL);
    if (!h) h = dlopen("libcurl-gnutls.so.4", RTLD_NOW | RTLD_LOCAL);
    if (!h) return;
    *(void **)&C.global_init = dlsym(h, "curl_global_init");
    *(void **)&C.easy_init = dlsym(h, "curl_easy_init");
    *(void **)&C.setopt = dlsym(h, "curl_easy_setopt");
    *(void **)&C.perform = dlsym(h, "curl_easy_perform");
    *(void **)&C.getinfo = dlsym(h, "curl_easy_getinfo");
    *(void **)&C.cleanup = dlsym(h, "curl_easy_cleanup");
    *(void **)&C.slist_append = dlsym(h, "curl_slist_append");
    *(void **)&C.slist_free = dlsym(h, "curl_slist_free_all");
    C.ok = C.global_init && C.easy_init && C.setopt && C.perform && C.getinfo && C.cleanup && C.slist_append &&
           C.slist_free && C.global_init(G_DEFAULT) == 0;
}
typedef struct { char *buf; size_t cap, len; } sink_t;
static size_t on_data(char *p, size_t sz, size_t n, void *u)
{
    sink_t *k = u;
    size_t b = sz * n, room = k->cap - 1 - k->len;
    memcpy(k->buf + k->len, p, b < room ? b : room);
    k->len += b < room ? b : room;
    return b;                                    /* (past cap: cut, the transfer goes on to its end) */
}
static int on_progress(void *u, long long a, long long b, long long c, long long d)
{
    (void)u; (void)a; (void)b; (void)c; (void)d;
    return __atomic_load_n(&cancel_flag, __ATOMIC_RELAXED);   /* nonzero: curl ends the transfer */
}
int frp_update_fetch(const char *url, char *buf, size_t cap, size_t *len, int timeout_ms, int follow)
{
    sink_t k = { buf, cap, 0 };
    CURL *h;
    struct curl_slist *hd;
    long code = 0;
    int r;
    *len = 0;
    if (!cap || strncmp(url, "https://", 8)) return -1;
    buf[0] = 0;
    __atomic_store_n(&cancel_flag, 0, __ATOMIC_RELAXED);
    pthread_once(&once, load);
    if (!C.ok || !(h = C.easy_init())) return -1;
    hd = C.slist_append(0, follow ? "Accept: application/octet-stream" : "Accept: application/vnd.github+json");
    C.setopt(h, O_URL, url);
    C.setopt(h, O_USERAGENT, "Frodhes");
    if (hd) C.setopt(h, O_HTTPHEADER, hd);
    C.setopt(h, O_WRITEFUNCTION, on_data);
    C.setopt(h, O_WRITEDATA, &k);
    C.setopt(h, O_NOPROGRESS, 0L);
    C.setopt(h, O_XFERINFOFUNCTION, on_progress);
    C.setopt(h, O_XFERINFODATA, (void *)0);
    C.setopt(h, O_SSL_VERIFYPEER, 1L);
    C.setopt(h, O_SSL_VERIFYHOST, 2L);
    C.setopt(h, O_FOLLOWLOCATION, follow ? 1L : 0L);
    if (follow) {                                /* (https only: the string form from 7.85, the mask before) */
        C.setopt(h, O_MAXREDIRS, 5L);
        if (C.setopt(h, O_REDIR_PROTOCOLS_STR, "https")) C.setopt(h, O_REDIR_PROTOCOLS, 2L /* CURLPROTO_HTTPS */);
    }
    C.setopt(h, O_NOSIGNAL, 1L);                 /* (no SIGALRM in the host's process) */
    C.setopt(h, O_TIMEOUT_MS, (long)timeout_ms);
    C.setopt(h, O_CONNECTTIMEOUT_MS, (long)timeout_ms);
    r = C.perform(h);
    if (r == 0) C.getinfo(h, I_RESPONSE_CODE, &code);
    C.cleanup(h);
    if (hd) C.slist_free(hd);
    if (r != 0 || code <= 0) { buf[0] = 0; return -1; }
    buf[k.len] = 0;
    *len = k.len;
    return (int)code;
}
void frp_update_fetch_cancel(void) { __atomic_store_n(&cancel_flag, 1, __ATOMIC_RELAXED); }
int frp_update_open_url(const char *url)
{
    char *argv[] = { (char *)"sh", (char *)"-c", (char *)"xdg-open \"$1\" >/dev/null 2>&1 &", (char *)"sh",
                     (char *)url, 0 };
    pid_t pid;
    int st;
    if (strncmp(url, "https://", 8)) return -1;
    if (posix_spawn(&pid, "/bin/sh", 0, 0, argv, environ)) return -1;
    while (waitpid(pid, &st, 0) < 0 && errno == EINTR) {}          /* (sh ends at once: xdg-open runs on in the background) */
    return 0;
}
int frp_update_reveal(const char *path)          /* the zip's folder in the file manager */
{
    char *argv[] = { (char *)"sh", (char *)"-c", (char *)"xdg-open \"$(dirname \"$1\")\" >/dev/null 2>&1 &",
                     (char *)"sh", (char *)path, 0 };
    pid_t pid;
    int st;
    if (path[0] != '/') return -1;
    if (posix_spawn(&pid, "/bin/sh", 0, 0, argv, environ)) return -1;
    while (waitpid(pid, &st, 0) < 0 && errno == EINTR) {}
    return 0;
}
#endif
