/* SPDX-License-Identifier: GPL-3.0-only */
/* The replay harness (M1-09): a host script's MIDI replayed through the Move plugin, written as the host build's WAV.
 *   move_replay DSP SCRIPT OUT.wav
 * dlopens DSP (build/move/frodhes/dsp.so), creates the instance in a fresh empty data folder (build/move/t/run.XXXXXX,
 * or $MOVE_REPLAY_TMP/run.XXXXXX: Gate M's own, so test-move's cleanup of build/move/t/run.* never removes it mid-run;
 * so no saved file changes the sound; the caller removes it), waits for the worker's boot, queues every
 * "T midi ST D1 D2" line as a message
 * due at host ms T (set_param "dev_midi", the bridge's harness key), renders 128-frame blocks and writes the same WAV
 * as tests/host.c: 44.1 kHz stereo int16, the same header, 256 * floor(end * 44100 / 256000) frames (host.c renders
 * one 256-frame block each time a millisecond tick owes 256 frames, and stops at the tick "end"). The sha256 of OUT
 * then equals the host render's when the plugin path is exact (Gate M, tools/gate_m.py). The replay plays Classic
 * (p_hq 0, the dev key; the Move's load default is HQ), as the host build does.
 * Lines that do not move the sound ("expect", "shot", "dump", comments) are skipped. Any other line (a panel button,
 * a knob, the FM-1's keys, a stall) is not MIDI-driven: exit 3 with its name, nothing written (that render stays
 * host-only, covered by Gate R).
 * Exit: 0 written, 1 failure, 3 not MIDI-driven. No Frodhes code here: only the plugin's ABI. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "../vendor/plugin_api_v1.h"

#define FS 44100u
#define HOST_BLOCK 256u                 /* tests/host.c HALF_FRAMES */
#define END_DEFAULT 6500u               /* tests/host.c end_ms when a script has no "end" line */

static void h_log(const char *m) { (void)m; }
static int h_clock(void) { return MOVE_CLOCK_STATUS_STOPPED; }
static float h_bpm(void) { return 120.0f; }
static double h_beat(void) { return -1.0; }
static host_api_v1_t host = { .api_version = 1, .sample_rate = FS, .frames_per_block = 128, .log = h_log,
                              .get_clock_status = h_clock, .get_bpm = h_bpm, .get_beat_position = h_beat };

static int skip_kind(const char *w)
{
    return !strcmp(w, "expect") || !strcmp(w, "shot") || !strcmp(w, "dump");
}

static void put32(FILE *f, uint32_t v) { fwrite(&v, 4, 1, f); }

