/* SPDX-License-Identifier: GPL-3.0-only */
/* CHECK FOR UPDATES, the pure part: versions, the response, the 24 h rule, the settings file's text
 * (plugin/src/frp_update.h). No OS call: tested on any OS (plugin/tests/test_update.c). */
#include "frp_update.h"
#include <stdio.h>
#include <string.h>

static int digit(char c) { return c >= '0' && c <= '9'; }

int frp_ver_parse(const char *s, int v[3])
{
    int i;
    if (!s) return -1;
    if (*s == 'v') s++;
    for (i = 0; i < 3; i++) {
        long x = 0;
        int nd = 0;
        while (digit(*s)) { if (++nd > 5) return -1; x = x * 10 + (*s++ - '0'); }
        if (!nd) return -1;
        v[i] = (int)x;
        if (i < 2) { if (*s != '.') return -1; s++; }
    }
    return *s ? -1 : 0;
}
int frp_ver_cmp(const int a[3], const int b[3])
{
    int i;
    for (i = 0; i < 3; i++) if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
    return 0;
}
int frp_ver_is_release(const char *own)
{
    int v[3];
    if (!own || *own == 'v' || frp_ver_parse(own, v)) return 0;
    return v[0] || v[1] || v[2];
}
int frp_update_newer(const char *own, const char *latest, char out[16])
{
    int a[3], b[3];
    out[0] = 0;
    if (!frp_ver_is_release(own) || frp_ver_parse(own, a) || frp_ver_parse(latest, b)) return 0;
    if (frp_ver_cmp(b, a) <= 0) return 0;
    snprintf(out, 16, "%d.%d.%d", b[0], b[1], b[2]);
    return 1;
}
int frp_update_due(long long now, long long checked)
{
    if (checked <= 0) return 1;
    return now - checked >= FRP_UPDATE_PERIOD || checked - now >= FRP_UPDATE_PERIOD;
}
int frp_update_plan(const frp_update_state *s, const char *own, long long now)
{
    return s->check && frp_ver_is_release(own) && frp_update_due(now, s->checked);
}

static const char *ws(const char *p, const char *e) { while (p < e && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) p++; return p; }
/* a JSON string at p ('"'): the end after its closing quote, 0 when it does not close */
static const char *str_end(const char *p, const char *e)
{
    if (p >= e || *p != '"') return 0;
    for (p++; p < e; p++) {
        if (*p == '\\') { if (++p >= e) return 0; }
        else if (*p == '"') return p + 1;
        else if ((unsigned char)*p < 0x20) return 0;
    }
    return 0;
}
int frp_update_tag(const char *body, size_t n, char out[16])
{
    static const char key[] = "\"tag_name\"";
    const char *p = body, *e = body + n;
    out[0] = 0;
    if (!body) return -1;
    while ((size_t)(e - p) >= sizeof key - 1) {
        const char *q = memchr(p, '"', (size_t)(e - p)), *v, *ve;
        char t[24];
        int ver[3];
        if (!q || (size_t)(e - q) < sizeof key - 1) return -1;
        if (memcmp(q, key, sizeof key - 1)) {   /* (skip this whole string: a key or a value) */
            const char *se = str_end(q, e);
            if (!se) return -1;
            p = se;
            continue;
        }
        v = ws(q + sizeof key - 1, e);
        if (v >= e || *v != ':') { p = q + sizeof key - 1; continue; }
        v = ws(v + 1, e);
        if (!(ve = str_end(v, e)) || ve - v - 2 >= (long)sizeof t) return -1;
        memcpy(t, v + 1, (size_t)(ve - v - 2));
        t[ve - v - 2] = 0;
        if (frp_ver_parse(t, ver)) return -1;
        snprintf(out, 16, "%d.%d.%d", ver[0], ver[1], ver[2]);
        return 0;
    }
    return -1;
}

