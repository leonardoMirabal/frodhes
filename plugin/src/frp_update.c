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
int frp_update_state_read(const char *text, frp_update_state *s)
{
    kv_t kv[MAXK];
    int n, i;
    frp_update_state_default(s);
    if (!text || parse_flat(text, kv, &n)) return -1;
    for (i = 0; i < n; i++) {
        size_t len = (size_t)(kv[i].ve - kv[i].v);
        if (key_is(&kv[i], "update_check")) s->check = !(len == 5 && !memcmp(kv[i].v, "false", 5));
        else if (key_is(&kv[i], "update_checked")) {
            long long x = 0;
            const char *p = kv[i].v;
            while (p < kv[i].ve && digit(*p) && x < 100000000000LL) x = x * 10 + (*p++ - '0');
            s->checked = p == kv[i].ve ? x : 0;
        } else if (key_is(&kv[i], "update_latest") && kv[i].v[0] == '"' && len - 2 < sizeof s->latest) {
            char t[16];
            int v[3];
            memcpy(t, kv[i].v + 1, len - 2);
            t[len - 2] = 0;
            if (!frp_ver_parse(t, v)) memcpy(s->latest, t, sizeof t);
        }
    }
    return 0;
}
int frp_update_state_write(const char *old, const frp_update_state *s, char *out, size_t n)
{
    kv_t kv[MAXK];
    int nkv = 0, i, k, first = 1, lv[3];
    size_t o = 0;
    char latest[16] = "";
    if (old && old[0] && parse_flat(old, kv, &nkv)) return -1;
    if (!frp_ver_parse(s->latest, lv)) snprintf(latest, sizeof latest, "%d.%d.%d", lv[0], lv[1], lv[2]);
#define PUT(...) do { k = snprintf(out + o, n - o, __VA_ARGS__); if (k < 0 || (size_t)k >= n - o) return -1; o += (size_t)k; } while (0)
    if (!n) return -1;
    PUT("{");
    for (i = 0; i < nkv; i++) {
        if (key_is(&kv[i], "update_check") || key_is(&kv[i], "update_checked") || key_is(&kv[i], "update_latest")) continue;
        PUT("%s\n  \"%.*s\": %.*s", first ? "" : ",", (int)(kv[i].ke - kv[i].k), kv[i].k, (int)(kv[i].ve - kv[i].v), kv[i].v);
        first = 0;
    }
    PUT("%s\n  \"update_check\": %s,\n  \"update_checked\": %lld,\n  \"update_latest\": \"%s\"\n}\n", first ? "" : ",",
        s->check ? "true" : "false", s->checked > 0 ? s->checked : 0LL, latest);
#undef PUT
    return 0;
}