int main(int argc, char **argv)
{
    char line[256], ev[96], what[16], tmpl[512], r[8];
    unsigned end = END_DEFAULT, nev = 0, due = 0;
    uint32_t frames, done = 0, i;
    int16_t *pcm;
    FILE *f, *w;
    void *h, *in;
    plugin_api_v2_t *api;
    move_plugin_init_v2_fn init;
    if (argc != 4) { fprintf(stderr, "usage: move_replay DSP SCRIPT OUT.wav\n"); return 1; }
    if (!(f = fopen(argv[2], "r"))) { printf("FAIL no script %s\n", argv[2]); return 1; }
    /* pass 1: MIDI-driven? and the end */
    while (fgets(line, sizeof line, f)) {
        unsigned t;
        if (line[0] == '#' || sscanf(line, "%u %15s", &t, what) != 2) continue;
        if (!strcmp(what, "end")) { end = t; continue; }
        if (!strcmp(what, "midi") || skip_kind(what)) continue;
        printf("host-only %s: \"%s\" lines\n", argv[2], what);
        fclose(f);
        return 3;
    }
    if (!(h = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL))) { printf("FAIL dlopen %s: %s\n", argv[1], dlerror()); return 1; }
    if (!(init = (move_plugin_init_v2_fn)dlsym(h, MOVE_PLUGIN_INIT_V2_SYMBOL))) { printf("FAIL no entry\n"); return 1; }
    api = init(&host);
    {
        const char *td = getenv("MOVE_REPLAY_TMP");
        if (td && *td) {
            mkdir(td, 0755);
            snprintf(tmpl, sizeof tmpl, "%s/run.XXXXXX", td);
        } else {
            mkdir("build", 0755); mkdir("build/move", 0755); mkdir("build/move/t", 0755);
            snprintf(tmpl, sizeof tmpl, "build/move/t/run.XXXXXX");
        }
    }
    if (!mkdtemp(tmpl)) { printf("FAIL mkdtemp\n"); return 1; }
    if (!(in = api->create_instance(tmpl, NULL))) { printf("FAIL create\n"); return 1; }
    for (i = 0;; i++) {                 /* the worker boots the app; the host would play silence meanwhile */
        int16_t z[256];
        r[0] = 0;
        api->get_param(in, "dev_ready", r, sizeof r);
        if (r[0] == '1') break;
        if (i >= 5000) { printf("FAIL the worker did not boot\n"); return 1; }
        api->render_block(in, z, 128);
        usleep(1000);
    }
    /* the Move loads with the HQ Rhodes on (move_bridge.h mb_start); Gate M proves the Classic path, so p_hq 0 (the
     * dev / test key) first, queued ahead of every message. MOVE_REPLAY_HQ=boot keeps the load's HQ */
    {
        const char *e = getenv("MOVE_REPLAY_HQ");
        if (!(e && !strcmp(e, "boot"))) api->set_param(in, "p_hq", "0");
    }
    api->set_param(in, "dev_trim", "0");   /* the app's own samples (move_bridge.h MB_TRIM_Q12) */
    /* pass 2: queue the MIDI. host.c plays its events in file order, each when the clock reaches its time and
     * every event before it has played: an event listed after a later one (an "expect" at 4000 before a MIDI line
     * at 3990) plays at the later time. So a message is due at the latest time of the events up to it */
    rewind(f);
    while (fgets(line, sizeof line, f)) {
        unsigned t, st, d1, d2;
        char sub[16] = "";
        if (line[0] == '#' || sscanf(line, "%u %15s", &t, what) != 2) continue;
        if (!strcmp(what, "end")) continue;     /* (no event in host.c: it does not hold the next ones back) */
        if (!strcmp(what, "expect") && (sscanf(line, "%*u %*s %15s", sub) != 1 ||
            (strcmp(sub, "preset") && strcmp(sub, "sound") && strcmp(sub, "state") && strcmp(sub, "led") && strcmp(sub, "rms"))))
            continue;                           /* (host.c skips an expect it does not know) */
        if (t > due) due = t;
        if (strcmp(what, "midi")) continue;
        if (sscanf(line, "%*u %*s %i %u %u", &st, &d1, &d2) != 3) { printf("FAIL bad MIDI line: %s", line); return 1; }
        snprintf(ev, sizeof ev, "%u %u %u %u", due, st, d1, d2);
        api->set_param(in, "dev_midi", ev);
        nev++;
    }
    fclose(f);
    frames = (uint32_t)((uint64_t)end * FS / (1000u * HOST_BLOCK)) * HOST_BLOCK;
    if (!(pcm = malloc(((size_t)frames + 128u) * 4u))) { printf("FAIL malloc\n"); return 1; }
    while (done < frames) { api->render_block(in, pcm + 2u * done, 128); done += 128u; }
    {
        char st[512] = "";
        unsigned out = 0;
        const char *p;
        api->get_param(in, "dev_stats", st, sizeof st);
        if ((p = strstr(st, "midi_out="))) sscanf(p + 9, "%u", &out);
        if (out != nev) { printf("FAIL %u of %u messages delivered (%s)\n", out, nev, st); return 1; }
    }
    if (!(w = fopen(argv[3], "wb"))) { perror(argv[3]); return 1; }
    fwrite("RIFF", 1, 4, w); put32(w, frames * 4u + 36u);
    fwrite("WAVEfmt ", 1, 8, w);
    put32(w, 16); put32(w, 0x00020001u); put32(w, FS); put32(w, FS * 4u); put32(w, 0x00100004u);
    fwrite("data", 1, 4, w); put32(w, frames * 4u);
    fwrite(pcm, 4, frames, w);
    if (fclose(w)) { perror(argv[3]); return 1; }
    printf("ok    replay %s: %u frames, %u messages -> %s\n", argv[2], frames, nev, argv[3]);
    api->destroy_instance(in);
    return 0;
}
