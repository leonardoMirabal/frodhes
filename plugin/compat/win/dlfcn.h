/* SPDX-License-Identifier: GPL-3.0-only */
/* Windows: dlopen / dlsym / dlerror over LoadLibrary, for the test hosts (plugin/tests/clap_render.c). On the include
 * path of the Windows build only. */
#ifndef FRODHES_PLUGIN_WIN_DLFCN_H
#define FRODHES_PLUGIN_WIN_DLFCN_H
#include <stdio.h>
#include <windows.h>
#define RTLD_NOW 2
#define RTLD_LAZY 1
#define RTLD_LOCAL 0
static char frp_dl_err[64];
static void *dlopen(const char *path, int mode)
{
    HMODULE h;
    (void)mode;
    h = LoadLibraryA(path);
    if (!h) snprintf(frp_dl_err, sizeof frp_dl_err, "LoadLibrary error %lu", (unsigned long)GetLastError());
    return (void *)h;
}
static void *dlsym(void *h, const char *name) { return (void *)GetProcAddress((HMODULE)h, name); }
static int dlclose(void *h) { return FreeLibrary((HMODULE)h) ? 0 : -1; }
static const char *dlerror(void) { return frp_dl_err; }
#endif
