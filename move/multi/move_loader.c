/* SPDX-License-Identifier: GPL-3.0-only */
/* PROTOTYPE (move-multi cost gate, not a product file): several Frodhes in several Schwung slots, each with its own
 * globals. The app is one set of globals a process (move/move_bridge.h). Schwung dlopen()s <module>/dsp.so with
 * RTLD_NOW | RTLD_LOCAL, and glibc hands every slot the same loaded copy. This dsp.so is a thin loader: the real
 * plugin (move/move_plugin.c, unchanged) ships beside it as engine.so, and each instance gets a private copy of it,
 * <base>/<k>/dsp.so, loaded with dlopen (a real copy: another inode, so another link map and other globals; a hard
 * link or a symlink returns the loaded copy, move/multi/probe). The engine's module_dir is <base>/<k>, so its data
 * folder (store.bin, boot.txt, tap/, cpu-ring.log) is that folder too (mb_pick_dir: no "/modules/" in the path).
 *
 * A pool: an engine copy is never dlclose'd (its app keeps running after its last destroy_instance, as today, and
 * a later create continues it). create_instance takes a free copy, else loads a new one (up to ML_MAX). A re-pick
 * creates the new instance before it destroys the old, so a slot can hold two copies for a moment.
 * create / destroy run on the host's loader thread (file copies and dlopen there: Gate P holds, nothing of this on
 * the audio path); the callbacks only forward through a pointer.
 * Extra read-only key: ml_inst (the copy's index k). */
#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "../vendor/plugin_api_v1.h"

#define ML_EXPORT __attribute__((visibility("default")))
#define ML_MAX 8
#define ML_BASE "/data/UserData/schwung/module_data/frodhes/inst"

typedef struct { void *h; plugin_api_v2_t *(*init)(const host_api_v1_t *); int busy, k; } ml_eng_t;
typedef struct { ml_eng_t *e; plugin_api_v2_t *api; void *inst; } ml_inst_t;
static ml_eng_t ml_pool[ML_MAX];
static pthread_mutex_t ml_mx = PTHREAD_MUTEX_INITIALIZER;
static const host_api_v1_t *_Atomic ml_host;   /* the table of the latest init call (the slot about to create) */

static int ml_mkdirs(const char *p)
{
    char b[512];
    snprintf(b, sizeof b, "%s", p);
    for (char *s = b + 1; *s; s++) if (*s == '/') { *s = 0; (void)mkdir(b, 0755); *s = '/'; }
    return mkdir(b, 0755) == 0 || errno == EEXIST ? 0 : -1;
}

/* engine.so -> dir/dsp.so when missing or not the same size and mtime (a tmp file, then a rename: never a mapped
 * file written in place) */
static int ml_copy(const char *src, const char *dir)
{
    char dst[700], tmp[700];
    static char buf[1 << 16];
    struct stat ss, ds;
    int i, o;
    ssize_t n;
    if (stat(src, &ss)) return -1;
    snprintf(dst, sizeof dst, "%s/dsp.so", dir);
    if (!stat(dst, &ds) && ds.st_size == ss.st_size && ds.st_mtim.tv_sec == ss.st_mtim.tv_sec) return 0;
    snprintf(tmp, sizeof tmp, "%s/.dsp.so.tmp", dir);
    i = open(src, O_RDONLY | O_CLOEXEC);
    o = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0755);
    if (i < 0 || o < 0) { if (i >= 0) close(i); if (o >= 0) close(o); return -1; }
    while ((n = read(i, buf, sizeof buf)) > 0) if (write(o, buf, (size_t)n) != n) { n = -1; break; }
    {
        struct timespec t[2] = { ss.st_atim, ss.st_mtim };
        (void)futimens(o, t);
    }
    close(i);
    if (close(o) || n < 0) { unlink(tmp); return -1; }
    return rename(tmp, dst);
}

static ml_eng_t *ml_take(const char *module_dir)
{
    char src[600], dir[600], so[700];
    struct stat st;
    int k;
    for (k = 0; k < ML_MAX; k++) if (ml_pool[k].h && !ml_pool[k].busy) { ml_pool[k].busy = 1; return &ml_pool[k]; }
    for (k = 0; k < ML_MAX && ml_pool[k].h; k++) {}
    if (k == ML_MAX) return 0;
    snprintf(src, sizeof src, "%s/engine.so", module_dir);
    if (stat("/data/UserData/schwung", &st) == 0) snprintf(dir, sizeof dir, "%s/%d", ML_BASE, k);
    else snprintf(dir, sizeof dir, "%s/inst/%d", module_dir, k);
    if (ml_mkdirs(dir) || ml_copy(src, dir)) return 0;
    snprintf(so, sizeof so, "%s/dsp.so", dir);
    ml_pool[k].h = dlopen(so, RTLD_NOW | RTLD_LOCAL);
    if (!ml_pool[k].h) return 0;
    ml_pool[k].init = (plugin_api_v2_t *(*)(const host_api_v1_t *))dlsym(ml_pool[k].h, MOVE_PLUGIN_INIT_V2_SYMBOL);
    if (!ml_pool[k].init) { dlclose(ml_pool[k].h); ml_pool[k].h = 0; return 0; }
    ml_pool[k].k = k;
    ml_pool[k].busy = 1;
    return &ml_pool[k];
}

