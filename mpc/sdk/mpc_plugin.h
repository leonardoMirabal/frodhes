// mpc_plugin.h - A tiny, header-only plugin framework for MPC standalone plugins.
//
// SPDX-License-Identifier: MIT
//
// You write a class that derives from mpc::Plugin, declare your parameters in
// its constructor, implement process(), and finish the file with
// MPC_PLUGIN_ENTRY(YourClass). This header turns that into a VST2-compatible
// shared library (it implements the AEffect dispatcher for you).
//
// Design rules baked in here (do not fight them):
//   * The host may call the dispatcher from a UI thread while process() runs on
//     the audio thread. Parameter values are atomics. A state load writes the
//     parameters and calls loadExtraState() on the UI thread, then reset() is
//     called on the audio thread before the next block: keep extra state in a
//     staging copy inside loadExtraState() and swap it in from reset().
//     Nothing here locks inside process().
//   * process() must never allocate, lock, log, or touch the filesystem.
//   * Denormals are flushed to zero around every process() call on every CPU.
//   * Only one symbol is exported from the library: VSTPluginMain.

#ifndef MPC_PLUGIN_H
#define MPC_PLUGIN_H

#include "mpc_vst2_abi.h"
#include "mpc_resources.h"

#include <atomic>
#include <cstdarg>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#if defined(_WIN32)
  #define MPC_EXPORT __declspec(dllexport)
#else
  #define MPC_EXPORT __attribute__((visibility("default")))
#endif

namespace mpc {

// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------

inline void copyString(char* dst, size_t dstSize, const char* src)
{
    if (dst == nullptr || dstSize == 0) return;
    size_t n = 0;
    while (src != nullptr && src[n] != '\0' && n + 1 < dstSize) { dst[n] = src[n]; ++n; }
    dst[n] = '\0';
}

// Logs to stderr. Safe to call from any thread EXCEPT the audio thread.
inline void log(const char* fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    std::fprintf(stderr, "[plugin] ");
    std::vfprintf(stderr, fmt, args);
    std::fprintf(stderr, "\n");
    va_end(args);
}

template <typename T> inline T clamp(T v, T lo, T hi) { return v < lo ? lo : (v > hi ? hi : v); }

// Flush-to-zero / denormals-are-zero for the duration of a scope.
// On 64-bit MPCs the host does not do this for you, and denormals in feedback
// paths (filters, delays, reverbs) can make a plugin 10-50x slower.
class ScopedDenormalsOff
{
public:
    ScopedDenormalsOff()
    {
#if defined(__aarch64__)
        uint64_t fpcr;
        __asm__ __volatile__("mrs %0, fpcr" : "=r"(fpcr));
        saved_ = fpcr;
        fpcr |= (1u << 24); // FZ
        __asm__ __volatile__("msr fpcr, %0" : : "r"(fpcr));
#elif defined(__arm__)
        uint32_t fpscr;
        __asm__ __volatile__("vmrs %0, fpscr" : "=r"(fpscr));
        saved_ = fpscr;
        fpscr |= (1u << 24); // FZ
        __asm__ __volatile__("vmsr fpscr, %0" : : "r"(fpscr));
#elif defined(__SSE__) || defined(_M_X64) || defined(__x86_64__)
        saved_ = getMxcsr();
        setMxcsr(static_cast<uint32_t>(saved_) | 0x8040u); // FTZ | DAZ
#else
        saved_ = 0;
#endif
    }
    ~ScopedDenormalsOff()
    {
#if defined(__aarch64__)
        uint64_t v = saved_;
        __asm__ __volatile__("msr fpcr, %0" : : "r"(v));
#elif defined(__arm__)
        uint32_t v = static_cast<uint32_t>(saved_);
        __asm__ __volatile__("vmsr fpscr, %0" : : "r"(v));
#elif defined(__SSE__) || defined(_M_X64) || defined(__x86_64__)
        setMxcsr(static_cast<uint32_t>(saved_));
#endif
    }
private:
#if defined(__SSE__) || defined(_M_X64) || defined(__x86_64__)
    static uint32_t getMxcsr() { uint32_t v; __asm__ __volatile__("stmxcsr %0" : "=m"(v)); return v; }
    static void setMxcsr(uint32_t v) { __asm__ __volatile__("ldmxcsr %0" : : "m"(v)); }
#endif
    uint64_t saved_ = 0;
};

// ---------------------------------------------------------------------------
// MIDI
// ---------------------------------------------------------------------------

struct MidiEvent
{
    int sampleOffset = 0;      // position inside the current block
    uint8_t data[3] = {0, 0, 0};
    uint8_t size = 0;          // 1..3

