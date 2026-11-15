/* SPDX-License-Identifier: GPL-3.0-only */
/* A headless VST 2.4 host for the MPC port's tests (docs/MPC.md), on the MPC Plugin SDK's own binary interface
 * (mpc/sdk/mpc_vst2_abi.h); of Frodhes only the parameter table (plugin/gen/params_map.h: ids and ranges). It plays the scripts plugin/tests/clap_render.c plays, the same way,
 * so the two renders can be compared byte for byte (tests/py/test_mpc.py: the MPC build against the desktop plugin).
 *
 *   vst2_render PLUGIN SCRIPT OUT.wav [options]
 *       a host script's MIDI (tests/host.c format: "T midi ST D1 D2", "T end"; expect / shot / dump lines skipped,
 *       an expect on preset / sound / state / led / rms holds the next events back to its time, as clap_render) at
 *       the host frame of the script's millisecond (44.1 kHz: ceil(T x 44.1); 48 kHz: 48 T; else ceil(T x rate /
 *       1000)), each MIDI message at its offset in the block. "T param ID VALUE" (ID the desktop plugin's, VALUE
 *       0..hi): the parameter set (setParameter, VALUE / hi) before the block that holds T. "T program N": the host
 *       selects program N (effSetProgram) before that block; the end prints the program and the PRESET's text. The output's first
 *       frames (the latency the plugin reports, initialDelay) are not written; the WAV is 16-bit stereo, the clip at
 *       +-32767, the length the host build's: what clap_render writes.
 *     --rate R              (44100)
 *     --block N             frames a process call (512); random:SEED for random sizes 1..2048
 *     --tempo BPM           the transport's tempo (none by default: the plugin plays 120)
 *     --nudge 1             the MPC's quirk at every prepare: parameter 0 set to 1.0 (or 0.0) and straight back
 *     --bench 1             print the time processReplacing took against the audio's length, and the worst call
 *     --instances N         N instances playing the same script at once (the time is theirs together; the WAV is
 *                           the last one's)
 *     --split 1             a block ends at each parameter change, the next starts with it (as a host that splits
 *                           its blocks at automation: the change at its own frame, as a CLAP parameter event)
 *     --state-check 1      after the script: the state saved, loaded over an instance in another state (all
 *                           parameters random) and saved again: the same bytes and values; two fresh instances
 *                           loaded with it play a chord sample for sample the same
 *   Exit: 0 ok, 1 a failure, 3 the script is not MIDI-driven (nothing written). */
#include "mpc_vst2_abi.h"
#include "../../plugin/gen/params_map.h"

#include <dlfcn.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

struct Ev { uint64_t h; uint8_t st, d1, d2; int pid; double pv; };   /* pid: a parameter's id, -1 MIDI, kProgramEv */
const int kProgramEv = -2;                           /* "T program N": the host selects program N (effSetProgram) */
std::vector<Ev> evs;
double g_tempo = 0.0, g_rate = 44100.0;
uint64_t g_pos = 0;
VstTimeInfo g_ti;

VstIntPtr host_cb(AEffect *e, VstInt32 op, VstInt32 index, VstIntPtr value, void *ptr, float opt)
{
    (void)e; (void)index; (void)value; (void)opt;
    switch (op) {
    case audioMasterVersion: return 2400;
    case audioMasterGetSampleRate: return (VstIntPtr)g_rate;
    case audioMasterGetTime:
        std::memset(&g_ti, 0, sizeof g_ti);
        g_ti.sampleRate = g_rate;
        g_ti.samplePos = (double)g_pos;
        g_ti.timeSigNumerator = 4; g_ti.timeSigDenominator = 4;
        g_ti.flags = kVstTimeSigValid;
        if (g_tempo > 0.0) {
            g_ti.tempo = g_tempo;
            g_ti.ppqPos = (double)g_pos / g_rate * g_tempo / 60.0;
            g_ti.flags |= kVstTempoValid | kVstPpqPosValid;
        }
        return (VstIntPtr)&g_ti;
    case audioMasterGetVendorString: if (ptr) std::strcpy((char *)ptr, "MPC 3"); return 1;
    case audioMasterGetProductString: if (ptr) std::strcpy((char *)ptr, "MPC 3"); return 1;
    case audioMasterCanDo:
        return ptr && (!std::strcmp((char *)ptr, "sendVstEvents") || !std::strcmp((char *)ptr, "sendVstMidiEvent") ||
                       !std::strcmp((char *)ptr, "sendVstTimeInfo") || !std::strcmp((char *)ptr, "receiveVstEvents") ||
                       !std::strcmp((char *)ptr, "receiveVstMidiEvent")) ? 1 : 0;
    default: return 0;
    }
}

