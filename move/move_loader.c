/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes on the Move: the module's dsp.so, a thin loader that gives every Schwung slot its own Frodhes.
 *
 * The app is one set of globals a loaded file (move/move_plugin.c, move/move_bridge.h). Schwung dlopen()s
 * <module>/dsp.so with RTLD_NOW | RTLD_LOCAL, and glibc hands every slot the same loaded copy, so one file can play in
 * one slot only. The real plugin ships beside this loader as engine.so, and each instance gets a private copy of it,
 * <inst>/<k>/dsp-<crc>.so, loaded with dlopen: a real copy (another inode: another link map and other globals; a hard link
 * or a symlink returns the copy already loaded, move/multi/probe). <inst> is
 * /data/UserData/schwung/module_data/frodhes/inst on the Move (a module install never deletes it), else
 * <module_dir>/inst (the host tests). The engine's module_dir is <inst>/<k>: its own files (boot.txt, tap/,
 * cpu-ring.log, stats.log) go there, the shared ones (store.bin: the settings and the user presets U01-U16) one level
 * up, in the data folder (move_bridge.h mb_pick_dir).
 *
 * create_instance takes the lowest free index k, brings <inst>/<k>/dsp-<crc>.so up to date with engine.so (size and
 * CRC-32: a changed engine.so, an update, is copied again under its new name; a temporary file then a rename, never a
 * mapped file written in place; the older copies in the folder are deleted), dlopens it and creates the engine's instance. destroy_instance destroys it (its worker writes what is
 * staged and ends), then dlclose()s the copy: its memory is freed (move_plugin.c mv_fini), its index free again
 * (inside Schwung 1.8.1 the copy stays mapped after dlclose: boot.txt's starts count on, the next create of k
 * continues that app, as one file always did. glibc finds an object already loaded by its name, so the copy's name
 * carries its CRC: an updated engine.so gets a new name and really loads). A
 * re-pick creates the new instance before it destroys the old, so a slot holds two copies for a moment. No cap but
 * the table (ML_MAX, above Schwung's slots and a re-pick). A copy that cannot load gives an instance that renders
 * silence and names itself ML_FAIL_NAME (preset_name), so Schwung still gets an instance.
 *
 * create / destroy run on Schwung's loader thread (the file copy and dlopen there, under a mutex): nothing of this on
 * the audio path (Gate P). The callbacks only forward through a pointer. Extra read-only key: dev_inst (the index k).
 * Exports one symbol (-fvisibility=hidden, -Wl,-z,defs). */
#define _GNU_SOURCE 1
#include <dirent.h>
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
#include "vendor/plugin_api_v1.h"

#define ML_EXPORT __attribute__((visibility("default")))
#define ML_MAX 8
#define ML_DATA "/data/UserData/schwung"
#define ML_BASE ML_DATA "/module_data/frodhes/inst"
#define ML_FAIL_NAME "NO ENGINE"

typedef struct { void *h; plugin_api_v2_t *api; char dir[600]; } ml_eng_t;
typedef struct { ml_eng_t *e; void *inst; int k; } ml_inst_t;
static ml_eng_t ml_pool[ML_MAX];
static pthread_mutex_t ml_mx = PTHREAD_MUTEX_INITIALIZER;
static const host_api_v1_t *_Atomic ml_host;   /* the table of the latest init call (the slot about to create) */

static int ml_mkdirs(const char *p)
{
    char b[600];
    snprintf(b, sizeof b, "%s", p);
    for (char *s = b + 1; *s; s++) if (*s == '/') { *s = 0; (void)mkdir(b, 0755); *s = '/'; }
    return mkdir(b, 0755) == 0 || errno == EEXIST ? 0 : -1;
}

static uint32_t ml_crc_t[256];
static int ml_crc_file(const char *path, uint32_t *crc, off_t *size)   /* zlib's CRC-32 of a file; 0 read */
{
    static uint8_t buf[1 << 16];
    uint32_t c = 0xFFFFFFFFu;
    off_t n = 0;
    ssize_t r;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    if (!ml_crc_t[1])
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t v = i;
            for (int j = 0; j < 8; j++) v = v >> 1 ^ (0xEDB88320u & (0u - (v & 1u)));
            ml_crc_t[i] = v;
        }
    while ((r = read(fd, buf, sizeof buf)) > 0) {
        for (ssize_t i = 0; i < r; i++) c = ml_crc_t[(c ^ buf[i]) & 0xFFu] ^ c >> 8;
        n += r;
    }
    close(fd);
    if (r < 0) return -1;
    *crc = ~c; *size = n;
    return 0;
}

