// SPDX-License-Identifier: GPL-3.0-only
// The Frodhes Audio Unit in Apple's own AU host (AVAudioEngine, offline manual rendering), for tools/plugin_hosts.py:
//   plugin_au_render RATE FRAMES EVENTS.txt OUT.f32
// EVENTS.txt: one MIDI event a line, "FRAME STATUS DATA1 DATA2" (decimal, frames from the start, sorted). The render
// is cut at every event's frame, so each event reaches the unit at exactly its frame (an AU instrument's MIDI applies
// from the next render call). OUT.f32: the left and right channels, interleaved float32, FRAMES frames.
// The unit is found by its component description (aumu Frd1 Frdh): it must be installed and registered.
import AVFoundation

let a = CommandLine.arguments
guard a.count == 5, let rate = Double(a[1]), let total = Int(a[2]) else {
    FileHandle.standardError.write("usage: plugin_au_render RATE FRAMES EVENTS.txt OUT.f32\n".data(using: .utf8)!)
    exit(2)
}
var events: [(Int, UInt8, UInt8, UInt8)] = []
for line in try! String(contentsOfFile: a[3], encoding: .utf8).split(separator: "\n") {
    let f = line.split(separator: " ").compactMap { Int($0) }
    if f.count == 4 { events.append((f[0], UInt8(f[1]), UInt8(f[2]), UInt8(f[3]))) }
}
let desc = AudioComponentDescription(componentType: kAudioUnitType_MusicDevice, componentSubType: 0x46726431,
                                     componentManufacturer: 0x46726468, componentFlags: 0, componentFlagsMask: 0)
let engine = AVAudioEngine()
let inst = AVAudioUnitMIDIInstrument(audioComponentDescription: desc)
engine.attach(inst)
let fmt = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 2)!
engine.connect(inst, to: engine.mainMixerNode, format: fmt)
try! engine.enableManualRenderingMode(.offline, format: fmt, maximumFrameCount: 512)
try! engine.start()
let buf = AVAudioPCMBuffer(pcmFormat: engine.manualRenderingFormat, frameCapacity: 512)!
var out = [Float](repeating: 0, count: total * 2)
var pos = 0, ei = 0
while pos < total {
    while ei < events.count && events[ei].0 <= pos {
        let e = events[ei]
        inst.sendMIDIEvent(e.1, data1: e.2, data2: e.3)
        ei += 1
    }
    var n = min(512, total - pos)
    if ei < events.count && events[ei].0 - pos < n { n = events[ei].0 - pos }
    let st = try! engine.renderOffline(AVAudioFrameCount(n), to: buf)
    if st != .success { FileHandle.standardError.write("render status \(st.rawValue)\n".data(using: .utf8)!); exit(1) }
    let l = buf.floatChannelData![0], r = buf.floatChannelData![1]
    for i in 0..<Int(buf.frameLength) { out[2 * (pos + i)] = l[i]; out[2 * (pos + i) + 1] = r[i] }
    pos += Int(buf.frameLength)
}
try! Data(bytes: out, count: out.count * 4).write(to: URL(fileURLWithPath: a[4]))
