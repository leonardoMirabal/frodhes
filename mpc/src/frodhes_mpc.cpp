/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes as an Akai MPC plugin (docs/MPC.md): the desktop plugin's engine (plugin/src/bridge.c over the slots of
 * plugin/src/engine_unit.c, the app firmware/src/frodhes.c unchanged) behind the MPC Plugin SDK's VST 2.4 framework
 * (mpc/sdk/mpc_plugin.h, class mpc::Plugin). Nothing of the engine is forked: this file is what
 * plugin/src/frodhes_clap.c is for CLAP, without the window (the MPC draws its own pages from
 * "Plugin Skins": mpc/skins/, drawn by mpc/tools/make_skin.py).
 *
 *   audio        no input, one stereo output; 44.1 kHz on the MPC (the bridge's own rate: its samples as they are),
 *                any other rate through the bridge's resamplers, as the desktop plugin
 *   MIDI         notes, velocity, CC (the sustain pedal CC 64 and the FM-1's CC map), pitch bend, program changes,
 *                each at its sample offset in the block (the bridge's exact time, as the desktop plugin)
 *   parameters   the desktop plugin's 72 (plugin/gen/params_map.h) in its order: the app's 67 with their FM-1 ranges
 *                and texts, then TUNE, PRESET, SUSTAIN, EFFECTS BYPASS, VELOCITY RESPONSE. The host sees 0..1; a value
 *                is v / hi (v = 0..hi, the texts the desktop plugin shows). The id strings ("p<FM-1 id>") are stable.
 *                A host write reaches the app at the start of the next block; a change the app makes itself (a CC,
 *                a program change, a preset) is written back into the host's value
 *   programs     the 57 factory presets F01..F57 (the PRESET parameter's list): a program select loads the preset
 *                as the desktop plugin's PRESET does
 *   state        the framework's chunk (every parameter by its id) + the desktop plugin's state text
 *                (slot_bridge.h: the sound, the settings, MIDI learn, the preset's name), which wins
 *   tempo        the MPC transport's tempo for the tempo-synced effects (the settings' tempo source HOST)
 *   voices       the slots are built with 16 voices on every engine (mpc/build.py, as the Move); HQ on every engine
 *                and effect, as the desktop plugin
 * Threads (mpc_plugin.h): the host's parameter writes, state and program calls may come from its UI thread while
 * process() runs; they only touch the framework's atomics and the slot's own mailboxes, and process() takes them at
 * the start of a block. */
#include "mpc_plugin.h"

extern "C" {
#include "bridge.h"
}

#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cfenv>
#include <string>
#include <thread>
#include <vector>

#ifndef FRP_VERSION
#define FRP_VERSION "0.0.0-dev"
#endif

namespace {

/* the VST version number from FRP_VERSION's X.Y.Z (X * 10000 + Y * 100 + Z; 0 for a dev build) */
int versionNumber()
{
    int x = 0, y = 0, z = 0;
    if (std::sscanf(FRP_VERSION, "%d.%d.%d", &x, &y, &z) != 3) return 0;
    return x * 10000 + y * 100 + z;
}

mpc::PluginInfo makeInfo()
{
    mpc::PluginInfo info;
    info.name = "Frodhes";
    info.vendor = "Frodhes";                         /* the desktop plugin's vendor (frodhes_clap.c DESC) */
    info.uniqueId = MPC_FOURCC('F', 'r', 'd', '1');  /* = mpc/plugin.json "uid"; never change it */
    info.version = versionNumber();
    info.isInstrument = true;
    info.numInputs = 0;
    info.numOutputs = 2;
    info.latencySamples = 0;                         /* (set from the bridge in the constructor) */
    info.tailSamples = 0;                            /* (unknown: the reverb's depends on the settings) */
    return info;
}

const int kN = (int)FRP_NPARAM;
const int kPresetIdx = (int)FRP_NPARAM_APP + 1;     /* PRESET (0x10002): the plugin's own parameters follow the app's */
const uint32_t kForceFrames = 8192;                  /* after a state or a program: the host's values follow the app's */

} // namespace

class Frodhes : public mpc::Plugin
{
public:
    Frodhes() : mpc::Plugin(makeInfo())
    {
        in_ = frp_inst_new();
        if (in_ && frp_inst_slot(in_) < 0)
            mpc::log("Frodhes: %u instances already play: this one stays silent", (unsigned)FRP_SLOTS);
        if (in_) {
            frp_inst_activate(in_, 44100.0);         /* (the MPC's rate: the latency the host reads at the load) */
            setLatency((int)frp_inst_latency(in_));
        }
        for (int i = 0; i < kN; i++) {
            const frp_param_t &d = FRP_PARAMS[i];
            std::vector<std::string> texts;
            for (uint32_t v = 0; v <= d.hi; v++) texts.push_back(FRP_TEXT[d.text0 + v]);
            double cur = d.def;
            if (in_) (void)frp_inst_get(in_, d.id, &cur);   /* (a fresh instance's: the boot preset's values) */
            char id[16];
            std::snprintf(id, sizeof id, "p%u", (unsigned)d.id);
            mpc::Param p = mpc::choiceParam(id, d.name, texts, (int)std::lround(cur));
            p.automatable = d.id != FRP_ID_PRESET;   /* (a choice a user makes, not a curve: as the desktop plugin) */
            addParam(p);
            hostv_[i] = (int)std::lround(cur);
        }
        for (uint32_t k = 0; k < FRP_NFACTORY; k++) {   /* the programs: F01..F57, the PRESET parameter's texts */
            uint8_t vals[FRP_NPARAM_APP];
            if (frp_fact_info(k, nullptr, 0, vals)) break;
            std::vector<float> plain(vals, vals + FRP_NPARAM_APP);   /* (the app's 67; the plugin's own left alone) */
            addProgram(FRP_TEXT[FRP_PARAMS[kPresetIdx].text0 + k], plain);
        }
        /* the app's values as the host's start (echo: a change the app makes in the very first block shows too) */
        if (in_ && frp_inst_snap(in_, &last_) == 0) haveLast_ = true;
    }

    ~Frodhes() override { frp_inst_free(in_); }

    void prepare(double sampleRate, int maxBlockSize) override
    {
        (void)maxBlockSize;
        if (!in_) return;
        frp_inst_deactivate(in_);
        if (frp_inst_activate(in_, sampleRate)) {
            mpc::log("Frodhes: plays at 1 to 1536 kHz, not at %.0f Hz", sampleRate);
            return;
        }
        if ((int)frp_inst_latency(in_) != info().latencySamples) setLatency((int)frp_inst_latency(in_));
    }

    /* mpc_plugin.h calls this on a resume (the host's prepare), after a state load and after a program select */
    void reset() override
    {
        if (!in_) return;
        int prog = currentProgram();
        bool state = stateLoaded_.exchange(false);
        if (state) stateSince_ = true;
        if (prog != lastProgram_) {
            /* a program: the preset loads in the app (its name, UNDO, the PRESET value), unless it is the preset a
             * state just restored (a project's program number after its state: the edited sound stays) */
            int restored = (int)std::lround(getNormalised(kPresetIdx) * FRP_PARAMS[kPresetIdx].hi);
            if (!(stateSince_ && prog == restored)) frp_inst_put_now(in_, FRP_EV_PRESET, 0, prog);
            lastProgram_ = prog;
            stateSince_ = false;
            force_.store(kForceFrames);
        } else if (state) {
            force_.store(kForceFrames);
        } else if (programTouched()) {
            force_.store(kForceFrames);              /* (the same program again: the host's values back to the app's) */
        } else if (played_.load() && std::this_thread::get_id() != audioThread_.load()) {
            /* a resume after the instance played (on the host's thread, not a program or state the framework hands
             * over on the audio thread before a block): all notes off, the effects' state cleared. Not at the load
             * (no block yet): the sound from its first sample is the desktop plugin's */
            frp_inst_reset(in_);
        }
        played_.store(false);
    }

    void process(mpc::ProcessContext &ctx) override
    {
        float *l = ctx.outputs[0], *r = ctx.outputs[1];
        uint32_t n = (uint32_t)ctx.numFrames, tempo10 = 0;
        if (!in_) return;
        audioThread_.store(std::this_thread::get_id(), std::memory_order_relaxed);
        played_.store(true, std::memory_order_relaxed);
        if (ctx.transport.valid && ctx.transport.tempoValid && ctx.transport.bpm > 0.0)
            tempo10 = (uint32_t)std::lround(ctx.transport.bpm * 10.0);
        bool forcing = force_.load() > 0;
        /* the host's writes since the last block, at its start (the framework keeps the latest value) */
        for (int i = 0; i < kN; i++) {
            int v = hostValue(i);
            if (forcing) { hostv_[i] = v; continue; }  /* (a state or a program: the app has the values) */
            if (v == hostv_[i]) { if (i == 0) p0cand_ = -1; continue; }
            if (i == 0 && p0cand_ != v) { p0cand_ = v; continue; }   /* (TYPE: two blocks, past the host's nudge) */
            if (i == 0) p0cand_ = -1;
            hostv_[i] = v;
            frp_inst_param(in_, 0, FRP_PARAMS[i].id, (double)v);
        }
        for (int k = 0; k < ctx.numMidiEvents; k++) {
            const mpc::MidiEvent &e = ctx.midi[k];
            if (e.size < 1) continue;
            frp_inst_midi(in_, (uint32_t)e.sampleOffset, e.data[0], e.size > 1 ? e.data[1] : 0, e.size > 2 ? e.data[2] : 0);
        }
        /* the bridge in the floating-point state the desktop plugin and the host build run in (the framework flushes
         * denormals around process(); the engine's own step sets the default state itself: slot_bridge.h), so its
         * resampler's samples are theirs, bit for bit */
        fenv_t fe;
        fegetenv(&fe);
        fesetenv(FE_DFL_ENV);
        frp_inst_render(in_, l, r, n, tempo10);
        fesetenv(&fe);
        if (waitBlocks_.load() > 0) {
            /* a state loaded, the app takes it on a block: until then its values are the sound before (not echoed:
             * the host keeps the values the state brought, as saved) */
            if (frp_inst_rev(in_) != waitRev_.load()) waitBlocks_.store(0);
            else { waitBlocks_.fetch_sub(1); return; }
        }
        echo(forcing);
        if (forcing) {
            uint32_t f = force_.load();
            force_.store(f > n ? f - n : 0);
        }
    }

    void saveExtraState(std::vector<uint8_t> &out) override
    {
        char buf[FRP_STATE_MAX];
        int len;
        if (!in_) return;
        if (!pendingJson_.empty() && frp_inst_rev(in_) == pendingRev_) {   /* (a state loaded, not yet taken by the
                                                     * app: saved as it came, not the sound before it) */
            out.insert(out.end(), pendingJson_.begin(), pendingJson_.end());
            return;
        }
        pendingJson_.clear();
        len = frp_inst_state_get(in_, buf, (int)sizeof buf);
        if (len > 0) out.insert(out.end(), buf, buf + len);
    }

    void loadExtraState(const uint8_t *data, size_t size) override
    {
        if (!in_ || !data || !size || size >= (size_t)FRP_STATE_MAX * 2) return;
        std::string json(reinterpret_cast<const char *>(data), size);   /* (the UI thread: allocation is fine) */
        uint32_t rev = frp_inst_rev(in_);
        if (frp_inst_state_set(in_, json.c_str()) == 0) {
            pendingJson_ = json;
            pendingRev_ = rev;
            waitRev_.store(rev);
            waitBlocks_.store(kWaitBlocks);
            stateLoaded_.store(true);
        }
        else mpc::log("Frodhes: the saved state could not be read");
    }

private:
    int hostValue(int i) const
    {
        long v = std::lround(getNormalised(i) * (float)FRP_PARAMS[i].hi);
        return (int)(v < 0 ? 0 : v > FRP_PARAMS[i].hi ? FRP_PARAMS[i].hi : v);
    }
    static int snapValue(const frp_snap_t &s, int i)
    {
        uint32_t id = FRP_PARAMS[i].id;
        return i < (int)FRP_NPARAM_APP ? s.pv[i] : id == FRP_ID_TUNE ? s.tune : id == FRP_ID_PRESET ? s.last_fact :
               id == FRP_ID_SUSTAIN ? s.sustain : id == FRP_ID_VRESP ? s.vresp : s.bypass;
    }
    /* the app's own changes into the host's values (frodhes_clap.c p_out: a value that moved and is not what the host
     * last wrote); while forcing (after a state or a program), every value the app holds */
    void echo(bool forcing)
    {
        frp_snap_t sn;
        if (frp_inst_snap(in_, &sn)) return;
        for (int i = 0; i < kN; i++) {
            int a = snapValue(sn, i);
            if (!forcing && (!haveLast_ || a == snapValue(last_, i) || a == hostv_[i])) continue;
            if (forcing && a == hostv_[i]) continue;
            hostv_[i] = a;
            values_[(size_t)i]->store(FRP_PARAMS[i].hi ? (float)a / (float)FRP_PARAMS[i].hi : 0.0f,
                                      std::memory_order_relaxed);
        }
        last_ = sn;
        haveLast_ = true;
    }
    /* a program select to the program already chosen also lands here (the framework wrote its values) */
    bool programTouched()
    {
        for (int i = 0; i < (int)FRP_NPARAM_APP; i++) if (hostValue(i) != hostv_[i]) return true;
        return false;
    }

    frp_inst_t *in_ = nullptr;
    int hostv_[FRP_NPARAM] = {};                     /* the host's values as the app last took or set them (0..hi) */
    frp_snap_t last_{};
    bool haveLast_ = false;
    int p0cand_ = -1;
    int lastProgram_ = 0;
    bool stateSince_ = false;
    std::atomic<bool> played_{false};                /* (a block since the last reset) */
    std::atomic<std::thread::id> audioThread_{};     /* (the thread of the last block) */
    std::atomic<bool> stateLoaded_{false};
    std::string pendingJson_;                        /* (the UI thread's: the last state loaded, until the app has it) */
    uint32_t pendingRev_ = 0;
    std::atomic<uint32_t> force_{0};
    std::atomic<uint32_t> waitRev_{0};               /* (a state loaded: the app's revision before it ...) */
    std::atomic<int> waitBlocks_{0};                 /* (... and the blocks left to wait for the app to take it) */
    static constexpr int kWaitBlocks = 64;
};

MPC_PLUGIN_ENTRY(Frodhes)
