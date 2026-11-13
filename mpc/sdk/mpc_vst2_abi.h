// mpc_vst2_abi.h - A clean-room description of the VST 2.4 plugin binary interface.
//
// SPDX-License-Identifier: MIT
//
// This header contains NO code from Steinberg's VST SDK. It describes the
// well-known, publicly documented binary layout (struct fields, opcode numbers,
// flag values) that every VST2 host and plugin agrees on, in the same way that
// independent re-implementations (vestige.h, the Rust `vst` crate, and others)
// do. "VST" is a trademark of Steinberg Media Technologies GmbH.
//
// Only the parts of the interface that an MPC plugin actually needs are
// included; deprecated opcodes are listed by number for completeness so that a
// dispatcher can ignore them explicitly.

#ifndef MPC_VST2_ABI_H
#define MPC_VST2_ABI_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// ---------------------------------------------------------------------------
// Core types
// ---------------------------------------------------------------------------

typedef intptr_t VstIntPtr;
typedef int32_t  VstInt32;
typedef int16_t  VstInt16;

struct AEffect;

// Host -> plugin and plugin -> host dispatcher signatures.
typedef VstIntPtr (*audioMasterCallback)(struct AEffect* effect, VstInt32 opcode, VstInt32 index,
                                         VstIntPtr value, void* ptr, float opt);
typedef VstIntPtr (*AEffectDispatcherProc)(struct AEffect* effect, VstInt32 opcode, VstInt32 index,
                                           VstIntPtr value, void* ptr, float opt);
typedef void  (*AEffectProcessProc)(struct AEffect* effect, float** inputs, float** outputs,
                                    VstInt32 sampleFrames);
typedef void  (*AEffectProcessDoubleProc)(struct AEffect* effect, double** inputs, double** outputs,
                                          VstInt32 sampleFrames);
typedef void  (*AEffectSetParameterProc)(struct AEffect* effect, VstInt32 index, float parameter);
typedef float (*AEffectGetParameterProc)(struct AEffect* effect, VstInt32 index);

// 'VstP' - the value the host checks first when it loads a plugin.
#define kEffectMagic ((VstInt32)0x56737450)

// The one struct the host and plugin share. Field order and sizes are the ABI.
struct AEffect
{
    VstInt32 magic;                                   // must be kEffectMagic
    AEffectDispatcherProc dispatcher;                 // host -> plugin messages
    AEffectProcessProc process;                       // deprecated accumulate-process; may be NULL
    AEffectSetParameterProc setParameter;
    AEffectGetParameterProc getParameter;
    VstInt32 numPrograms;
    VstInt32 numParams;
    VstInt32 numInputs;
    VstInt32 numOutputs;
    VstInt32 flags;                                   // effFlagsXxx
    VstIntPtr resvd1;                                 // reserved, must be 0
    VstIntPtr resvd2;                                 // reserved, must be 0
    VstInt32 initialDelay;                            // latency in samples
    VstInt32 realQualities;                           // deprecated, 0
    VstInt32 offQualities;                            // deprecated, 0
    float    ioRatio;                                 // deprecated, 1.0f
    void*    object;                                  // plugin-private pointer (your instance)
    void*    user;                                    // host-private pointer; never touch
    VstInt32 uniqueID;                                // four-char-code registered id
    VstInt32 version;                                 // plugin version, e.g. 1000 = 1.0.0.0
    AEffectProcessProc processReplacing;              // the real audio callback
    AEffectProcessDoubleProc processDoubleReplacing;  // optional 64-bit path
    char future[56];                                  // reserved, zeroed
};

// AEffect::flags
enum
{
    effFlagsHasEditor          = 1 << 0,
    effFlagsCanReplacing       = 1 << 4,   // processReplacing is implemented (always set this)
    effFlagsProgramChunks      = 1 << 5,   // state is saved via effGetChunk/effSetChunk
    effFlagsIsSynth            = 1 << 8,   // instrument (receives MIDI, usually no audio input)
    effFlagsNoSoundInStop      = 1 << 9,
    effFlagsCanDoubleReplacing = 1 << 12
};

