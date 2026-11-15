/* SPDX-License-Identifier: GPL-3.0-only */
/* CHECK FOR UPDATES through this OS's own network code (plugin/src/frp_update_net.c / frp_update_mac.m), once, for
 * real: the HTTP status, the body's size, the latest release's tag and how long it took. Needs the network; no test
 * runs it on its own. Exit 0 when a tag came back.
 *   frp_update_probe [URL]          (default FRP_UPDATE_URL; https only)
 *   frp_update_probe --cancel       a request cancelled after 100 ms ends at once with -1
 *   frp_update_probe --package      also this system's package of the latest release: downloaded (redirects
 *                                   followed), its size and sha256 checked against the answer's digest; exit 0 when
 *                                   they match (nothing is written) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "frp_paths.h"
#include "frp_update.h"
#if defined(_WIN32)
#include <windows.h>
static double now_s(void) { return (double)GetTickCount64() / 1000.0; }
static DWORD WINAPI canceller(LPVOID a) { (void)a; Sleep(100); frp_update_fetch_cancel(); return 0; }
static void cancel_later(void) { CloseHandle(CreateThread(0, 0, canceller, 0, 0, 0)); }
#else
#include <pthread.h>
#include <unistd.h>
static double now_s(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (double)t.tv_sec + t.tv_nsec * 1e-9; }
static void *canceller(void *a) { (void)a; usleep(100000); frp_update_fetch_cancel(); return 0; }
static void cancel_later(void) { pthread_t t; if (!pthread_create(&t, 0, canceller, 0)) pthread_detach(t); }
#endif

int main(int argc, char **argv)
{
    const char *url = FRP_UPDATE_URL;
    int cancel = argc > 1 && !strcmp(argv[1], "--cancel"), pkg = argc > 1 && !strcmp(argv[1], "--package"), st;
    char *buf = malloc(FRP_UPDATE_MAX_BODY), tag[16] = "";
    size_t len = 0;
    double t0;
    if (argc > 1 && !cancel && !pkg) url = argv[1];
    if (!buf) return 2;
    if (cancel) cancel_later();
    t0 = now_s();
    st = frp_update_fetch(url, buf, FRP_UPDATE_MAX_BODY, &len, FRP_UPDATE_TIMEOUT_MS, 0);
    if (st == 200) frp_update_tag(buf, len, tag);
    printf("status %d, %zu bytes, %.2f s, tag %s\n", st, len, now_s() - t0, tag[0] ? tag : "(none)");
    if (pkg) {
        const char *name = frp_update_asset_name(FRP_OS_HERE, FRP_UPD_ARCH_HERE);
        char au[512], sha[65], got[65] = "";
        long long size = 0;
        char *p;
        size_t n = 0;
        int ok;
        if (st != 200 || frp_update_asset(buf, len, name, au, &size, sha)) { printf("%s: no asset with a digest\n", name); free(buf); return 1; }
        if (!(p = malloc((size_t)size + 1))) { free(buf); return 2; }
        t0 = now_s();
        st = frp_update_fetch(au, p, (size_t)size + 1, &n, FRP_UPDATE_DL_TIMEOUT_MS, 1);
        if (st == 200) frp_sha256_hex(p, n, got);
        ok = st == 200 && n == (size_t)size && !strcmp(got, sha);
        printf("%s: status %d, %zu of %lld bytes, %.2f s, sha256 %s %s\n", name, st, n, size, now_s() - t0, got,
               ok ? "= the digest" : "!= the digest");
        free(p);
        free(buf);
        return ok ? 0 : 1;
    }
    free(buf);
    if (cancel) return st == -1 && now_s() - t0 < 2.0 ? 0 : 1;
    return tag[0] ? 0 : 1;
}