/* ---- the settings file: a flat JSON object (string, number, true / false / null values) */
#define MAXK 64
typedef struct { const char *k, *ke, *v, *ve; } kv_t;   /* the key without its quotes, the value's raw text */
static const char *val_end(const char *p, const char *e)
{
    if (p >= e) return 0;
    if (*p == '"') return str_end(p, e);
    if (*p == '{' || *p == '[') return 0;              /* (not flat: never rewritten) */
    while (p < e && *p != ',' && *p != '}' && *p != ' ' && *p != '\t' && *p != '\r' && *p != '\n') p++;
    return p;
}
static int parse_flat(const char *text, kv_t *kv, int *nkv)
{
    const char *e = text + strlen(text), *p = ws(text, e);
    *nkv = 0;
    if (p == e) return 0;                              /* (an empty file: no keys) */
    if (*p++ != '{') return -1;
    p = ws(p, e);
    if (p < e && *p == '}') return ws(p + 1, e) == e ? 0 : -1;
    for (;;) {
        const char *ke = str_end(p, e), *v, *ve;
        if (!ke || *nkv >= MAXK) return -1;
        v = ws(ke, e);
        if (v >= e || *v != ':') return -1;
        v = ws(v + 1, e);
        if (!(ve = val_end(v, e)) || ve == v) return -1;
        kv[*nkv].k = p + 1; kv[*nkv].ke = ke - 1; kv[*nkv].v = v; kv[*nkv].ve = ve;
        (*nkv)++;
        p = ws(ve, e);
        if (p < e && *p == ',') { p = ws(p + 1, e); continue; }
        if (p < e && *p == '}') return ws(p + 1, e) == e ? 0 : -1;
        return -1;
    }
}
static int key_is(const kv_t *a, const char *k) { size_t n = strlen(k); return (size_t)(a->ke - a->k) == n && !memcmp(a->k, k, n); }
void frp_update_state_default(frp_update_state *s) { memset(s, 0, sizeof *s); s->check = 1; }
/* a JSON string's text (raw, between the quotes) into out: \\ \" \/ undone; -1 another escape, a control character
 * or too long */
static int unquote(const char *v, const char *ve, char *out, size_t n)
{
    size_t o = 0;
    if (ve - v < 2 || *v != '"' || ve[-1] != '"') return -1;
    for (v++, ve--; v < ve; v++) {
        char c = *v;
        if ((unsigned char)c < 0x20) return -1;
        if (c == '\\') {
            if (++v >= ve || (*v != '\\' && *v != '"' && *v != '/')) return -1;
            c = *v;
        }
        if (o + 1 >= n) return -1;
        out[o++] = c;
    }
    out[o] = 0;
    return 0;
}
int frp_update_state_read(const char *text, frp_update_state *s)
{
    kv_t kv[MAXK];
    int n, i, v[3];
    frp_update_state_default(s);
    if (!text || parse_flat(text, kv, &n)) return -1;
    for (i = 0; i < n; i++) {
        size_t len = (size_t)(kv[i].ve - kv[i].v);
        char t[16];
        if (key_is(&kv[i], "update_check")) s->check = !(len == 5 && !memcmp(kv[i].v, "false", 5));
        else if (key_is(&kv[i], "update_checked")) {
            long long x = 0;
            const char *p = kv[i].v;
            while (p < kv[i].ve && digit(*p) && x < 100000000000LL) x = x * 10 + (*p++ - '0');
            s->checked = p == kv[i].ve ? x : 0;
        } else if (key_is(&kv[i], "update_latest")) {
            if (!unquote(kv[i].v, kv[i].ve, t, sizeof t) && !frp_ver_parse(t, v)) memcpy(s->latest, t, sizeof t);
        } else if (key_is(&kv[i], "update_file_version")) {
            if (!unquote(kv[i].v, kv[i].ve, t, sizeof t) && !frp_ver_parse(t, v)) memcpy(s->file_ver, t, sizeof t);
        } else if (key_is(&kv[i], "update_file")) {
            if (unquote(kv[i].v, kv[i].ve, s->file, sizeof s->file)) s->file[0] = 0;
        }
    }
    if (!s->file[0] || !s->file_ver[0]) s->file[0] = s->file_ver[0] = 0;   /* (the two together, or neither) */
    return 0;
}
static int is_ours(const kv_t *a)
{
    return key_is(a, "update_check") || key_is(a, "update_checked") || key_is(a, "update_latest") ||
           key_is(a, "update_file") || key_is(a, "update_file_version");
}
int frp_update_state_write(const char *old, const frp_update_state *s, char *out, size_t n)
{
    kv_t kv[MAXK];
    int nkv = 0, i, k, first = 1, lv[3], fv[3];
    size_t o = 0, q = 0;
    char latest[16] = "", fver[16] = "", file[2100];
    const char *p;
    if (old && old[0] && parse_flat(old, kv, &nkv)) return -1;
    if (!frp_ver_parse(s->latest, lv)) snprintf(latest, sizeof latest, "%d.%d.%d", lv[0], lv[1], lv[2]);
    for (p = s->file; *p && q + 3 < sizeof file; p++) {            /* (a path: \ and " escaped) */
        if ((unsigned char)*p < 0x20) { q = 0; break; }
        if (*p == '\\' || *p == '"') file[q++] = '\\';
        file[q++] = *p;
    }
    if (*p) q = 0;
    file[q] = 0;
    if (q && !frp_ver_parse(s->file_ver, fv)) snprintf(fver, sizeof fver, "%d.%d.%d", fv[0], fv[1], fv[2]);
    else file[0] = 0;
#define PUT(...) do { k = snprintf(out + o, n - o, __VA_ARGS__); if (k < 0 || (size_t)k >= n - o) return -1; o += (size_t)k; } while (0)
    if (!n) return -1;
    PUT("{");
    for (i = 0; i < nkv; i++) {
        if (is_ours(&kv[i])) continue;
        PUT("%s\n  \"%.*s\": %.*s", first ? "" : ",", (int)(kv[i].ke - kv[i].k), kv[i].k, (int)(kv[i].ve - kv[i].v), kv[i].v);
        first = 0;
    }
    PUT("%s\n  \"update_check\": %s,\n  \"update_checked\": %lld,\n  \"update_latest\": \"%s\",\n"
        "  \"update_file\": \"%s\",\n  \"update_file_version\": \"%s\"\n}\n", first ? "" : ",",
        s->check ? "true" : "false", s->checked > 0 ? s->checked : 0LL, latest, file, fver);
#undef PUT
    return 0;
}