int read_script(const char *path, unsigned *end_ms)
{
    char line[256], what[16];
    unsigned due = 0;
    FILE *f = std::fopen(path, "r");
    *end_ms = 6500u;
    if (!f) { std::printf("FAIL no script %s\n", path); return 1; }
    while (std::fgets(line, sizeof line, f)) {
        unsigned t, st = 0, d1 = 0, d2 = 0;
        char sub[16] = "";
        Ev e{};
        e.pid = -1;
        if (line[0] == '#' || std::sscanf(line, "%u %15s", &t, what) != 2) continue;
        if (!std::strcmp(what, "end")) { *end_ms = t; continue; }
        if (!std::strcmp(what, "shot") || !std::strcmp(what, "dump")) continue;
        if (!std::strcmp(what, "expect")) {
            if (std::sscanf(line, "%*u %*s %15s", sub) == 1 && (!std::strcmp(sub, "preset") || !std::strcmp(sub, "sound") ||
                !std::strcmp(sub, "state") || !std::strcmp(sub, "led") || !std::strcmp(sub, "rms")) && t > due) due = t;
            continue;
        }
        if (!std::strcmp(what, "param")) {
            unsigned id; double v;
            if (std::sscanf(line, "%*u %*s %i %lf", &id, &v) != 2) { std::printf("FAIL bad param line: %s", line); std::fclose(f); return 1; }
            e.pid = (int)id; e.pv = v;
        } else if (!std::strcmp(what, "program")) {
            int n;
            if (std::sscanf(line, "%*u %*s %i", &n) != 1) { std::printf("FAIL bad program line: %s", line); std::fclose(f); return 1; }
            e.pid = kProgramEv; e.pv = n;
        } else if (std::strcmp(what, "midi")) { std::printf("host-only %s: \"%s\" lines\n", path, what); std::fclose(f); return 3; }
        else if (std::sscanf(line, "%*u %*s %i %u %u", &st, &d1, &d2) != 3) { std::printf("FAIL bad MIDI line: %s", line); std::fclose(f); return 1; }
        if (t > due) due = t;
        e.h = g_rate == 48000.0 ? 48ull * due : g_rate == 44100.0 ? (441ull * due + 9u) / 10u
                                               : (uint64_t)std::ceil(due * g_rate / 1000.0 - 1e-9);
        e.st = (uint8_t)st; e.d1 = (uint8_t)d1; e.d2 = (uint8_t)d2;
        evs.push_back(e);
    }
    std::fclose(f);
    return 0;
}

uint32_t rnd_state = 1;
uint32_t rnd() { rnd_state ^= rnd_state << 13; rnd_state ^= rnd_state >> 17; rnd_state ^= rnd_state << 5; return rnd_state; }
void put32(FILE *f, uint32_t v) { std::fwrite(&v, 4, 1, f); }
VstIntPtr disp(AEffect *e, VstInt32 op, VstInt32 i = 0, VstIntPtr v = 0, void *p = nullptr, float o = 0.0f)
{
    return e->dispatcher(e, op, i, v, p, o);
}

/* the desktop plugin's parameter id -> the VST2 index (the table's order, as mpc/src/frodhes_mpc.cpp declares them) */
int param_index(int id, double *hi)
{
    for (uint32_t i = 0; i < FRP_NPARAM; i++)
        if ((int)FRP_PARAMS[i].id == id) { *hi = FRP_PARAMS[i].hi; return (int)i; }
    return -1;
}

} // namespace

