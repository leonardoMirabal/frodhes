// SPDX-License-Identifier: GPL-3.0-only
// The Frodhes VST3 in a minimal offline VST3 host (the SDK's own hosting classes), for tools/plugin_dist.py:
//   vst3_render PLUGIN.vst3 RATE FRAMES EVENTS.txt OUT.f32 [ENGINE]
// EVENTS.txt: one MIDI event a line, "FRAME STATUS DATA1 DATA2" (decimal, frames from the start, sorted), the format
// of tools/plugin_au_render.swift. Notes go as VST3 note events; a controller goes the way a DAW sends it: through the
// plugin's IMidiMapping, as a point on the parameter it names, at the event's frame. The render is cut at every
// event's frame (so each block holds at most one point a parameter: clap-wrapper reads a MIDI parameter's last
// point). ENGINE (0 CLASSIC, 1 HQ; default: the plugin's own) is set the way a DAW restores a project: the
// component's state, read back with its "engine" key changed, loaded before the plugin is activated (a parameter
// change while active hands over on the main thread, which this host does not run). OUT.f32: the left and right channels, interleaved float32, FRAMES frames. Built by tools/plugin_dist.py
// with the command line tools (as tools/plugin_vst3_validator.sh builds the SDK's validator).
#include "public.sdk/source/common/memorystream.h"
#include "public.sdk/source/vst/hosting/eventlist.h"
#include "public.sdk/source/vst/hosting/hostclasses.h"
#include "public.sdk/source/vst/hosting/module.h"
#include "public.sdk/source/vst/hosting/parameterchanges.h"
#include "public.sdk/source/vst/hosting/plugprovider.h"
#include "public.sdk/source/vst/hosting/processdata.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivstmidicontrollers.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

using namespace Steinberg;
using namespace Steinberg::Vst;