/* ---- this system's package */
const char *frp_update_asset_name(int os, int arch)
{
    switch (os) {
    case 0: return "frodhes-plugin-macos.pkg";            /* (FRP_OS_MACOS) */
    case 2: return "frodhes-plugin-windows.zip";          /* (FRP_OS_WINDOWS) */
    default: return arch == FRP_UPD_ARCH_AARCH64 ? "frodhes-plugin-linux-aarch64.zip" : "frodhes-plugin-linux-x86_64.zip";
    }
}
const char *frp_update_download_url(int os, int arch)
{
    static char u[4][128];
    int k = os == 0 ? 0 : os == 2 ? 1 : arch == FRP_UPD_ARCH_AARCH64 ? 3 : 2;
    snprintf(u[k], sizeof u[k], "%s%s", FRP_UPDATE_DL, frp_update_asset_name(os, arch));
    return u[k];
}
int frp_update_file_name(int os, int arch, const char *ver, char *out, size_t n)
{
    const char *a = frp_update_asset_name(os, arch), *dot = strrchr(a, '.');
    int v[3], k;
    if (frp_ver_parse(ver, v)) return -1;
    k = snprintf(out, n, "%.*s-%d.%d.%d%s", (int)(dot - a), a, v[0], v[1], v[2], dot);
    return k < 0 || (size_t)k >= n ? -1 : 0;
}