// ---------------------------------------------------------------------------
// Host -> plugin opcodes (AEffect::dispatcher). Numbers are the ABI.
// ---------------------------------------------------------------------------
enum
{
    effOpen = 0,
    effClose = 1,
    effSetProgram = 2,            // value = program index
    effGetProgram = 3,
    effSetProgramName = 4,        // ptr = const char*
    effGetProgramName = 5,        // ptr = char[kVstMaxProgNameLen+1]
    effGetParamLabel = 6,         // index = param, ptr = char[] units, e.g. "dB"
    effGetParamDisplay = 7,       // index = param, ptr = char[] value as text
    effGetParamName = 8,          // index = param, ptr = char[] name
    effGetVu_Deprecated = 9,
    effSetSampleRate = 10,        // opt = sample rate
    effSetBlockSize = 11,         // value = max frames per process call
    effMainsChanged = 12,         // value = 1 resume / 0 suspend
    effEditGetRect = 13,          // ptr = ERect** (we have no editor; return 0)
    effEditOpen = 14,
    effEditClose = 15,
    effEditDraw_Deprecated = 16,
    effEditMouse_Deprecated = 17,
    effEditKey_Deprecated = 18,
    effEditIdle = 19,
    effEditTop_Deprecated = 20,
    effEditSleep_Deprecated = 21,
    effIdentify_Deprecated = 22,
    effGetChunk = 23,             // index = 0 bank / 1 program, ptr = void** out; return byte count
    effSetChunk = 24,             // index as above, value = byte count, ptr = data
    effProcessEvents = 25,        // ptr = VstEvents* (MIDI for the next process call)
    effCanBeAutomated = 26,       // index = param; return 1 if automatable
    effString2Parameter = 27,     // index = param, ptr = const char*; return 1 if handled
    effGetNumProgramCategories_Deprecated = 28,
    effGetProgramNameIndexed = 29,// index = program, ptr = char[]; return 1 if handled
    effCopyProgram_Deprecated = 30,
    effConnectInput_Deprecated = 31,
    effConnectOutput_Deprecated = 32,
    effGetInputProperties = 33,   // index = pin, ptr = VstPinProperties*
    effGetOutputProperties = 34,  // index = pin, ptr = VstPinProperties*
    effGetPlugCategory = 35,      // return VstPlugCategory
    effGetCurrentPosition_Deprecated = 36,
    effGetDestinationBuffer_Deprecated = 37,
    effOfflineNotify = 38,
    effOfflinePrepare = 39,
    effOfflineRun = 40,
    effProcessVarIo = 41,
    effSetSpeakerArrangement = 42,// value = VstSpeakerArrangement* in, ptr = out
    effSetBlockSizeAndSampleRate_Deprecated = 43,
    effSetBypass = 44,            // value = 1 bypass on
    effGetEffectName = 45,        // ptr = char[kVstMaxEffectNameLen+1]
    effGetErrorText_Deprecated = 46,
    effGetVendorString = 47,      // ptr = char[kVstMaxVendorStrLen+1]
    effGetProductString = 48,     // ptr = char[kVstMaxProductStrLen+1]
    effGetVendorVersion = 49,     // return version number
    effVendorSpecific = 50,
    effCanDo = 51,                // ptr = const char*; return 1 yes / -1 no / 0 don't know
    effGetTailSize = 52,          // return tail length in samples (0 = default, 1 = no tail)
    effIdle_Deprecated = 53,
    effGetIcon_Deprecated = 54,
    effSetViewPosition_Deprecated = 55,
    effGetParameterProperties = 56,// index = param, ptr = VstParameterProperties*; return 1 if filled
    effKeysRequired_Deprecated = 57,
    effGetVstVersion = 58,        // return 2400
    effEditKeyDown = 59,
    effEditKeyUp = 60,
    effSetEditKnobMode = 61,
    effGetMidiProgramName = 62,
    effGetCurrentMidiProgram = 63,
    effGetMidiProgramCategory = 64,
    effHasMidiProgramsChanged = 65,
    effGetMidiKeyName = 66,
    effBeginSetProgram = 67,
    effEndSetProgram = 68,
    effGetSpeakerArrangement = 69,
    effShellGetNextPlugin = 70,
    effStartProcess = 71,
    effStopProcess = 72,
    effSetTotalSampleToProcess = 73,
    effSetPanLaw = 74,
    effBeginLoadBank = 75,
    effBeginLoadProgram = 76,
    effSetProcessPrecision = 77,  // value = kVstProcessPrecision32 / 64
    effGetNumMidiInputChannels = 78,
    effGetNumMidiOutputChannels = 79
};