    uint8_t status()  const { return data[0] & 0xF0; }
    uint8_t channel() const { return data[0] & 0x0F; }   // 0..15
    bool isNoteOn()   const { return status() == 0x90 && data[2] != 0; }
    bool isNoteOff()  const { return status() == 0x80 || (status() == 0x90 && data[2] == 0); }
    bool isController() const { return status() == 0xB0; }
    bool isPitchBend()  const { return status() == 0xE0; }
    bool isAftertouch() const { return status() == 0xD0; }
    bool isProgramChange() const { return status() == 0xC0; }
    int note()     const { return data[1]; }
    int velocity() const { return data[2]; }
    int controller() const { return data[1]; }
    int controllerValue() const { return data[2]; }
    int pitchBend() const { return (data[1] | (data[2] << 7)) - 8192; }   // -8192..8191
    bool isAllNotesOff() const { return isController() && (data[1] == 123 || data[1] == 120); }

    static MidiEvent noteOn(int ch, int note, int vel, int offset = 0)
    { MidiEvent e; e.sampleOffset = offset; e.size = 3; e.data[0] = (uint8_t)(0x90 | (ch & 15)); e.data[1] = (uint8_t)note; e.data[2] = (uint8_t)vel; return e; }
    static MidiEvent noteOff(int ch, int note, int offset = 0)
    { MidiEvent e; e.sampleOffset = offset; e.size = 3; e.data[0] = (uint8_t)(0x80 | (ch & 15)); e.data[1] = (uint8_t)note; e.data[2] = 0; return e; }
    static MidiEvent cc(int ch, int controller, int value, int offset = 0)
    { MidiEvent e; e.sampleOffset = offset; e.size = 3; e.data[0] = (uint8_t)(0xB0 | (ch & 15)); e.data[1] = (uint8_t)controller; e.data[2] = (uint8_t)value; return e; }
};

inline float noteToHz(float midiNote, float a4 = 440.0f) { return a4 * std::pow(2.0f, (midiNote - 69.0f) / 12.0f); }

// ---------------------------------------------------------------------------
// Transport information from the host (valid only inside process())
// ---------------------------------------------------------------------------

struct Transport
{
    bool   valid = false;        // false if the host gave us nothing
    bool   playing = false;
    bool   recording = false;
    bool   cycling = false;
    bool   tempoValid = false;
    bool   ppqValid = false;
    double bpm = 120.0;
    double ppqPos = 0.0;         // song position in quarter notes
    double barStartPpq = 0.0;
    double cycleStartPpq = 0.0;
    double cycleEndPpq = 0.0;
    double samplePos = 0.0;
    int    timeSigNumerator = 4;
    int    timeSigDenominator = 4;
};

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

struct Param
{
    enum class Kind { Float, Int, Bool, Choice };

    Kind kind = Kind::Float;
    std::string id;          // stable machine name, used in saved state. Never rename.
    std::string name;        // shown on the MPC screen (keep it short, ~12 chars)
    std::string unit;        // "Hz", "dB", "%", "ms" ...
    float min = 0.0f;
    float max = 1.0f;
    float def = 0.0f;
    float skew = 1.0f;       // 1 = linear, <1 gives more resolution at the low end
    bool  logarithmic = false;
    int   decimals = 2;
    bool  automatable = true;
    std::vector<std::string> choices;   // for Kind::Choice

    // --- mapping between the 0..1 host range and real ("plain") values -------
    float toPlain(float norm) const
    {
        norm = clamp(norm, 0.0f, 1.0f);
        switch (kind)
        {
            case Kind::Bool:   return norm >= 0.5f ? 1.0f : 0.0f;
            case Kind::Choice: { int n = (int)choices.size(); if (n < 2) return 0.0f;
                                 return (float)clamp((int)std::lround(norm * (float)(n - 1)), 0, n - 1); }
            case Kind::Int:    return std::round(min + norm * (max - min));
            case Kind::Float:
            default:
                if (logarithmic && min > 0.0f && max > 0.0f)
                    return min * std::pow(max / min, norm);
                if (skew != 1.0f)
                    return min + (max - min) * std::pow(norm, 1.0f / skew);
                return min + (max - min) * norm;
        }
    }

    float toNormalised(float plain) const
    {
        switch (kind)
        {
            case Kind::Bool:   return plain >= 0.5f ? 1.0f : 0.0f;
            case Kind::Choice: { int n = (int)choices.size(); if (n < 2) return 0.0f;
                                 return clamp(plain / (float)(n - 1), 0.0f, 1.0f); }
            case Kind::Int:
            case Kind::Float:
            default:
            {
                if (max == min) return 0.0f;
                plain = clamp(plain, std::fmin(min, max), std::fmax(min, max));
                if (kind == Kind::Float && logarithmic && min > 0.0f && max > 0.0f)
                    return clamp(std::log(plain / min) / std::log(max / min), 0.0f, 1.0f);
                float lin = (plain - min) / (max - min);
                if (kind == Kind::Float && skew != 1.0f)
                    return clamp(std::pow(lin, skew), 0.0f, 1.0f);
                return clamp(lin, 0.0f, 1.0f);
            }
        }
    }