/* ---- the answer's assets: a small JSON walk (objects, arrays, strings, literals) */
typedef struct { const char *p, *e; int depth; } jw_t;
static void jws(jw_t *j) { j->p = ws(j->p, j->e); }
static int jskip(jw_t *j)                        /* one value: 0 ok */
{
    jws(j);
    if (j->p >= j->e) return -1;
    if (*j->p == '"') { const char *q = str_end(j->p, j->e); if (!q) return -1; j->p = q; return 0; }
    if (*j->p == '{' || *j->p == '[') {
        char close = *j->p == '{' ? '}' : ']';
        int obj = close == '}';
        if (++j->depth > 64) return -1;
        j->p++;
        jws(j);
        if (j->p < j->e && *j->p == close) { j->p++; j->depth--; return 0; }
        for (;;) {
            if (obj) {
                const char *q = str_end(j->p, j->e);
                if (!q) return -1;
                j->p = ws(q, j->e);
                if (j->p >= j->e || *j->p++ != ':') return -1;
            }
            if (jskip(j)) return -1;
            jws(j);
            if (j->p < j->e && *j->p == ',') { j->p++; jws(j); continue; }
            if (j->p < j->e && *j->p == close) { j->p++; j->depth--; return 0; }
            return -1;
        }
    }
    while (j->p < j->e && *j->p != ',' && *j->p != '}' && *j->p != ']' && *j->p != ' ' && *j->p != '\n' &&
           *j->p != '\r' && *j->p != '\t') j->p++;
    return 0;
}
static int jkey(jw_t *j, const char **k, size_t *kn)   /* an object's next key (j at it): 0 ok, then j after ':' */
{
    const char *q;
    jws(j);
    if (!(q = str_end(j->p, j->e))) return -1;
    *k = j->p + 1; *kn = (size_t)(q - j->p - 2);
    j->p = ws(q, j->e);
    if (j->p >= j->e || *j->p++ != ':') return -1;
    jws(j);
    return 0;
}
static int keq(const char *k, size_t kn, const char *s) { return kn == strlen(s) && !memcmp(k, s, kn); }
/* the members of one asset object (j at '{'): name, browser_download_url, size, digest */
static int asset_obj(jw_t *j, char name[128], char url[512], long long *size, char dig[80])
{
    name[0] = url[0] = dig[0] = 0;
    *size = -1;
    if (*j->p != '{') return jskip(j);
    j->p++;
    jws(j);
    if (j->p < j->e && *j->p == '}') { j->p++; return 0; }
    for (;;) {
        const char *k, *v;
        size_t kn;
        if (jkey(j, &k, &kn)) return -1;
        v = j->p;
        if (jskip(j)) return -1;
        if (keq(k, kn, "name")) { if (unquote(v, j->p, name, 128)) name[0] = 0; }
        else if (keq(k, kn, "browser_download_url")) { if (unquote(v, j->p, url, 512)) url[0] = 0; }
        else if (keq(k, kn, "digest")) { if (unquote(v, j->p, dig, 80)) dig[0] = 0; }
        else if (keq(k, kn, "size")) {
            long long x = 0;
            const char *p = v;
            while (p < j->p && digit(*p) && x < (1LL << 40)) x = x * 10 + (*p++ - '0');
            *size = p == j->p && p > v ? x : -1;
        }
        jws(j);
        if (j->p < j->e && *j->p == ',') { j->p++; continue; }
        if (j->p < j->e && *j->p == '}') { j->p++; return 0; }
        return -1;
    }
}
int frp_update_asset(const char *body, size_t n, const char *name, char url[512], long long *size, char sha[65])
{
    jw_t j = { body, body + n, 0 };
    url[0] = sha[0] = 0;
    *size = -1;
    if (!body) return -1;
    jws(&j);
    if (j.p >= j.e || *j.p++ != '{') return -1;
    for (;;) {
        const char *k;
        size_t kn;
        jws(&j);
        if (j.p < j.e && *j.p == '}') return -1;
        if (jkey(&j, &k, &kn)) return -1;
        if (keq(k, kn, "assets") && j.p < j.e && *j.p == '[') {
            j.p++;
            jws(&j);
            if (j.p < j.e && *j.p == ']') return -1;
            for (;;) {
                char an[128], au[512], ad[80];
                long long as;
                int i, hex;
                if (asset_obj(&j, an, au, &as, ad)) return -1;
                if (!strcmp(an, name)) {
                    for (hex = strlen(ad) == 71, i = 0; hex && i < 64; i++) hex = (ad[7 + i] >= '0' && ad[7 + i] <= '9') || (ad[7 + i] >= 'a' && ad[7 + i] <= 'f');
                    if (strncmp(ad, "sha256:", 7) || strlen(ad) != 71 || !hex || as <= 0 ||
                        strncmp(au, FRP_UPDATE_ASSET_PREFIX, strlen(FRP_UPDATE_ASSET_PREFIX)) || strstr(au, ".."))
                        return -1;
                    memcpy(url, au, strlen(au) + 1);
                    memcpy(sha, ad + 7, 65);
                    *size = as;
                    return 0;
                }
                jws(&j);
                if (j.p < j.e && *j.p == ',') { j.p++; jws(&j); continue; }
                return -1;                       /* (the end of the list: not there) */
            }
        }
        if (jskip(&j)) return -1;
        jws(&j);
        if (j.p < j.e && *j.p == ',') { j.p++; continue; }
        return -1;
    }
}

/* ---- SHA-256 (FIPS 180-4) */
static const uint32_t K256[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
    0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
    0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
    0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
    0xc67178f2};
#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))
static void sha_block(uint32_t h[8], const uint8_t *b)
{
    uint32_t w[64], a, bb, c, d, e, f, g, hh, t1, t2;
    int i;
    for (i = 0; i < 16; i++) w[i] = (uint32_t)b[4 * i] << 24 | (uint32_t)b[4 * i + 1] << 16 | (uint32_t)b[4 * i + 2] << 8 | b[4 * i + 3];
    for (i = 16; i < 64; i++)
        w[i] = (ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10)) + w[i - 7] +
               (ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3)) + w[i - 16];
    a = h[0]; bb = h[1]; c = h[2]; d = h[3]; e = h[4]; f = h[5]; g = h[6]; hh = h[7];
    for (i = 0; i < 64; i++) {
        t1 = hh + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & f) ^ (~e & g)) + K256[i] + w[i];
        t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c));
        hh = g; g = f; f = e; e = d + t1; d = c; c = bb; bb = a; a = t1 + t2;
    }
    h[0] += a; h[1] += bb; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
}
void frp_sha256_hex(const void *data, size_t n, char hex[65])
{
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    const uint8_t *p = data;
    uint8_t last[128];
    size_t i, r = n % 64, tail;
    uint64_t bits = (uint64_t)n * 8u;
    for (i = 0; i + 64 <= n; i += 64) sha_block(h, p + i);
    memset(last, 0, sizeof last);
    memcpy(last, p + n - r, r);
    last[r] = 0x80;
    tail = r < 56 ? 64 : 128;
    for (i = 0; i < 8; i++) last[tail - 1 - i] = (uint8_t)(bits >> (8 * i));
    sha_block(h, last);
    if (tail == 128) sha_block(h, last + 64);
    for (i = 0; i < 8; i++) snprintf(hex + 8 * i, 9, "%08x", (unsigned)h[i]);
}