// ---------------------------------------------------------------------------
// Plugin -> host opcodes (audioMasterCallback)
// ---------------------------------------------------------------------------
enum
{
    audioMasterAutomate = 0,                // index = param, opt = new value: tell host a param moved
    audioMasterVersion = 1,                 // returns host VST version (2400)
    audioMasterCurrentId = 2,
    audioMasterIdle = 3,
    audioMasterPinConnected_Deprecated = 4,
    audioMasterWantMidi_Deprecated = 6,
    audioMasterGetTime = 7,                 // value = flags wanted; returns VstTimeInfo* or 0
    audioMasterProcessEvents = 8,           // ptr = VstEvents* to send MIDI out
    audioMasterSetTime_Deprecated = 9,
    audioMasterTempoAt_Deprecated = 10,
    audioMasterGetNumAutomatableParameters_Deprecated = 11,
    audioMasterGetParameterQuantization_Deprecated = 12,
    audioMasterIOChanged = 13,
    audioMasterNeedIdle_Deprecated = 14,
    audioMasterSizeWindow = 15,
    audioMasterGetSampleRate = 16,
    audioMasterGetBlockSize = 17,
    audioMasterGetInputLatency = 18,
    audioMasterGetOutputLatency = 19,
    audioMasterGetPreviousPlug_Deprecated = 20,
    audioMasterGetNextPlug_Deprecated = 21,
    audioMasterWillReplaceOrAccumulate_Deprecated = 22,
    audioMasterGetCurrentProcessLevel = 23,
    audioMasterGetAutomationState = 24,
    audioMasterOfflineStart = 25,
    audioMasterOfflineRead = 26,
    audioMasterOfflineWrite = 27,
    audioMasterOfflineGetCurrentPass = 28,
    audioMasterOfflineGetCurrentMetaPass = 29,
    audioMasterSetOutputSampleRate_Deprecated = 30,
    audioMasterGetOutputSpeakerArrangement_Deprecated = 31,
    audioMasterGetVendorString = 32,        // ptr = char[kVstMaxVendorStrLen+1]
    audioMasterGetProductString = 33,       // ptr = char[kVstMaxProductStrLen+1]
    audioMasterGetVendorVersion = 34,
    audioMasterVendorSpecific = 35,
    audioMasterSetIcon_Deprecated = 36,
    audioMasterCanDo = 37,                  // ptr = const char*
    audioMasterGetLanguage = 38,
    audioMasterOpenWindow_Deprecated = 39,
    audioMasterCloseWindow_Deprecated = 40,
    audioMasterGetDirectory = 41,
    audioMasterUpdateDisplay = 42,          // tell host that names/values changed
    audioMasterBeginEdit = 43,              // index = param: user grabbed a control
    audioMasterEndEdit = 44,                // index = param: user released a control
    audioMasterOpenFileSelector = 45,
    audioMasterCloseFileSelector = 46,
    audioMasterEditFile_Deprecated = 47,
    audioMasterGetChunkFile_Deprecated = 48,
    audioMasterGetInputSpeakerArrangement_Deprecated = 49
};

// ---------------------------------------------------------------------------
// String length conventions (buffer sizes the host guarantees, excluding NUL)
// ---------------------------------------------------------------------------
enum
{
    kVstMaxProgNameLen   = 24,
    kVstMaxParamStrLen   = 8,     // the conservative limit for param name/label/display
    kVstMaxVendorStrLen  = 64,
    kVstMaxProductStrLen = 64,
    kVstMaxEffectNameLen = 32
};

// effGetVstVersion answer
#define kVstVersion 2400

// effSetProcessPrecision values
enum { kVstProcessPrecision32 = 0, kVstProcessPrecision64 = 1 };

// effGetPlugCategory answers
typedef enum
{
    kPlugCategUnknown = 0,
    kPlugCategEffect = 1,
    kPlugCategSynth = 2,
    kPlugCategAnalysis = 3,
    kPlugCategMastering = 4,
    kPlugCategSpacializer = 5,
    kPlugCategRoomFx = 6,
    kPlugSurroundFx = 7,
    kPlugCategRestoration = 8,
    kPlugCategOfflineProcess = 9,
    kPlugCategShell = 10,
    kPlugCategGenerator = 11
} VstPlugCategory;

// ---------------------------------------------------------------------------
// Events (MIDI)
// ---------------------------------------------------------------------------
enum
{
    kVstMidiType  = 1,
    kVstSysExType = 6
};

struct VstEvent
{
    VstInt32 type;
    VstInt32 byteSize;
    VstInt32 deltaFrames;   // sample offset inside the next process call
    VstInt32 flags;
    char data[16];
};

enum { kVstMidiEventIsRealtime = 1 };

struct VstMidiEvent
{
    VstInt32 type;          // kVstMidiType
    VstInt32 byteSize;      // sizeof(VstMidiEvent)
    VstInt32 deltaFrames;
    VstInt32 flags;
    VstInt32 noteLength;    // usually 0
    VstInt32 noteOffset;    // usually 0
    char midiData[4];       // status, data1, data2, 0
    char detune;
    char noteOffVelocity;
    char reserved1;
    char reserved2;
};