int main(int argc, char **argv)
{
    int block = 512, nudge = 0, bench = 0, ninst = 1, state_check = 0, split = 0;
    if (argc < 4) {
        std::fprintf(stderr, "usage: vst2_render PLUGIN SCRIPT OUT.wav [--rate R] [--block N|random:SEED] [--tempo BPM] "
                             "[--nudge 1] [--bench 1] [--instances N]\n");
        return 1;
    }
    for (int i = 4; i + 1 < argc; i += 2) {
        if (!std::strcmp(argv[i], "--rate")) g_rate = std::atof(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--block")) {
            if (!std::strncmp(argv[i + 1], "random:", 7)) { block = 0; rnd_state = (uint32_t)std::strtoul(argv[i + 1] + 7, nullptr, 0) | 1u; }
            else block = std::atoi(argv[i + 1]);
        } else if (!std::strcmp(argv[i], "--tempo")) g_tempo = std::atof(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--nudge")) nudge = std::atoi(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--bench")) bench = std::atoi(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--instances")) ninst = std::atoi(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--state-check")) state_check = std::atoi(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--split")) split = std::atoi(argv[i + 1]);
    }
    unsigned end;
    int rc = read_script(argv[2], &end);
    if (rc) return rc;
    void *lib = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!lib) { std::printf("FAIL dlopen %s: %s\n", argv[1], dlerror()); return 1; }
    auto entry = (AEffect * (*)(audioMasterCallback))dlsym(lib, "VSTPluginMain");
    if (!entry) { std::printf("FAIL no VSTPluginMain\n"); return 1; }
    const int maxblk = 4096;
    std::vector<AEffect *> fx;
    for (int k = 0; k < ninst; k++) {
        AEffect *e = entry(host_cb);
        if (!e || e->magic != kEffectMagic) { std::printf("FAIL VSTPluginMain\n"); return 1; }
        disp(e, effOpen);
        disp(e, effSetSampleRate, 0, 0, nullptr, (float)g_rate);
        disp(e, effSetBlockSize, 0, block > 0 ? block : 2048);
        disp(e, effSetProgram, 0, 0);
        disp(e, effMainsChanged, 0, 1);
        if (nudge) {
            float v0 = e->getParameter(e, 0);
            e->setParameter(e, 0, v0 >= 0.5f ? 0.0f : 1.0f);
            e->setParameter(e, 0, v0);
        }
        disp(e, effStartProcess);
        fx.push_back(e);
    }
    AEffect *e0 = fx[0];
    uint32_t frames = (uint32_t)((uint64_t)end * 44100u / (1000u * 256u)) * 256u;
    if (g_rate == 48000.0) frames = (uint32_t)((uint64_t)frames * 160u / 147u);
    else if (g_rate != 44100.0) frames = (uint32_t)std::floor(frames * g_rate / 44100.0);
    uint32_t pre = (uint32_t)e0->initialDelay, clips = 0;
    std::vector<int16_t> pcm((size_t)frames * 2u + 2u);
    std::vector<float> l(maxblk), r(maxblk);
    std::vector<VstMidiEvent> me(4096);
    std::vector<char> evbuf(sizeof(VstEvents) + sizeof(VstEvent *) * 4096);
    uint64_t total = (uint64_t)pre + frames;
    size_t k = 0;
    double busy = 0.0, worst = 0.0;
    uint32_t worst_n = 0;
    std::vector<double> calls;                       /* (--bench: each call's time, for the percentiles) */
    g_pos = 0;
    while (g_pos < total) {
        uint32_t n = block > 0 ? (uint32_t)block : 1u + rnd() % 2048u;
        if (n > (uint32_t)maxblk) n = maxblk;
        if (g_pos + n > total) n = (uint32_t)(total - g_pos);
        if (split)   /* a block ends where a parameter changes: the next one starts with it, at its frame */
            for (size_t j = k; j < evs.size() && evs[j].h < g_pos + n; j++)
                if ((evs[j].pid >= 0 || evs[j].pid == kProgramEv) && evs[j].h > g_pos) { n = (uint32_t)(evs[j].h - g_pos); break; }
        VstEvents *ve = reinterpret_cast<VstEvents *>(evbuf.data());
        int nm = 0;
        for (; k < evs.size() && evs[k].h < g_pos + n; k++) {
            const Ev &e = evs[k];
            if (e.pid == kProgramEv) {   /* a program: selected before the block */
                for (AEffect *x : fx) disp(x, effSetProgram, 0, (VstIntPtr)e.pv);
                continue;
            }
            if (e.pid >= 0) {   /* a parameter: set before the block (VST2 has no time for it) */
                double hi = 1.0;
                int idx = param_index(e.pid, &hi);
                if (idx < 0) { std::printf("FAIL param id %d not in the table\n", e.pid); return 1; }
                for (AEffect *x : fx) x->setParameter(x, idx, (float)(e.pv / (hi > 0.0 ? hi : 1.0)));
                continue;
            }
            VstMidiEvent &m = me[(size_t)nm];
            std::memset(&m, 0, sizeof m);
            m.type = kVstMidiType; m.byteSize = sizeof m;
            m.deltaFrames = (VstInt32)(e.h - g_pos);
            m.midiData[0] = (char)e.st; m.midiData[1] = (char)e.d1; m.midiData[2] = (char)e.d2;
            ve->events[nm] = reinterpret_cast<VstEvent *>(&m);
            if (++nm >= 4096) { k++; break; }
        }
        ve->numEvents = nm; ve->reserved = 0;
        auto t0 = std::chrono::steady_clock::now();
        for (AEffect *x : fx) {
            float *outs[2] = { l.data(), r.data() };
            disp(x, effProcessEvents, 0, 0, ve);
            x->processReplacing(x, nullptr, outs, (VstInt32)n);
        }
        double dt = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        busy += dt;
        if (dt > worst) { worst = dt; worst_n = n; }
        if (bench) calls.push_back(dt);
        /* (the buffers hold the last instance's output; with one instance, the first's) */
        for (uint32_t i = 0; i < n; i++) {
            uint64_t g = g_pos + i;
            if (!std::isfinite(l[i]) || !std::isfinite(r[i])) { std::printf("FAIL non-finite output at frame %llu\n", (unsigned long long)g); return 1; }
            if (g >= pre && g - pre < frames) {
                long x = std::lrint(l[i] * 32768.0), y = std::lrint(r[i] * 32768.0);
                if (x > 32767 || x < -32767 || y > 32767 || y < -32767) clips++;
                pcm[2u * (g - pre)] = (int16_t)(x > 32767 ? 32767 : x < -32767 ? -32767 : x);
                pcm[2u * (g - pre) + 1u] = (int16_t)(y > 32767 ? 32767 : y < -32767 ? -32767 : y);
            }
        }
        g_pos += n;
    }
    {   /* the program the host shows and the PRESET parameter's text (tests/py/test_mpc.py) */
        char pn[64] = "", pt[64] = "";
        double hi = 0.0;
        int pi = param_index(0x10002, &hi);
        VstIntPtr prog = disp(e0, effGetProgram);
        disp(e0, effGetProgramName, 0, 0, pn);
        if (pi >= 0) disp(e0, effGetParamDisplay, pi, 0, pt);
        std::printf("program %ld \"%s\", Preset parameter \"%s\"\n", (long)prog, pn, pt);
    }
    if (state_check) {   /* the chunk saved now: (1) loaded into an instance left in another state (every parameter
                          * set at random, 2 blocks played; the SDK host_sim's check 3) and saved again after 2 blocks:
                          * the same bytes, every parameter value the same; (2) two fresh instances loaded with it play
                          * a chord sample for sample the same (an instance's history is not state: the effects'
                          * filters, envelopes and free-running LFO phases carry it, as in the desktop plugin) */
        void *p1 = nullptr, *p2 = nullptr;
        VstIntPtr n1 = disp(e0, effGetChunk, 0, 0, &p1);
        std::vector<uint8_t> c1((uint8_t *)p1, (uint8_t *)p1 + (n1 > 0 ? n1 : 0));
        VstEvents none{};
        auto play = [&](AEffect *x, int blocks) {
            float *o[2] = { l.data(), r.data() };
            for (int k = 0; k < blocks; k++) { disp(x, effProcessEvents, 0, 0, &none); x->processReplacing(x, nullptr, o, 128); }
        };
        auto fresh = [&](int scramble) {
            AEffect *x = entry(host_cb);
            disp(x, effOpen);
            disp(x, effSetSampleRate, 0, 0, nullptr, (float)g_rate);
            disp(x, effSetBlockSize, 0, 512);
            disp(x, effMainsChanged, 0, 1);
            disp(x, effStartProcess);
            uint32_t rng = 12345u;
            for (int i = 0; scramble && i < x->numParams; i++) {
                rng = rng * 1664525u + 1013904223u;
                x->setParameter(x, i, (float)(rng >> 8) / 16777216.0f);
            }
            if (scramble) play(x, 2);
            disp(x, effSetChunk, 0, (VstIntPtr)c1.size(), c1.data());
            return x;
        };
        AEffect *a = fresh(1), *b = fresh(0), *c = fresh(0);
        play(a, 2);
        VstIntPtr n2 = disp(a, effGetChunk, 0, 0, &p2);
        int same = n2 == n1 && n1 > 0 && !std::memcmp(p2, c1.data(), (size_t)n1), pdiff = 0;
        for (int i = 0; i < e0->numParams; i++) if (e0->getParameter(e0, i) != a->getParameter(a, i)) pdiff++;
        if (!same) {
            std::string s1((const char *)c1.data(), c1.size()), s2((const char *)p2, (size_t)(n2 > 0 ? n2 : 0));
            size_t k0 = 0;
            while (k0 < s1.size() && k0 < s2.size() && s1[k0] == s2[k0]) k0++;
            std::printf("state: the chunks differ from byte %zu\n", k0);
        }
        std::vector<float> l2(512), r2(512);
        int diff_audio = 0;
        double worst = 0.0, peak = 0.0;
        for (int blk = 0; blk < 88; blk++) {   /* 1 s: a chord, released at 0.5 s */
            float *o1[2] = { l.data(), r.data() }, *o2[2] = { l2.data(), r2.data() };
            VstMidiEvent me[3];
            struct { VstInt32 numEvents; VstIntPtr reserved; VstEvent *events[3]; } ve{};   /* VstEvents, 3 long */
            int ne = 0;
            if (blk == 0 || blk == 44)
                for (int k = 0; k < 3; k++) {
                    std::memset(&me[k], 0, sizeof me[k]);
                    me[k].type = kVstMidiType; me[k].byteSize = sizeof me[k]; me[k].deltaFrames = 17 * k;
                    me[k].midiData[0] = (char)(blk ? 0x80 : 0x90); me[k].midiData[1] = (char)(57 + 4 * k);
                    me[k].midiData[2] = (char)(blk ? 0 : 70 + 20 * k);
                    ve.events[ne++] = (VstEvent *)&me[k];
                }
            ve.numEvents = ne;
            disp(b, effProcessEvents, 0, 0, &ve); b->processReplacing(b, nullptr, o1, 512);
            disp(c, effProcessEvents, 0, 0, &ve); c->processReplacing(c, nullptr, o2, 512);
            int d = 0;
            for (int k = 0; k < 512; k++) {
                double x = std::fabs((double)l[k] - l2[k]) + std::fabs((double)r[k] - r2[k]);
                if (x > 0.0) d = 1;
                worst = std::fmax(worst, x);
                peak = std::fmax(peak, std::fabs((double)l[k]));
            }
            diff_audio += d;
        }
        int bad = !same || pdiff || diff_audio || peak <= 0.0;
        std::printf("%s state round trip: chunk %ld bytes, loaded over another state and re-saved: %s, %d parameter "
                    "values differ; two fresh loads: %d of 88 blocks differ (worst %.3g, peak %.3f)\n",
                    bad ? "FAIL " : "ok   ", (long)n1, same ? "identical" : "DIFFERS", pdiff, diff_audio, worst, peak);
        for (AEffect *x : { a, b, c }) { disp(x, effStopProcess); disp(x, effMainsChanged, 0, 0); disp(x, effClose); }
        if (bad) return 1;
    }
    if (bench) {
        double audio = (double)total / g_rate;
        std::printf("bench: %d instance(s): process %.3f s for %.3f s of audio at %.0f Hz: %.4f of real time; the worst "
                    "call %.0f us for %u frames (%.3f of its time)\n", ninst, busy, audio, g_rate, busy / audio,
                    worst * 1e6, worst_n, worst * g_rate / (worst_n ? worst_n : 1));
        std::sort(calls.begin(), calls.end());
        auto pct = [&](double q) { return calls.empty() ? 0.0 : calls[(size_t)(q * (double)(calls.size() - 1))] * 1e6; };
        std::printf("bench: per call p50 %.0f us, p99 %.0f us, p99.9 %.0f us (%zu calls; the worst is the machine's "
                    "noise as much as the plugin's)\n", pct(0.5), pct(0.99), pct(0.999), calls.size());
    }
    for (AEffect *x : fx) { disp(x, effStopProcess); disp(x, effMainsChanged, 0, 0); disp(x, effClose); }
    FILE *w = std::fopen(argv[3], "wb");
    if (!w) { std::perror(argv[3]); return 1; }
    std::fwrite("RIFF", 1, 4, w); put32(w, frames * 4u + 36u);
    std::fwrite("WAVEfmt ", 1, 8, w);
    put32(w, 16); put32(w, 0x00020001u); put32(w, (uint32_t)(g_rate + 0.5)); put32(w, (uint32_t)(g_rate + 0.5) * 4u);
    put32(w, 0x00100004u);
    std::fwrite("data", 1, 4, w); put32(w, frames * 4u);
    std::fwrite(pcm.data(), 4, frames, w);
    if (std::fclose(w)) { std::perror(argv[3]); return 1; }
    std::printf("ok    render %s: %u frames at %.0f Hz, latency %u, %zu events, %u clipped -> %s\n", argv[2], frames,
                g_rate, pre, evs.size(), clips, argv[3]);
    return 0;
}