/* dir/dsp-<crc>.so = src (size and CRC-32), copied when it is missing or differs; the other copies in dir deleted
 * (a mapped one lives on until it is unmapped). so: the copy's path. 0 ok */
static int ml_copy(const char *src, const char *dir, char *so, size_t son)
{
    static char buf[1 << 16];
    char tmp[720], nm[32], old[900];
    uint32_t cs, cd;
    off_t ns, nd;
    int i, o;
    ssize_t n = 0;
    DIR *d;
    struct dirent *de;
    if (ml_crc_file(src, &cs, &ns)) return -1;
    snprintf(nm, sizeof nm, "dsp-%08x.so", cs);
    snprintf(so, son, "%s/%s", dir, nm);
    if (!(!ml_crc_file(so, &cd, &nd) && nd == ns && cd == cs)) {
        snprintf(tmp, sizeof tmp, "%s/.dsp.so.%ld.tmp", dir, (long)getpid());   /* (two processes on one folder: tests) */
        i = open(src, O_RDONLY | O_CLOEXEC);
        o = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0755);
        if (i < 0 || o < 0) { if (i >= 0) close(i); if (o >= 0) { close(o); unlink(tmp); } return -1; }
        while ((n = read(i, buf, sizeof buf)) > 0) if (write(o, buf, (size_t)n) != n) { n = -1; break; }
        close(i);
        if (close(o) || n < 0) { unlink(tmp); return -1; }
        if (rename(tmp, so)) { unlink(tmp); return -1; }
    }
    if ((d = opendir(dir))) {
        while ((de = readdir(d)))
            if (!strncmp(de->d_name, "dsp", 3) && strstr(de->d_name, ".so") && strcmp(de->d_name, nm) &&
                (strlen(de->d_name) == 15 || !strcmp(de->d_name, "dsp.so"))) {
                snprintf(old, sizeof old, "%s/%s", dir, de->d_name);
                (void)unlink(old);
            }
        closedir(d);
    }
    return 0;
}

static void ml_engine_path(const char *module_dir, char *out, size_t n)   /* engine.so beside this file */
{
    Dl_info di;
    const char *s;
    if (dladdr((void *)ml_copy, &di) && di.dli_fname && (s = strrchr(di.dli_fname, '/')))
        snprintf(out, n, "%.*s/engine.so", (int)(s - di.dli_fname), di.dli_fname);
    else
        snprintf(out, n, "%s/engine.so", module_dir && *module_dir ? module_dir : ".");
}

static void ml_note(const char *dir, int k, long us)   /* <dir>/load.txt: the copy + dlopen time (the device tools) */
{
    char p[700], l[96];
    int fd, n;
    snprintf(p, sizeof p, "%s/load.txt", dir);
    n = snprintf(l, sizeof l, "inst %d copy+dlopen %ld us\n", k, us);
    fd = open(p, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd >= 0) { if (write(fd, l, (size_t)n) < 0) {} close(fd); }
}
static long ml_us(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1000000L + t.tv_nsec / 1000; }