static char ml_dirs[ML_MAX][600];
static long ml_us(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1000000L + t.tv_nsec / 1000; }
static void ml_note(int k, long take_us, long create_us, int fresh)  /* <dir>/load.txt: the loader's own timings */
{
    char p[700], l[160];
    int fd, n;
    snprintf(p, sizeof p, "%s/load.txt", ml_dirs[k]);
    n = snprintf(l, sizeof l, "inst %d %s copy+dlopen %ld us create %ld us\n", k, fresh ? "new" : "reused", take_us, create_us);
    fd = open(p, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    if (fd >= 0) { if (write(fd, l, (size_t)n) < 0) {} close(fd); }
}
static void *ml_create(const char *module_dir, const char *json_defaults)
{
    ml_inst_t *in = calloc(1, sizeof *in);
    ml_eng_t *e;
    struct stat st;
    if (!in) return 0;
    long t0 = ml_us(), t1;
    int fresh;
    pthread_mutex_lock(&ml_mx);
    for (fresh = 1, t1 = 0; t1 < ML_MAX; t1++) if (ml_pool[t1].h && !ml_pool[t1].busy) fresh = 0;
    e = ml_take(module_dir);
    t1 = ml_us();
    if (!e) { pthread_mutex_unlock(&ml_mx); free(in); return 0; }
    in->e = e;
    in->api = e->init(atomic_load(&ml_host));            /* (the engine keeps this slot's host table) */
    if (stat("/data/UserData/schwung", &st) == 0) snprintf(ml_dirs[e->k], sizeof ml_dirs[0], "%s/%d", ML_BASE, e->k);
    else snprintf(ml_dirs[e->k], sizeof ml_dirs[0], "%s/inst/%d", module_dir, e->k);
    in->inst = in->api && in->api->create_instance ? in->api->create_instance(ml_dirs[e->k], json_defaults) : 0;
    if (!in->inst) { e->busy = 0; pthread_mutex_unlock(&ml_mx); free(in); return 0; }
    ml_note(e->k, t1 - t0, ml_us() - t1, fresh);
    pthread_mutex_unlock(&ml_mx);
    return in;
}
static void ml_destroy(void *p)
{
    ml_inst_t *in = p;
    if (!in) return;
    pthread_mutex_lock(&ml_mx);
    in->api->destroy_instance(in->inst);                  /* (the engine's worker ends; the copy stays loaded) */
    in->e->busy = 0;
    pthread_mutex_unlock(&ml_mx);
    free(in);
}
static void ml_on_midi(void *p, const uint8_t *m, int len, int src) { ml_inst_t *in = p; in->api->on_midi(in->inst, m, len, src); }
static void ml_set_param(void *p, const char *k, const char *v) { ml_inst_t *in = p; in->api->set_param(in->inst, k, v); }
static int ml_get_param(void *p, const char *k, char *b, int n)
{
    ml_inst_t *in = p;
    if (!in) return -1;
    if (!strcmp(k, "ml_inst")) return snprintf(b, (size_t)n, "%d", in->e->k);
    return in->api->get_param(in->inst, k, b, n);
}
static int ml_get_error(void *p, char *b, int n) { ml_inst_t *in = p; return in->api->get_error ? in->api->get_error(in->inst, b, n) : 0; }
static void ml_render(void *p, int16_t *out, int frames) { ml_inst_t *in = p; in->api->render_block(in->inst, out, frames); }

static plugin_api_v2_t ml_api = {
    .api_version = MOVE_PLUGIN_API_VERSION_2,
    .create_instance = ml_create, .destroy_instance = ml_destroy, .on_midi = ml_on_midi,
    .set_param = ml_set_param, .get_param = ml_get_param, .get_error = ml_get_error, .render_block = ml_render,
};

ML_EXPORT plugin_api_v2_t *move_plugin_init_v2(const host_api_v1_t *host)
{
    atomic_store(&ml_host, host);
    return &ml_api;
}