    // Text shown on screen for a plain value (without the unit).
    std::string display(float plain) const
    {
        char buf[64];
        switch (kind)
        {
            case Kind::Bool:   return plain >= 0.5f ? "On" : "Off";
            case Kind::Choice: { int i = clamp((int)std::lround(plain), 0, (int)choices.size() - 1);
                                 return choices.empty() ? std::string() : choices[(size_t)i]; }
            case Kind::Int:    std::snprintf(buf, sizeof(buf), "%d", (int)std::lround(plain)); return buf;
            case Kind::Float:
            default:
            {
                std::snprintf(buf, sizeof(buf), "%.*f", decimals, (double)plain);
                if (buf[0] == '-') // turn "-0.0" into "0.0"
                {
                    bool allZero = true;
                    for (const char* q = buf + 1; *q; ++q) if (*q != '0' && *q != '.') { allZero = false; break; }
                    if (allZero) return std::string(buf + 1);
                }
                return buf;
            }
        }
    }
};

// Convenience constructors ----------------------------------------------------
inline Param floatParam(const char* id, const char* name, float min, float max, float def,
                        const char* unit = "", int decimals = 2, float skew = 1.0f)
{
    Param p; p.kind = Param::Kind::Float; p.id = id; p.name = name; p.unit = unit;
    p.min = min; p.max = max; p.def = def; p.decimals = decimals; p.skew = skew; return p;
}
// Logarithmic mapping: good for frequencies (20..20000 Hz) and times (1..5000 ms). min must be > 0.
inline Param logParam(const char* id, const char* name, float min, float max, float def,
                      const char* unit = "", int decimals = 1)
{
    Param p = floatParam(id, name, min, max, def, unit, decimals); p.logarithmic = true; return p;
}
inline Param intParam(const char* id, const char* name, int min, int max, int def, const char* unit = "")
{
    Param p; p.kind = Param::Kind::Int; p.id = id; p.name = name; p.unit = unit;
    p.min = (float)min; p.max = (float)max; p.def = (float)def; p.decimals = 0; return p;
}
inline Param boolParam(const char* id, const char* name, bool def)
{
    Param p; p.kind = Param::Kind::Bool; p.id = id; p.name = name; p.min = 0; p.max = 1; p.def = def ? 1.0f : 0.0f; return p;
}
inline Param choiceParam(const char* id, const char* name, std::vector<std::string> choices, int def = 0)
{
    Param p; p.kind = Param::Kind::Choice; p.id = id; p.name = name; p.choices = std::move(choices);
    p.min = 0; p.max = (float)(p.choices.empty() ? 0 : p.choices.size() - 1); p.def = (float)def; return p;
}

// ---------------------------------------------------------------------------
// Plugin description
// ---------------------------------------------------------------------------

struct PluginInfo
{
    const char* name = "Untitled";       // plugin name as shown on the MPC
    const char* vendor = "Unknown";      // your name or brand (the "manufacturer")
    uint32_t    uniqueId = MPC_FOURCC('N','o','I','d');  // four-char code, must be unique per plugin
    int         version = 1000;          // 1000 = 1.0.0.0
    bool        isInstrument = false;    // true = synth/drum machine/sequencer, false = effect
    int         numInputs = 2;           // audio inputs (effects: 2; instruments usually 0 or 2)
    int         numOutputs = 2;          // audio outputs (2 = stereo)
    bool        wantsMidi = false;       // effects that react to MIDI set this; instruments always get MIDI
    bool        sendsMidi = false;       // MIDI output (note: the MPC host currently ignores plugin MIDI output)
    int         latencySamples = 0;
    int         tailSamples = 0;         // 0 = unknown, 1 = no tail, n = n samples of decay after input stops
};

struct ProcessContext
{
    const float* const* inputs = nullptr;   // inputs[channel][frame]; may be nullptr when numInputs == 0
    float* const* outputs = nullptr;        // outputs[channel][frame]. Output channels that are not also
                                            // inputs are cleared before process(). Inputs and outputs are
                                            // USUALLY THE SAME BUFFERS (in-place): read the input sample(s)
                                            // of a frame before writing that frame's output, and never read
                                            // ahead into later input frames after writing earlier outputs.
    int numInputs = 0;
    int numOutputs = 0;
    int numFrames = 0;
    const MidiEvent* midi = nullptr;        // sorted by sampleOffset
    int numMidiEvents = 0;
    double sampleRate = 44100.0;
    Transport transport;
};

// A built-in preset ("program" in VST2 language).
struct Program
{
    std::string name;
    std::vector<float> plainValues;         // one per parameter, in parameter order
};

// ---------------------------------------------------------------------------
// The base class
// ---------------------------------------------------------------------------

class Plugin
{
public:
    explicit Plugin(const PluginInfo& info) : info_(info) {}
    virtual ~Plugin() { for (std::atomic<float>* v : values_) delete v; }

