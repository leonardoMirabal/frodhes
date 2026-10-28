/* SPDX-License-Identifier: GPL-3.0-only */
/* The glibc probe (move-multi cost gate): which ways of loading one .so twice give two sets of globals on the
 * Move's glibc. argv[1] = a directory holding lib.so; the probe makes a copy, a hard link and a symlink beside it
 * and loads each with dlopen(RTLD_NOW | RTLD_LOCAL), as Schwung loads a dsp.so, plus the original twice with
 * dlmopen(LM_ID_NEWLM). For each: the handle, the global's address, and whether a write through the first load
 * shows through it (shared = the same globals). */
#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <gnu/libc-version.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>

typedef int *(*glob_fn)(void);
static int *glob_of(void *h) { glob_fn f = h ? (glob_fn)dlsym(h, "probe_global") : 0; return f ? f() : 0; }

static int copy_file(const char *a, const char *b)
{
    char buf[65536];
    FILE *i = fopen(a, "rb"), *o = fopen(b, "wb");
    size_t n;
    if (!i || !o) return -1;
    while ((n = fread(buf, 1, sizeof buf, i)) > 0) fwrite(buf, 1, n, o);
    fclose(i); fclose(o);
    return 0;
}

int main(int argc, char **argv)
{
    char orig[512], cp[512], hl[512], sl[512];
    void *h0, *h;
    int *g0;
    const char *names[3]; const char *paths[3];
    if (argc < 2) return 2;
    snprintf(orig, sizeof orig, "%s/lib.so", argv[1]);
    snprintf(cp, sizeof cp, "%s/lib_copy.so", argv[1]);
    snprintf(hl, sizeof hl, "%s/lib_hard.so", argv[1]);
    snprintf(sl, sizeof sl, "%s/lib_sym.so", argv[1]);
    unlink(cp); unlink(hl); unlink(sl);
    if (copy_file(orig, cp) || link(orig, hl) || symlink("lib.so", sl)) { perror("setup"); return 1; }
    printf("glibc %s\n", gnu_get_libc_version());
    h0 = dlopen(orig, RTLD_NOW | RTLD_LOCAL);
    g0 = glob_of(h0);
    if (!g0) { printf("dlopen orig failed: %s\n", dlerror()); return 1; }
    *g0 = 42;
    printf("orig     handle %p global %p\n", h0, (void *)g0);
    h = dlopen(orig, RTLD_NOW | RTLD_LOCAL);
    printf("same     handle %p global %p shared=%d\n", h, (void *)glob_of(h), glob_of(h) == g0);
    names[0] = "copy"; paths[0] = cp; names[1] = "hardlink"; paths[1] = hl; names[2] = "symlink"; paths[2] = sl;
    for (int i = 0; i < 3; i++) {
        int *g;
        h = dlopen(paths[i], RTLD_NOW | RTLD_LOCAL);
        g = glob_of(h);
        printf("%-8s handle %p global %p shared=%d val=%d\n", names[i], h, (void *)g, g == g0, g ? *g : -1);
    }
    for (int i = 0; i < 2; i++) {
        int *g;
        h = dlmopen(LM_ID_NEWLM, orig, RTLD_NOW | RTLD_LOCAL);
        g = glob_of(h);
        printf("dlmopen%d handle %p global %p shared=%d val=%d %s\n", i, h, (void *)g, g == g0, g ? *g : -1,
               h ? "" : dlerror());
    }
    return 0;
}