int main(int argc, char **argv)
{
    if (argc < 6) {
        fprintf(stderr, "usage: vst3_render PLUGIN.vst3 RATE FRAMES EVENTS.txt OUT.f32 [ENGINE]\n");
        return 2;
    }
    double rate = atof(argv[2]);
    long total = atol(argv[3]);
    int engine = argc > 6 ? atoi(argv[6]) : -1;
    struct Ev { long fr; int st, d1, d2; };
    std::vector<Ev> ev;
    if (FILE *f = fopen(argv[4], "r")) {
        Ev e;
        while (fscanf(f, "%ld %d %d %d", &e.fr, &e.st, &e.d1, &e.d2) == 4) ev.push_back(e);
        fclose(f);
    } else { perror(argv[4]); return 2; }

    HostApplication host;
    PluginContextFactory::instance().setPluginContext(&host);
    std::string err;
    auto mod = VST3::Hosting::Module::create(argv[1], err);
    if (!mod) { fprintf(stderr, "load %s: %s\n", argv[1], err.c_str()); return 1; }
    auto factory = mod->getFactory();
    IPtr<PlugProvider> prov;
    for (auto &ci : factory.classInfos())
        if (ci.category() == kVstAudioEffectClass) { prov = owned(new PlugProvider(factory, ci, true)); break; }
    if (!prov || !prov->initialize()) { fprintf(stderr, "no audio class / init failed\n"); return 1; }
    IPtr<IComponent> comp = prov->getComponentPtr();
    IPtr<IEditController> ctl = prov->getControllerPtr();
    FUnknownPtr<IAudioProcessor> proc(comp);
    FUnknownPtr<IMidiMapping> mm(ctl);
    if (!proc) { fprintf(stderr, "no IAudioProcessor\n"); return 1; }

    ProcessSetup ps{kOffline, kSample32, 512, rate};
    if (proc->setupProcessing(ps) != kResultOk) { fprintf(stderr, "setupProcessing\n"); return 1; }
    for (int32 i = 0; i < comp->getBusCount(kAudio, kOutput); i++) comp->activateBus(kAudio, kOutput, i, i == 0);
    for (int32 i = 0; i < comp->getBusCount(kEvent, kInput); i++) comp->activateBus(kEvent, kInput, i, i == 0);
    if (engine >= 0) {
        MemoryStream ms;
        if (comp->getState(&ms) != kResultOk) { fprintf(stderr, "getState\n"); return 1; }
        char *b = ms.getData();
        int64 len = ms.getSize();
        const char *key = "\"engine\":\"";
        size_t kl = strlen(key);
        int64 i = 0;
        while (i + (int64)kl < len && memcmp(b + i, key, kl)) i++;
        if (i + (int64)kl >= len) { fprintf(stderr, "no \"engine\" in the state\n"); return 1; }
        b[i + kl] = engine ? '1' : '0';
        ms.seek(0, IBStream::kIBSeekSet, nullptr);
        if (comp->setState(&ms) != kResultOk) { fprintf(stderr, "setState\n"); return 1; }
        ms.seek(0, IBStream::kIBSeekSet, nullptr);
        if (ctl) ctl->setComponentState(&ms);
    }
    uint32 lat_in = proc->getLatencySamples();   /* (a host may ask before setActive: docs/PLUGIN.md "latency") */
    if (comp->setActive(true) != kResultOk) { fprintf(stderr, "setActive\n"); return 1; }
    proc->setProcessing(true);
    fprintf(stderr, "vst3_render: latency %u before setActive, %u after (%.0f Hz)\n", (unsigned)lat_in,
            (unsigned)proc->getLatencySamples(), rate);

    HostProcessData pd;
    pd.prepare(*comp, 512, kSample32);
    EventList events(1024);
    ParameterChanges inp(256), outp(256);
    pd.inputEvents = &events;
    pd.inputParameterChanges = &inp;
    pd.outputParameterChanges = &outp;
    pd.processContext = nullptr;
    std::vector<float> out((size_t)total * 2, 0.0f);
    long pos = 0;
    size_t ei = 0;
    int ccmiss = 0;
    while (pos < total) {
        events.clear();
        inp.clearQueue();
        outp.clearQueue();
        int32 n = (int32)(total - pos < 512 ? total - pos : 512);
        if (ei < ev.size() && ev[ei].fr > pos && ev[ei].fr - pos < n) n = (int32)(ev[ei].fr - pos);
        while (ei < ev.size() && ev[ei].fr <= pos) {
            const Ev &e = ev[ei++];
            int t = e.st & 0xF0, ch = e.st & 15;
            if (t == 0x90 || t == 0x80) {
                Event ve{};
                ve.busIndex = 0;
                ve.sampleOffset = 0;
                if (t == 0x90 && e.d2) {
                    ve.type = Event::kNoteOnEvent;
                    ve.noteOn = {(int16)ch, (int16)e.d1, 0.f, e.d2 / 127.f, 0, -1};
                } else {
                    ve.type = Event::kNoteOffEvent;
                    ve.noteOff = {(int16)ch, (int16)e.d1, e.d2 / 127.f, -1, 0.f};
                }
                events.addEvent(ve);
            } else if (t == 0xB0) {
                ParamID id;
                if (!mm || mm->getMidiControllerAssignment(0, (int16)ch, (CtrlNumber)e.d1, id) != kResultTrue) {
                    ccmiss++;
                    continue;
                }
                int32 qi, pi;
                if (IParamValueQueue *q = inp.addParameterData(id, qi)) q->addPoint(0, e.d2 / 127.0, pi);
            }
        }
        pd.numSamples = n;
        if (proc->process(pd) != kResultOk) { fprintf(stderr, "process failed at %ld\n", pos); return 1; }
        float *l = pd.outputs[0].channelBuffers32[0], *r = pd.outputs[0].channelBuffers32[1];
        for (int32 i = 0; i < n; i++) { out[2 * (pos + i)] = l[i]; out[2 * (pos + i) + 1] = r[i]; }
        pos += n;
    }
    proc->setProcessing(false);
    comp->setActive(false);
    if (ccmiss) { fprintf(stderr, "%d controllers had no IMidiMapping parameter\n", ccmiss); return 1; }
    FILE *o = fopen(argv[5], "wb");
    if (!o || fwrite(out.data(), 4, out.size(), o) != out.size() || fclose(o)) { perror(argv[5]); return 1; }
    return 0;
}