    // ----- things you implement --------------------------------------------
    // Called before processing starts and whenever sample rate / block size changes.
    virtual void prepare(double sampleRate, int maxBlockSize) { (void)sampleRate; (void)maxBlockSize; }
    // Clear delay lines, voices, envelopes. Called on resume and after a state load.
    virtual void reset() {}
    // The audio callback. Real-time: no allocation, locks, I/O, or logging.
    virtual void process(ProcessContext& ctx) = 0;
    // Optional: react immediately when a parameter changes (called from any thread; keep it trivial).
    virtual void parameterChanged(int index, float plainValue) { (void)index; (void)plainValue; }
    // Optional: extra state beyond parameters (sequencer patterns, sample data...).
    virtual void saveExtraState(std::vector<uint8_t>& out) { (void)out; }
    virtual void loadExtraState(const uint8_t* data, size_t size) { (void)data; (void)size; }
    // Optional: custom value text. Return false to use the default formatting.
    virtual bool formatValue(int index, float plainValue, std::string& out) { (void)index; (void)plainValue; (void)out; return false; }

    // ----- parameter declaration (call from your constructor) --------------
    int addParam(const Param& p)
    {
        params_.push_back(p);
        values_.push_back(new std::atomic<float>(p.toNormalised(p.def)));
        return (int)params_.size() - 1;
    }
    // Optional built-in presets. Values are plain values in parameter order.
    void addProgram(const char* name, std::vector<float> plainValues)
    {
        Program pr; pr.name = name; pr.plainValues = std::move(plainValues); programs_.push_back(std::move(pr));
    }

    // ----- parameter access (safe from any thread) ------------------------
    int numParams() const { return (int)params_.size(); }
    const Param& param(int i) const { return params_[(size_t)i]; }
    float getNormalised(int i) const { return values_[(size_t)i]->load(std::memory_order_relaxed); }
    float get(int i) const { return params_[(size_t)i].toPlain(getNormalised(i)); }       // plain value
    bool  getBool(int i) const { return get(i) >= 0.5f; }
    int   getInt(int i) const { return (int)std::lround(get(i)); }
    void  setNormalised(int i, float norm)
    {
        if (i < 0 || i >= numParams()) return;
        norm = clamp(norm, 0.0f, 1.0f);
        values_[(size_t)i]->store(norm, std::memory_order_relaxed);
        parameterChanged(i, params_[(size_t)i].toPlain(norm));
    }
    void  set(int i, float plain) { if (i >= 0 && i < numParams()) setNormalised(i, params_[(size_t)i].toNormalised(plain)); }

    // Tell the host a parameter moved (e.g. from a MIDI CC). Not from the audio thread if avoidable.
    void notifyHostParameterChanged(int i)
    {
        if (host_ && effect_) host_(effect_, audioMasterAutomate, i, 0, nullptr, getNormalised(i));
    }
    // Ask the host to re-read parameter names/values (needed if your names change dynamically).
    void requestDisplayUpdate() { if (host_ && effect_) host_(effect_, audioMasterUpdateDisplay, 0, 0, nullptr, 0.0f); }
    // Queue a MIDI event to send to the host at the end of this process() call.
    void sendMidi(const MidiEvent& e) { if (numMidiOut_ < kMaxMidiEvents) midiOut_[numMidiOut_++] = e; }
    // Change latency after construction.
    void setLatency(int samples)
    {
        info_.latencySamples = samples;
        if (effect_) { effect_->initialDelay = samples; if (host_) host_(effect_, audioMasterIOChanged, 0, 0, nullptr, 0.0f); }
    }

    const PluginInfo& info() const { return info_; }
    double sampleRate() const { return sampleRate_; }
    int    maxBlockSize() const { return blockSize_; }

    // ----- glue, used by the dispatcher below (do not call yourself) -------
    AEffect* effect_ = nullptr;
    audioMasterCallback host_ = nullptr;

    void internalSetSampleRate(double sr) { sampleRate_ = sr; prepared_ = false; }
    void internalSetBlockSize(int bs) { blockSize_ = bs; prepared_ = false; }
    void internalResume()
    {
        if (!prepared_) { prepare(sampleRate_, blockSize_); prepared_ = true; }
        reset();
        numMidiIn_ = 0;
    }
    void internalSuspend() {}

    void internalAddMidi(const VstEvents* events)
    {
        if (events == nullptr) return;
        for (int i = 0; i < events->numEvents; ++i)
        {
            const VstEvent* ev = events->events[i];
            if (ev == nullptr || ev->type != kVstMidiType) continue;
            const VstMidiEvent* me = reinterpret_cast<const VstMidiEvent*>(ev);
            if (numMidiIn_ >= kMaxMidiEvents) break;
            MidiEvent& e = midiIn_[numMidiIn_++];
            e.sampleOffset = me->deltaFrames < 0 ? 0 : me->deltaFrames;
            uint8_t status = (uint8_t)me->midiData[0];
            e.data[0] = status; e.data[1] = (uint8_t)me->midiData[1]; e.data[2] = (uint8_t)me->midiData[2];
            uint8_t hi = status & 0xF0;
            if (status >= 0xF0)      e.size = (status == 0xF2) ? 3 : ((status == 0xF1 || status == 0xF3) ? 2 : 1);
            else if (hi == 0xC0 || hi == 0xD0) e.size = 2;
            else                     e.size = 3;
        }
    }