static ml_eng_t *ml_load(const char *module_dir, int *kout)   /* (under ml_mx) the lowest free index, loaded */
{
    char src[700], so[700];
    struct stat st;
    plugin_api_v2_t *(*init)(const host_api_v1_t *);
    ml_eng_t *e;
    long t0 = ml_us();
    int k;
    for (k = 0; k < ML_MAX && ml_pool[k].h; k++) {}
    *kout = k;
    if (k == ML_MAX) return 0;
    e = &ml_pool[k];
    ml_engine_path(module_dir, src, sizeof src);
    if (module_dir && strstr(module_dir, "/modules/") && stat(ML_DATA, &st) == 0 && S_ISDIR(st.st_mode))
        snprintf(e->dir, sizeof e->dir, "%s/%d", ML_BASE, k);
    else
        snprintf(e->dir, sizeof e->dir, "%s/inst/%d", module_dir && *module_dir ? module_dir : ".", k);
    if (ml_mkdirs(e->dir) || ml_copy(src, e->dir, so, sizeof so)) return 0;
    e->h = dlopen(so, RTLD_NOW | RTLD_LOCAL);
    if (!e->h) return 0;
    init = (plugin_api_v2_t *(*)(const host_api_v1_t *))dlsym(e->h, MOVE_PLUGIN_INIT_V2_SYMBOL);
    e->api = init ? init(atomic_load(&ml_host)) : 0;   /* (the engine keeps this slot's host table) */
    if (!e->api || e->api->api_version != MOVE_PLUGIN_API_VERSION_2 || !e->api->create_instance) {
        dlclose(e->h); e->h = 0; e->api = 0;
        return 0;
    }
    ml_note(e->dir, k, ml_us() - t0);
    return e;
}
static void ml_unload(ml_eng_t *e) { dlclose(e->h); e->h = 0; e->api = 0; }

static void *ml_create(const char *module_dir, const char *json_defaults)
{
    ml_inst_t *in = calloc(1, sizeof *in);
    ml_eng_t *e;
    if (!in) return 0;
    pthread_mutex_lock(&ml_mx);
    e = ml_load(module_dir, &in->k);
    if (e) {
        in->inst = e->api->create_instance(e->dir, json_defaults);
        if (in->inst) in->e = e; else ml_unload(e);
    }
    pthread_mutex_unlock(&ml_mx);
    return in;                                         /* (no engine: a silent instance named ML_FAIL_NAME) */
}
static void ml_destroy(void *p)
{
    ml_inst_t *in = p;
    if (!in) return;
    pthread_mutex_lock(&ml_mx);
    if (in->e) {
        if (in->e->api->destroy_instance) in->e->api->destroy_instance(in->inst);   /* (its worker ends) */
        ml_unload(in->e);                              /* (no code of the copy runs any more: dlclose frees it) */
    }
    pthread_mutex_unlock(&ml_mx);
    free(in);
}
static void ml_on_midi(void *p, const uint8_t *m, int len, int src)
{
    ml_inst_t *in = p;
    if (in && in->e && in->e->api->on_midi) in->e->api->on_midi(in->inst, m, len, src);
}
static void ml_set_param(void *p, const char *k, const char *v)
{
    ml_inst_t *in = p;
    if (in && in->e && in->e->api->set_param) in->e->api->set_param(in->inst, k, v);
}
static int ml_get_param(void *p, const char *k, char *b, int n)
{
    ml_inst_t *in = p;
    if (!in || !k || !b || n <= 0) return -1;
    if (!strcmp(k, "dev_inst")) return snprintf(b, (size_t)n, "%d", in->k);
    if (!in->e) return !strcmp(k, "preset_name") ? snprintf(b, (size_t)n, "%s", ML_FAIL_NAME) : -1;
    return in->e->api->get_param ? in->e->api->get_param(in->inst, k, b, n) : -1;
}
static int ml_get_error(void *p, char *b, int n)
{
    ml_inst_t *in = p;
    if (in && !in->e && b && n > 0) return snprintf(b, (size_t)n, "Frodhes: the engine copy did not load");
    return in && in->e && in->e->api->get_error ? in->e->api->get_error(in->inst, b, n) : 0;
}
static void ml_render(void *p, int16_t *out, int frames)
{
    ml_inst_t *in = p;
    if (in && in->e) in->e->api->render_block(in->inst, out, frames);
    else memset(out, 0, (size_t)frames * 2u * sizeof *out);
}

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
