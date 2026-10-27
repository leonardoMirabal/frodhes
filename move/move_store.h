/* SPDX-License-Identifier: GPL-3.0-only */
/* The Move's files (M1-08): one record a file, written whole and atomically: a temporary file, fsync, rename over
 * the old one, fsync of the folder. A cut at any moment leaves the old file or the new one, never a torn one (a
 * torn temporary file is ignored). Standalone (no app code), so move/tests/test_store.c tests it on its own.
 *
 *   file:  "FRDM" | u32 version (1) | u32 payload length | u32 CRC-32 of the payload | the payload
 *
 * Called from the persistence worker only (move_bridge.h), never from a callback. */
#ifndef FRODHES_MOVE_STORE_H
#define FRODHES_MOVE_STORE_H
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MS_MAGIC 0x4D445246u             /* "FRDM" little endian */
#define MS_VER 1u

static uint32_t ms_crc32(const uint8_t *p, uint32_t n)
{
    uint32_t c = 0xFFFFFFFFu;
    while (n--) { c ^= *p++; for (int k = 0; k < 8; k++) c = c >> 1 ^ (0xEDB88320u & (0u - (c & 1u))); }
    return ~c;
}
static int ms_write_all(int fd, const void *p, size_t n)
{
    const uint8_t *b = p;
    while (n) { ssize_t w = write(fd, b, n); if (w < 0) { if (errno == EINTR) continue; return -1; } b += w; n -= (size_t)w; }
    return 0;
}
/* 0 written, -1 not (the old file stays) */
static int ms_save(const char *dir, const char *name, const void *data, uint32_t n)
{
    char path[600], tmp[620];
    uint32_t h[4] = { MS_MAGIC, MS_VER, n, ms_crc32(data, n) };
    int fd, dfd;
    snprintf(path, sizeof path, "%s/%s", dir, name);
    snprintf(tmp, sizeof tmp, "%s/.%s.tmp", dir, name);
    fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return -1;
    if (ms_write_all(fd, h, sizeof h) || ms_write_all(fd, data, n) || fsync(fd)) { close(fd); unlink(tmp); return -1; }
    if (close(fd) || rename(tmp, path)) { unlink(tmp); return -1; }
    dfd = open(dir, O_RDONLY | O_DIRECTORY | O_CLOEXEC);   /* (the rename itself on disk) */
    if (dfd >= 0) { (void)fsync(dfd); close(dfd); }
    return 0;
}
/* the payload's length (== n) when the file is whole and of this size, else -1 (missing, torn, another size) */
static int ms_load(const char *dir, const char *name, void *data, uint32_t n)
{
    char path[600];
    uint32_t h[4];
    int fd, ok = -1;
    snprintf(path, sizeof path, "%s/%s", dir, name);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    if (read(fd, h, sizeof h) == (ssize_t)sizeof h && h[0] == MS_MAGIC && h[1] == MS_VER && h[2] == n &&
        read(fd, data, n) == (ssize_t)n && ms_crc32(data, n) == h[3])
        ok = (int)n;
    close(fd);
    return ok;
}
static int ms_mkdirs(const char *dir)    /* mkdir -p */
{
    char p[600];
    size_t i, n = strlen(dir);
    if (n >= sizeof p) return -1;
    memcpy(p, dir, n + 1);
    for (i = 1; i <= n; i++)
        if (p[i] == '/' || p[i] == 0) {
            char c = p[i]; p[i] = 0;
            if (mkdir(p, 0755) && errno != EEXIST) return -1;
            p[i] = c;
        }
    return 0;
}
#endif