    void internalProcess(float** inputs, float** outputs, int numFrames)
    {
        ScopedDenormalsOff denormalsOff;
        // Hosts normally prepare (sample rate, block size, resume) before processing, so this
        // lazy prepare only runs if a host changes the sample rate without suspending first.
        if (!prepared_) { prepare(sampleRate_, blockSize_ > 0 ? blockSize_ : numFrames); prepared_ = true; }
        if (stateLoadPending_.exchange(false, std::memory_order_acq_rel)) reset();
        if (numFrames > blockSize_) blockSize_ = numFrames;

        ProcessContext ctx;
        ctx.inputs = inputs; ctx.outputs = outputs;
        ctx.numInputs = info_.numInputs; ctx.numOutputs = info_.numOutputs;
        ctx.numFrames = numFrames;
        // Sort MIDI by offset (insertion sort; events arrive nearly sorted and are few).
        for (int i = 1; i < numMidiIn_; ++i)
        {
            MidiEvent key = midiIn_[i]; int j = i - 1;
            while (j >= 0 && midiIn_[j].sampleOffset > key.sampleOffset) { midiIn_[j + 1] = midiIn_[j]; --j; }
            midiIn_[j + 1] = key;
        }
        for (int i = 0; i < numMidiIn_; ++i) if (midiIn_[i].sampleOffset >= numFrames) midiIn_[i].sampleOffset = numFrames - 1;
        ctx.midi = midiIn_; ctx.numMidiEvents = numMidiIn_;
        ctx.sampleRate = sampleRate_;
        ctx.transport = fetchTransport();

        // Clear output buffers that do not alias an input buffer. Hosts (including the MPC)
        // usually process in place, passing the same buffers as inputs and outputs, so
        // those must be left intact for process() to read.
        for (int c = 0; c < info_.numOutputs; ++c)
        {
            if (outputs[c] == nullptr) continue;
            bool aliasesInput = false;
            for (int i = 0; i < info_.numInputs && inputs != nullptr; ++i)
                if (inputs[i] == outputs[c]) { aliasesInput = true; break; }
            if (!aliasesInput) std::memset(outputs[c], 0, sizeof(float) * (size_t)numFrames);
        }

        numMidiOut_ = 0;
        process(ctx);
        numMidiIn_ = 0;
        flushMidiOut();
    }

    // State: "MPCP" | u32 version(2) | u32 nParams | { u8 idLen, id, f32 plain, f32 normalised }* | u32 extraLen | extra
    // Version 1 chunks (no normalised value) are still accepted. On load the normalised value is used when it
    // agrees with the plain value (exact round trip); otherwise the plain value wins (the parameter range changed).
    const std::vector<uint8_t>& internalSaveState()
    {
        std::vector<uint8_t>& out = stateBuffer_;
        out.clear();
        auto putU32 = [&out](uint32_t v) { for (int i = 0; i < 4; ++i) out.push_back((uint8_t)(v >> (8 * i))); };
        auto putF32 = [&out](float f) { uint32_t v; std::memcpy(&v, &f, 4); for (int i = 0; i < 4; ++i) out.push_back((uint8_t)(v >> (8 * i))); };
        out.push_back('M'); out.push_back('P'); out.push_back('C'); out.push_back('P');
        putU32(2);
        putU32((uint32_t)params_.size());
        for (int i = 0; i < numParams(); ++i)
        {
            const std::string& id = params_[(size_t)i].id;
            out.push_back((uint8_t)clamp<size_t>(id.size(), 0, 255));
            for (size_t k = 0; k < id.size() && k < 255; ++k) out.push_back((uint8_t)id[k]);
            putF32(get(i));
            putF32(getNormalised(i));
        }
        std::vector<uint8_t> extra;
        saveExtraState(extra);
        putU32((uint32_t)extra.size());
        out.insert(out.end(), extra.begin(), extra.end());
        return out;
    }

    bool internalLoadState(const uint8_t* data, size_t size)
    {
        if (data == nullptr || size < 12 || std::memcmp(data, "MPCP", 4) != 0) return false;
        size_t pos = 4;
        auto getU32 = [&](uint32_t& v) { if (pos + 4 > size) return false; v = 0; for (int i = 0; i < 4; ++i) v |= (uint32_t)data[pos + (size_t)i] << (8 * i); pos += 4; return true; };
        uint32_t version = 0, n = 0;
        if (!getU32(version) || !getU32(n)) return false;
        for (uint32_t k = 0; k < n; ++k)
        {
            if (pos >= size) return false;
            uint8_t idLen = data[pos++];
            if (pos + idLen + 4 > size) return false;
            std::string id(reinterpret_cast<const char*>(data + pos), idLen); pos += idLen;
            uint32_t bits; getU32(bits); float plain; std::memcpy(&plain, &bits, 4);
            float norm = -1.0f;
            if (version >= 2) { if (pos + 4 > size) return false; getU32(bits); std::memcpy(&norm, &bits, 4); }
            for (int i = 0; i < numParams(); ++i)
            {
                if (params_[(size_t)i].id != id) continue;
                const Param& pr = params_[(size_t)i];
                if (norm >= 0.0f && norm <= 1.0f && std::fabs(pr.toPlain(norm) - plain) <= 1e-4f * (1.0f + std::fabs(plain)))
                    setNormalised(i, norm);
                else
                    set(i, plain);
                break;
            }
        }
        uint32_t extraLen = 0;
        if (getU32(extraLen) && pos + extraLen <= size && extraLen > 0) loadExtraState(data + pos, extraLen);
        stateLoadPending_.store(true, std::memory_order_release);
        return true;
    }