struct VstMidiSysexEvent
{
    VstInt32 type;          // kVstSysExType
    VstInt32 byteSize;      // sizeof(VstMidiSysexEvent)
    VstInt32 deltaFrames;
    VstInt32 flags;
    VstInt32 dumpBytes;     // length of sysexDump
    VstIntPtr resvd1;
    char* sysexDump;
    VstIntPtr resvd2;
};

struct VstEvents
{
    VstInt32 numEvents;
    VstIntPtr reserved;
    struct VstEvent* events[2];   // variable length in practice: numEvents entries
};

// ---------------------------------------------------------------------------
// Time / transport info (audioMasterGetTime)
// ---------------------------------------------------------------------------
struct VstTimeInfo
{
    double samplePos;           // current position in samples
    double sampleRate;
    double nanoSeconds;         // system time
    double ppqPos;              // position in quarter notes
    double tempo;               // BPM
    double barStartPos;         // ppq of the last bar start
    double cycleStartPos;
    double cycleEndPos;
    VstInt32 timeSigNumerator;
    VstInt32 timeSigDenominator;
    VstInt32 smpteOffset;
    VstInt32 smpteFrameRate;
    VstInt32 samplesToNextClock;
    VstInt32 flags;             // kVstXxx below
};

enum
{
    kVstTransportChanged     = 1,
    kVstTransportPlaying     = 1 << 1,
    kVstTransportCycleActive = 1 << 2,
    kVstTransportRecording   = 1 << 3,
    kVstAutomationWriting    = 1 << 6,
    kVstAutomationReading    = 1 << 7,
    kVstNanosValid           = 1 << 8,
    kVstPpqPosValid          = 1 << 9,
    kVstTempoValid           = 1 << 10,
    kVstBarsValid            = 1 << 11,
    kVstCyclePosValid        = 1 << 12,
    kVstTimeSigValid         = 1 << 13,
    kVstSmpteValid           = 1 << 14,
    kVstClockValid           = 1 << 15
};

// ---------------------------------------------------------------------------
// Parameter and pin properties
// ---------------------------------------------------------------------------
struct VstParameterProperties
{
    float stepFloat;
    float smallStepFloat;
    float largeStepFloat;
    char label[64];
    VstInt32 flags;             // kVstParameterXxx
    VstInt32 minInteger;
    VstInt32 maxInteger;
    VstInt32 stepInteger;
    VstInt32 largeStepInteger;
    char shortLabel[8];
    VstInt16 displayIndex;
    VstInt16 category;
    VstInt16 numParametersInCategory;
    VstInt16 reserved;
    char categoryLabel[24];
    char future[16];
};

enum
{
    kVstParameterIsSwitch                = 1 << 0,
    kVstParameterUsesIntegerMinMax       = 1 << 1,
    kVstParameterUsesFloatStep           = 1 << 2,
    kVstParameterUsesIntStep             = 1 << 3,
    kVstParameterSupportsDisplayIndex    = 1 << 4,
    kVstParameterSupportsDisplayCategory = 1 << 5,
    kVstParameterCanRamp                 = 1 << 6
};

struct VstPinProperties
{
    char label[64];
    VstInt32 flags;             // kVstPinXxx
    VstInt32 arrangementType;
    char shortLabel[8];
    char future[48];
};

enum
{
    kVstPinIsActive   = 1 << 0,
    kVstPinIsStereo   = 1 << 1,
    kVstPinUseSpeaker = 1 << 2
};

struct VstSpeakerProperties
{
    float azimuth;
    float elevation;
    float radius;
    float reserved;
    char name[64];
    VstInt32 type;
    char future[28];
};

struct VstSpeakerArrangement
{
    VstInt32 type;              // kSpeakerArrXxx (0 = mono, 1 = stereo, -1 = user defined)
    VstInt32 numChannels;
    struct VstSpeakerProperties speakers[8];
};

enum
{
    kSpeakerArrUserDefined = -1,
    kSpeakerArrEmpty = -2,
    kSpeakerArrMono = 0,
    kSpeakerArrStereo = 1
};

struct ERect
{
    VstInt16 top;
    VstInt16 left;
    VstInt16 bottom;
    VstInt16 right;
};

// Helper: build a four-character-code unique id the way hosts expect, e.g. MPC_FOURCC('M','y','S','y').
#define MPC_FOURCC(a, b, c, d) \
    ((VstInt32)(((uint32_t)(uint8_t)(a) << 24) | ((uint32_t)(uint8_t)(b) << 16) | \
                ((uint32_t)(uint8_t)(c) << 8)  |  (uint32_t)(uint8_t)(d)))

#ifdef __cplusplus
} // extern "C"
#endif

#endif // MPC_VST2_ABI_H