    int numPrograms() const { return programs_.empty() ? 1 : (int)programs_.size(); }
    int currentProgram() const { return currentProgram_; }
    const char* programName(int i) const
    {
        if (programs_.empty()) return "Default";
        if (i < 0 || i >= (int)programs_.size()) return "";
        return programs_[(size_t)i].name.c_str();
    }
    void setProgram(int i)
    {
        if (programs_.empty() || i < 0 || i >= (int)programs_.size()) return;
        currentProgram_ = i;
        const Program& p = programs_[(size_t)i];
        for (int k = 0; k < numParams() && k < (int)p.plainValues.size(); ++k) set(k, p.plainValues[(size_t)k]);
        stateLoadPending_.store(true, std::memory_order_release);
    }

    std::string displayText(int i)
    {
        std::string s;
        float plain = get(i);
        if (!formatValue(i, plain, s)) s = params_[(size_t)i].display(plain);
        return s;
    }

protected:
    static const int kMaxMidiEvents = 1024;

    Transport fetchTransport()
    {
        Transport t;
        if (!host_ || !effect_) return t;
        VstIntPtr r = host_(effect_, audioMasterGetTime, 0,
                            kVstTempoValid | kVstPpqPosValid | kVstBarsValid | kVstCyclePosValid | kVstTimeSigValid,
                            nullptr, 0.0f);
        if (r == 0) return t;
        const VstTimeInfo* ti = reinterpret_cast<const VstTimeInfo*>(r);
        t.valid = true;
        t.playing = (ti->flags & kVstTransportPlaying) != 0;
        t.recording = (ti->flags & kVstTransportRecording) != 0;
        t.cycling = (ti->flags & kVstTransportCycleActive) != 0;
        t.tempoValid = (ti->flags & kVstTempoValid) != 0;
        t.ppqValid = (ti->flags & kVstPpqPosValid) != 0;
        if (t.tempoValid) t.bpm = ti->tempo;
        if (t.ppqValid) t.ppqPos = ti->ppqPos;
        if (ti->flags & kVstBarsValid) t.barStartPpq = ti->barStartPos;
        if (ti->flags & kVstCyclePosValid) { t.cycleStartPpq = ti->cycleStartPos; t.cycleEndPpq = ti->cycleEndPos; }
        if (ti->flags & kVstTimeSigValid) { t.timeSigNumerator = ti->timeSigNumerator; t.timeSigDenominator = ti->timeSigDenominator; }
        t.samplePos = ti->samplePos;
        return t;
    }

    void flushMidiOut()
    {
        if (numMidiOut_ == 0 || !host_ || !effect_ || !info_.sendsMidi) return;
        // VstEvents has room for 2 pointers; we reserve storage for kMaxMidiEvents.
        for (int i = 0; i < numMidiOut_; ++i)
        {
            VstMidiEvent& me = midiOutVst_[i];
            std::memset(&me, 0, sizeof(me));
            me.type = kVstMidiType; me.byteSize = sizeof(VstMidiEvent);
            me.deltaFrames = midiOut_[i].sampleOffset;
            me.midiData[0] = (char)midiOut_[i].data[0]; me.midiData[1] = (char)midiOut_[i].data[1]; me.midiData[2] = (char)midiOut_[i].data[2];
            midiOutPtrs_[i] = reinterpret_cast<VstEvent*>(&me);
        }
        VstEvents* evs = reinterpret_cast<VstEvents*>(midiOutEventsStorage_);
        evs->numEvents = numMidiOut_; evs->reserved = 0;
        std::memcpy(evs->events, midiOutPtrs_, sizeof(VstEvent*) * (size_t)numMidiOut_);
        host_(effect_, audioMasterProcessEvents, 0, 0, evs, 0.0f);
        numMidiOut_ = 0;
    }

    PluginInfo info_;
    std::vector<Param> params_;
    std::vector<std::atomic<float>*> values_;   // heap-allocated so the vector can grow during construction
    std::vector<Program> programs_;
    int currentProgram_ = 0;
    double sampleRate_ = 44100.0;
    int blockSize_ = 128;
    bool prepared_ = false;
    std::atomic<bool> stateLoadPending_{false};
    std::vector<uint8_t> stateBuffer_;
    MidiEvent midiIn_[kMaxMidiEvents];
    int numMidiIn_ = 0;
    MidiEvent midiOut_[kMaxMidiEvents];
    VstMidiEvent midiOutVst_[kMaxMidiEvents];
    VstEvent* midiOutPtrs_[kMaxMidiEvents];
    alignas(VstEvents) char midiOutEventsStorage_[sizeof(VstEvents) + sizeof(VstEvent*) * kMaxMidiEvents];
    int numMidiOut_ = 0;
};

// ---------------------------------------------------------------------------
// The VST2 dispatcher: translates host opcodes into calls on your Plugin.
// ---------------------------------------------------------------------------
namespace detail {

inline Plugin* self(AEffect* e) { return static_cast<Plugin*>(e->object); }

inline VstIntPtr dispatcher(AEffect* e, VstInt32 opcode, VstInt32 index, VstIntPtr value, void* ptr, float opt)
{
    Plugin* p = self(e);
    if (p == nullptr) return 0;
    char* str = static_cast<char*>(ptr);
    const int kStr = 32; // we never write more than this many chars (+NUL) into host string buffers

    switch (opcode)
    {
        case effOpen: return 0;
        case effClose:
            delete p;
            e->object = nullptr;
            std::free(e);
            return 0;

        case effSetProgram: p->setProgram((int)value); return 0;
        case effGetProgram: return p->currentProgram();
        case effSetProgramName: return 0;
        case effGetProgramName: copyString(str, kVstMaxProgNameLen + 1, p->programName(p->currentProgram())); return 0;
        case effGetProgramNameIndexed:
            if (index < 0 || index >= p->numPrograms()) return 0;
            copyString(str, kVstMaxProgNameLen + 1, p->programName(index)); return 1;

        case effGetParamLabel:
            if (index < 0 || index >= p->numParams()) return 0;
            copyString(str, kStr, p->param(index).unit.c_str()); return 0;
        case effGetParamDisplay:
            if (index < 0 || index >= p->numParams()) return 0;
            copyString(str, kStr, p->displayText(index).c_str()); return 0;
        case effGetParamName:
            if (index < 0 || index >= p->numParams()) return 0;
            copyString(str, kStr, p->param(index).name.c_str()); return 0;
        case effCanBeAutomated:
            return (index >= 0 && index < p->numParams() && p->param(index).automatable) ? 1 : 0;
        case effString2Parameter:
        {
            if (index < 0 || index >= p->numParams() || str == nullptr) return 0;
            const Param& pr = p->param(index);
            if (pr.kind == Param::Kind::Choice)
            {
                for (size_t i = 0; i < pr.choices.size(); ++i)
                    if (pr.choices[i] == str) { p->set(index, (float)i); return 1; }
                return 0;
            }
            char* end = nullptr; double v = std::strtod(str, &end);
            if (end == str) return 0;
            p->set(index, (float)v); return 1;
        }
        case effGetParameterProperties:
        {
            if (index < 0 || index >= p->numParams() || ptr == nullptr) return 0;
            VstParameterProperties* pp = static_cast<VstParameterProperties*>(ptr);
            std::memset(pp, 0, sizeof(*pp));
            const Param& pr = p->param(index);
            copyString(pp->label, sizeof(pp->label), pr.name.c_str());
            copyString(pp->shortLabel, sizeof(pp->shortLabel), pr.name.c_str());
            if (pr.kind == Param::Kind::Bool) pp->flags |= kVstParameterIsSwitch;
            if (pr.kind == Param::Kind::Int || pr.kind == Param::Kind::Choice)
            { pp->flags |= kVstParameterUsesIntegerMinMax | kVstParameterUsesIntStep; pp->minInteger = (VstInt32)pr.min; pp->maxInteger = (VstInt32)pr.max; pp->stepInteger = 1; pp->largeStepInteger = 1; }
            return 1;
        }

        case effSetSampleRate: p->internalSetSampleRate((double)opt); return 0;
        case effSetBlockSize: p->internalSetBlockSize((int)value); return 0;
        case effMainsChanged: if (value != 0) p->internalResume(); else p->internalSuspend(); return 0;
        case effStartProcess: case effStopProcess: return 0;
        case effSetProcessPrecision: return value == kVstProcessPrecision32 ? 1 : 0;

        case effEditGetRect: case effEditOpen: case effEditClose: case effEditIdle: return 0;

        case effGetChunk:
        {
            if (ptr == nullptr) return 0;
            const std::vector<uint8_t>& s = p->internalSaveState();
            *static_cast<void**>(ptr) = const_cast<uint8_t*>(s.data());
            return (VstIntPtr)s.size();
        }
        case effSetChunk:
            return p->internalLoadState(static_cast<const uint8_t*>(ptr), (size_t)value) ? 1 : 0;

        case effProcessEvents: p->internalAddMidi(static_cast<const VstEvents*>(ptr)); return 1;

        case effGetInputProperties: case effGetOutputProperties:
        {
            int count = opcode == effGetInputProperties ? p->info().numInputs : p->info().numOutputs;
            if (index < 0 || index >= count || ptr == nullptr) return 0;
            VstPinProperties* pin = static_cast<VstPinProperties*>(ptr);
            std::memset(pin, 0, sizeof(*pin));
            std::snprintf(pin->label, sizeof(pin->label), "%s %d", opcode == effGetInputProperties ? "In" : "Out", index + 1);
            std::snprintf(pin->shortLabel, sizeof(pin->shortLabel), "%s%d", opcode == effGetInputProperties ? "In" : "Out", index + 1);
            pin->flags = kVstPinIsActive;
            if ((index & 1) == 0 && index + 1 < count) pin->flags |= kVstPinIsStereo;
            return 1;
        }
        case effSetSpeakerArrangement: return 0;
        case effGetPlugCategory: return p->info().isInstrument ? kPlugCategSynth : kPlugCategEffect;
        case effSetBypass: return 0;
        case effGetEffectName: copyString(str, kVstMaxEffectNameLen + 1, p->info().name); return 1;
        case effGetVendorString: copyString(str, kVstMaxVendorStrLen + 1, p->info().vendor); return 1;
        case effGetProductString: copyString(str, kVstMaxProductStrLen + 1, p->info().name); return 1;
        case effGetVendorVersion: return p->info().version;
        case effVendorSpecific: return 0;
        case effCanDo:
        {
            if (str == nullptr) return 0;
            bool receives = p->info().isInstrument || p->info().wantsMidi;
            if (!std::strcmp(str, "receiveVstEvents") || !std::strcmp(str, "receiveVstMidiEvent")) return receives ? 1 : -1;
            if (!std::strcmp(str, "sendVstEvents") || !std::strcmp(str, "sendVstMidiEvent")) return p->info().sendsMidi ? 1 : -1;
            if (!std::strcmp(str, "receiveVstTimeInfo")) return 1;
            if (!std::strcmp(str, "bypass")) return -1;
            if (!std::strcmp(str, "offline")) return -1;
            return 0;
        }
        case effGetTailSize: return p->info().tailSamples;
        case effGetVstVersion: return kVstVersion;
        case effGetNumMidiInputChannels: return (p->info().isInstrument || p->info().wantsMidi) ? 16 : 0;
        case effGetNumMidiOutputChannels: return p->info().sendsMidi ? 16 : 0;
        case effBeginSetProgram: case effEndSetProgram: return 0;
        case effBeginLoadBank: case effBeginLoadProgram: return 0;
        default: return 0;
    }
}

inline void processReplacing(AEffect* e, float** in, float** out, VstInt32 frames)
{
    Plugin* p = self(e);
    if (p != nullptr && frames > 0) p->internalProcess(in, out, (int)frames);
}

inline void setParameter(AEffect* e, VstInt32 index, float value)
{
    Plugin* p = self(e); if (p != nullptr) p->setNormalised((int)index, value);
}

inline float getParameter(AEffect* e, VstInt32 index)
{
    Plugin* p = self(e);
    return (p != nullptr && index >= 0 && index < p->numParams()) ? p->getNormalised((int)index) : 0.0f;
}

inline AEffect* createEffect(Plugin* plugin, audioMasterCallback host)
{
    AEffect* e = static_cast<AEffect*>(std::calloc(1, sizeof(AEffect)));
    if (e == nullptr) { delete plugin; return nullptr; }
    const PluginInfo& info = plugin->info();
    e->magic = kEffectMagic;
    e->dispatcher = &dispatcher;
    e->process = nullptr;
    e->setParameter = &setParameter;
    e->getParameter = &getParameter;
    e->numPrograms = plugin->numPrograms();
    e->numParams = plugin->numParams();
    e->numInputs = info.numInputs;
    e->numOutputs = info.numOutputs;
    e->flags = effFlagsCanReplacing | effFlagsProgramChunks | (info.isInstrument ? effFlagsIsSynth : 0);
    e->initialDelay = info.latencySamples;
    e->ioRatio = 1.0f;
    e->object = plugin;
    e->uniqueID = (VstInt32)info.uniqueId;
    e->version = info.version;
    e->processReplacing = &processReplacing;
    e->processDoubleReplacing = nullptr;
    plugin->effect_ = e;
    plugin->host_ = host;
    return e;
}

} // namespace detail
} // namespace mpc

// Put this at the end of your plugin's .cpp file.
#define MPC_PLUGIN_ENTRY_BODY(PluginClass)                                                   \
    {                                                                                        \
        if (host != nullptr && host(nullptr, audioMasterVersion, 0, 0, nullptr, 0.0f) == 0)  \
            return nullptr; /* host is VST1 only */                                          \
        return mpc::detail::createEffect(new PluginClass(), host);                           \
    }

#if defined(__APPLE__)
  // Older macOS VST2 hosts look for main_macho instead of VSTPluginMain; export both.
  #define MPC_PLUGIN_ENTRY(PluginClass)                                                      \
    extern "C" MPC_EXPORT AEffect* VSTPluginMain(audioMasterCallback host)                   \
    MPC_PLUGIN_ENTRY_BODY(PluginClass)                                                       \
    extern "C" MPC_EXPORT AEffect* main_macho(audioMasterCallback host)                      \
    { return VSTPluginMain(host); }
#else
  #define MPC_PLUGIN_ENTRY(PluginClass)                                                      \
    extern "C" MPC_EXPORT AEffect* VSTPluginMain(audioMasterCallback host)                   \
    MPC_PLUGIN_ENTRY_BODY(PluginClass)
#endif

#endif // MPC_PLUGIN_H
