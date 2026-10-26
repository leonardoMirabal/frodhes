# Platform API

Everything [platform.c](../firmware/src/platform.c) gives your app. One compilation unit: `frodhes.c` includes
`platform.c` first, so all of this is just there. Nothing here is a library you link against; read the source
when in doubt, it is short.

- [The contract](#the-contract)
- [Time](#time)
- [Keys, buttons, encoders](#keys-buttons-encoders)
- [LEDs](#leds)
- [Screen](#screen)
- [Sound](#sound)
- [MIDI](#midi)
- [Storage: settings, blobs, slots](#storage-settings-blobs-slots)
- [Memory and flash](#memory-and-flash)
- [Build options](#build-options)
- [The SMK-37](#the-smk-37)

## The contract

Your app supplies two functions:

```c
static void app_audio(int32_t *out, uint32_t n);   /* the audio interrupt: fill n stereo frames */
static void app_main(void);                        /* your main loop; never returns */
```

`app_main` must call **`platform_poll()`** often: every few milliseconds, and never less than once every
couple of seconds. It feeds the watchdog (8 s, then the FM-1 resets), runs USB (MIDI, the updater, a computer
asking for the update mode) and reads the MASTER knob. A long job (drawing a lot, saving) is fine; a loop that
waits for something must poll.

```c
static void app_main(void)
{
    /* set up */
    for (;;) {
        platform_poll();
        /* read the panel, MIDI; draw what changed */
    }
}
```

Names the platform defines that you can change from the build: `APP_NAME` (the rescue and crash screens,
default `"DINGHY"`; Frodhes sets `"FRODHES"`), `APP_VER` (set by `./build.sh --release`), `FELUCCA_ID` (the package identity; see
`tools/build.py`).

## Time

| | |
| --- | --- |
| `fm1_ms` | milliseconds since power-on (`volatile uint32_t`, counted by the 10 kHz timer) |
| `fm1_ticks()` | a free-running 24 MHz counter: `FM1_TICKS_PER_US` (24) per microsecond; wraps in ~179 s, so subtract: `fm1_ticks() - t0` |
| `fm1_micros()` | microseconds, from the same counter |
| `fm1_delay_ms(ms)`, `fm1_delay_us(us)` | busy waits; keep them short (they do not poll) |

To time your audio code: `t0 = fm1_ticks()` before, `(fm1_ticks() - t0) / FM1_TICKS_PER_US` after.

## Keys, buttons, encoders

The panel is scanned at 10 kHz in the background and debounced.

| | |
| --- | --- |
| `fm1_in.notes` | the 27 keys held now: bit 0 = F3 ... bit 26 = G5 |
| `fm1_in.buttons` | the 14 buttons held now: bit = `BTN_...` |
| `fm1_input_edges(&released)` | buttons **pressed** since the last call (bits), and those released into `released` |
| `fm1_input_note_edges()` | keys pressed since the last call (bits): a press shorter than a loop pass still counts |
| `fm1_enc_take(ENC_...)` | detents turned since the last call, + = clockwise |
| `fm1_adc_read(FM1_ADC_MASTER)` | the MASTER knob, 0..1023 (-1 = not ready). platform.c already makes it the volume |
| `fm1_adc_read(FM1_ADC_BATT)` | the battery voltage, raw |

Buttons (ids 0..13):

| id | | id | | id | |
| --- | --- | --- | --- | --- | --- |
| 0 | `BTN_OCT_DOWN` | 5 | `BTN_LFO` | 10 | `BTN_ARP` |
| 1 | `BTN_OCT_UP` | 6 | `BTN_EDIT` | 11 | `BTN_SEQ` |
| 2 | `BTN_FX` | 7 | `BTN_GLO` | 12 | `BTN_PLAY` |
| 3 | `BTN_SEL` | 8 | `BTN_HOME` | 13 | `BTN_REC` |
| 4 | `BTN_ENV` | 9 | `BTN_SAVE` | | |

`BTN_OCT_DOWN` held at power-on is the USB rescue; your app sees it like any button otherwise.

Encoders: `ENC_SELECT` (0), `ENC_ALGORITHM` (1), `ENC_KNOB1` .. `ENC_KNOB4` (2..5), `ENC_PRESETS` (6).

A key down and up, the usual way:

```c
uint32_t keys = fm1_in.notes, down = keys & ~prev, up = prev & ~keys;
prev = keys;
```

## LEDs

| | |
| --- | --- |
| `fm1_led_key(id, on)` | a button (`BTN_...`) or a key (`KEY_LED(k)`, k = 0..26) lit or not |
| `fm1_led_key_glow(id, on)` | the same LED dimly (~1/24): "there is something here" (landmarks, a step that is set) |
| `fm1_led[col]`, `fm1_led_dim[col]` | the raw layers, for many LEDs at once (see `hal/fm1_input.h`) |
| `fm1_led_duty[2]`, `fm1_led_group_ids(id0, id1)`, `fm1_led_build()` | brightness: the lit layer's duty per group (lit on that many of 16 scan frames, 0 dark; group 0 the ids id0..id1, 1 the rest), and the scan ISR's tables, rebuilt by `fm1_led_build()` after the LEDs or a duty changed (main loop) |

## Screen

240 x 240 pixels, 16-bit colour (`RGB(r, g, b)` with 0..255 each; `C_BLACK`, `C_WHITE`). The screen keeps
what you draw: draw only what changed.

Straight to the screen:

| | |
| --- | --- |
| `lcd_fill(x, y, w, h, colour)` | a rectangle |
| `draw_text_box(x, y, w, font, "text", colour, align)` | text in a box of width `w`, cleared to black first; align 0 left, 1 centre, 2 right |

Fonts: `FONT_S` (16 px, ASCII 32..126) and `FONT_L` (32 px, ASCII 32..95: capitals, digits, signs; small
letters show as `?`). Frodhes stores in `FONT_S` only the characters its own texts use (`tools/font_chars.py`,
`tools/gen_font.py`): a new text with another character needs `tools/gen_font.py` run again
(`tests/py/test_font_glyphs.py` says so).

For anything more, a canvas: draw into a buffer, then send it in one go (no flicker):

```c
cv_begin(240, 56, C_BLACK);                 /* w x h, cleared; w * h <= CV_MAX (240 x 64) */
cv_rect(x, y, w, h, colour);
cv_line(x0, y0, x1, y1, colour);
cv_pset(x, y, colour);
cv_text(x, y, &FONT_S, "text", colour);     /* returns x after the text; text_w(&FONT_S, "text") measures */
cv_blit(0, 140);                            /* to the screen at (0, 140) */
```

A bigger canvas: `#define CV_MAX (240u * 120u)` before `#include "platform.c"` (2 bytes a pixel, in the pool:
240 x 240 is 113 KB of 336).

**Nothing waits for a redraw.** A canvas goes out by DMA, a strip of 240 x 64 in some 3 ms. Define `LCD_IDLE` before
`#include "platform.c"` and lcd.c calls it again and again while it waits for the DMA, so your app reads MIDI (or the
keys) meanwhile; without it, a chord or a fast CC stream lands only after the drawing. Dinghy's app does it (Frodhes
does not: it draws one small element a main-loop pass and reads MIDI in between):

```c
static void app_idle(void);
#define LCD_IDLE app_idle
#include "platform.c"
...
static void app_idle(void) { if (idle_ok) midi_in(); }   /* idle_ok: set only around your own drawing */
```

Guard it with a flag of your own: the platform draws too (the update and crash screens), and the function must not
run inside itself.

## Sound

```c
static void app_audio(int32_t *out, uint32_t n)
```

- Runs in the audio interrupt, every 5.8 ms, with `n` = 256 frames at **44.1 kHz** (`FS`).
- `out` is `L R L R ...`, **already zero**: add your sound to it. Full scale is **±32767** (Q15); more is clipped.
  (An app that writes every sample itself may `#define APP_OUT_WRITTEN 1` before the include: the clear is skipped.
  Frodhes does.)
- After you: the MASTER knob, the clip, the USB audio stream (the computer records exactly this), the DAC.
  The DAC gets it 6 dB under its full scale, so the FM-1's output stage does not distort.
- Keep it fast and simple: no flash, no screen, no waiting. Talk to the main loop through `volatile` variables
  (one writer each). If a render runs late the sound breaks up; see [RULES.md](RULES.md).

Pitch: a 32-bit phase that wraps is an oscillator with no division at run time: `inc = f * 2^32 / 44100`,
`ph += inc` each frame. `rhodes.c` keeps a table for one octave and shifts it for the others.

## MIDI

USB-MIDI (class compliant) and, on the FM-1, the **TRS MIDI IN** jack (31250 baud, polled at 2 kHz: sloopDX's parser,
with Felucca Salt's fix for the ring). Both land in one queue. Frodhes adds `uart_midi_on` (midi_uart.c; the MIDI page's
TRS IN, `set.trs_off`): 0 = the jack is still parsed but nothing of it reaches the queue. The platform filters no
channel and counts no clock: the app does both, once (frodhes.c, tempo.c).

| | |
| --- | --- |
| `midi_get(&status, &d1, &d2)` | the next message: **1** from USB, **2** from the TRS jack, 0 = none. Call it from the main loop until 0 |
| `midi_send(status, d1, d2)` | send one message over USB (from the main loop only) |

`status` includes the channel: `0x90` note on channel 1, `0x93` channel 4; a note on with velocity 0 is a note off.
Channel messages come through, and of the system messages the clock and transport ones: `0xF8` clock (24 a quarter
note), `0xFA` start, `0xFB` continue, `0xFC` stop (`d1`, `d2` are 0). SysEx is the platform's (the updater) and does
not reach the app; other system messages are dropped.

A channel of your own, for both ways (Frodhes: the MIDI page's CHANNEL, ALL or 1..16, kept in the settings):

```c
if (ch && (st & 15u) != ch - 1u) continue;          /* in: not ours */
midi_send(0x90u | (ch ? ch - 1u : 0u), note, vel);  /* out */
```

The tempo of an incoming clock: the time between every 24th `0xF8`, `60000 / ms` BPM (Frodhes: tempo.c, CLOCK EXT; it
blinks PLAY on the beat). `midi_send(0xF8, 0, 0)` sends a clock, if your firmware is the master. The SMK-37 has no TRS MIDI
IN (its pin is a key-matrix row there): `midi_get` returns only 1 on it.

## Storage: settings, blobs, slots

```c
static int settings_load(void *p, uint32_t n);         /* 0 = loaded; else *p is left alone */
static int settings_save(const void *p, uint32_t n);   /* 0 = saved */
static int settings_save_start(const void *p, uint32_t n);   /* the same, beside the main loop: 1 = started */
static int settings_save_poll(void);                    /* once a pass: 1 = still running, 0 = done, < 0 failed */
static int blob_load(uint32_t i, void *p, uint32_t n);         /* blob i of NBLOB (8): one sector each */
static int blob_save(uint32_t i, const void *p, uint32_t n);
static int slot_load(uint32_t i, void *p, uint32_t n);         /* slot i of NSLOT (8): SLOT_SECT (4) sectors each */
static int slot_save(uint32_t i, const void *p, uint32_t n);
```

| | how much | where (FM-1) | where (SMK-37) |
| --- | --- | --- | --- |
| settings | up to `SECTOR_MAX` (4084 B) | 0xFC000 | 0x9D000 |
| blobs 0..7 (presets, patterns) | up to 4084 B each | 0xF4000.. | 0x9E000.. |
| slots 0..7 (songs, samples) | up to `SLOT_MAX` (16372 B) each | 0x97000.. | 0xC2000.. |

Each is kept with a magic, its length and a CRC32 ([store.h](../firmware/src/store.h)). A change of size, another
firmware's data or a broken save reads as "nothing saved": start from your defaults then. `settings_load` and
`blob_load` leave `p` alone when they fail; `slot_load` reads straight into `p` (no buffer that big), so on -1 it holds
rubbish. A fork gives itself its own `STORE_MAGIC` (store.h); Frodhes keeps Dinghy's "DNGY", the magic its settings
have carried since 0.1, so a settings record saved by an older Frodhes still loads.

**Save when nothing sounds.** A sector's erase keeps the flash part busy for tens of milliseconds (a slot: four
sectors). On the FM-1 the app's erases and page programs run in slices of ~0.25 ms with the interrupts off, the part
suspended while the interrupts run between them (`fl_split_op` in `hal/fm1_flash.h`; a part that cannot suspend gets
the old blocking erase, the sound dropping out), so the audio and the USB stream go on. `frodhes.c` saves the settings
2 s after the last change (the pattern 1.5 s), once all voices are silent, whether or not a computer records the USB
audio. `settings_save` still holds the main loop for the whole save (~100 ms on the FM-1: a MIDI note in the meantime
waits); `settings_save_start` starts the erase and returns (the 256-byte record must stay as it is until the save
ends), and `settings_save_poll`, called once a main-loop pass, runs at most one or two ~0.25 ms slices (`fl_op_start` / `fl_op_poll` in `hal/fm1_flash.h`), so MIDI and
the keys are taken between the slices. Frodhes' auto save uses the pair; any other save finishes one in progress first. Flash wears out after about 100,000 erases of a sector: never save in a loop.

## Memory and flash

| | | |
| --- | --- | --- |
| Code + constants | 568 KB | runs straight from flash (`const` tables cost no RAM) |
| RAM `.data` + `.bss` | 96 KB | your globals and statics |
| Pool | 336 KB | big buffers, zeroed at boot: `static int16_t delay[65536] __attribute__((section(".pool")));` |
| Stack | 24 KB | keep big arrays off it: `static` |

The build prints what you use and fails above the limits.

Flash (1 MiB) as Dinghy uses it: the app, the update staging area (FM-1 0xE0000, SMK-37 0xBB000, the platform's) and
the store above. On the FM-1 the other firmwares of the family keep their data in 0x97000..0xFBFFF too; a new firmware
simply finds its own magic missing and starts fresh. To store elsewhere, change the places in store.h and its
`FL_RANGE_OK`: the guard that keeps a bug from erasing anything else. On the SMK-37 the RAM flash driver refuses the
calibration, the pairing data, the stock resources and key_mac on its own as well, whatever `FL_RANGE_OK` says.

**Frodhes** keeps a tighter allow-list than Dinghy's (a delta in store.h): `APP_FL_RANGE` in frodhes_store.h replaces
the blobs and slots, and keeps everything in flash that no region of the stock firmware's table covers (0.9.6): on
the FM-1 only the update staging, the record A / B (0xFC000, 0xFD000: the settings and the user presets, set_store.c)
and the pattern (0xFE000) can be erased or written; on the SMK-37 only 0xEB000..0xF3FFF (the record, the pattern,
the staging). The places 0.9.5 used (FM-1 0x97000..0x9AFFF, SMK-37 0x9D000..0xA1FFF) are read once, by the migration
at the first start, and refused for writing.

## Build options

`./build.sh` takes `--release X.Y`, `--board fm1|smk37` and, with smk37, `--model pro|elite|mke` (or `BOARD=`,
`MODEL=`); see [The SMK-37](#the-smk-37). `DINGHY_DEFS="-DFELUCCA_UAC=0" ./build.sh` adds defines for a test build.
In platform.c (or before `#include "platform.c"` where it says `#ifndef`):

| | | |
| --- | --- | --- |
| `FELUCCA_UAC` | 1 | USB audio input (the output to the computer); 0 = MIDI only |
| `FELUCCA_UART` | FM-1: 1 | the TRS MIDI IN (always 0 on the SMK-37) |
| `FELUCCA_CDC` | 0 | a USB serial console for debugging (some hosts then hide the audio; see usb.c) |
| `FELUCCA_USB_PID` | 0x0005 | the app's USB product id (VID 0x1209; the update loader keeps 0x0002). A computer keeps a MIDI device's name per vendor / product id, so a fork that renames its device (`STR2` in usb.c) needs an id of its own to show the new name |
| `FELUCCA_OTA` | 1 | the updater. **Leave it on**: without it nothing can install over your firmware but the rescue |
| `CV_MAX` | 240 x 64 | the biggest canvas, in pixels |

The app is built with `-Os`. Leave it so: with `-O2` (the unity build puts usb.c in it) the FM-1's USB-MIDI IN babbled
on the device and the hub dropped the port (OberVave, 2026-10-13; which line -O2 breaks is not known).

In tools/build.py, for your fork: `PRODUCT` (the identity), `DATA_FAMILY` (the SMK-37 store's owner tag),
`SCREEN_NAME` and `SCREEN_COLOUR` (the update loader's screen on the FM-1: your name in your colour, while an
install runs or after one was cut off).

## The SMK-37

The M-Vave SMK-37 (Pro, and the same keyboard as SMK-37 Elite and MKE-P37) has the FM-1's chip, a 37-key keybed with
velocity, 16 pads, 4 faders, 2 wheels and 8 knobs. `./build.sh --board smk37` builds your app for it; nothing in your
code needs to change, and `#if BOARD_SMK37` reaches the extras. Frodhes' own SMK-37 builds are experimental: not tested on hardware by us
(back up the flash first; [SMK37.md](SMK37.md)). The board layer is genkigenki's
([fm1-to-smk37](https://github.com/genkigenki/fm1-to-smk37), from his sloop-fm1 port); more in [SMK37.md](SMK37.md).

The FM-1's calls keep working: `fm1_in.notes` are the middle 27 keys (F3..G5), the buttons have the FM-1's names on the
SMK buttons that mean the same, the knobs too:

| FM-1 | SMK-37 | | FM-1 | SMK-37 |
| --- | --- | --- | --- | --- |
| HOME | PATCH | | MASTER (pot) | K1 (turned: the volume) |
| EDIT | PARA | | `ENC_SELECT` | K2 |
| ENV | CHORD | | `ENC_ALGORITHM` | K3 |
| LFO | NOTE REPEAT | | `ENC_PRESETS` | K4 |
| SEL | SCALE | | `ENC_KNOB1..4` | K5..K8 |
| GLO | GLOBE | | OCT- OCT+, ARP, FX, SEQ, PLAY, REC | the same |
| SAVE | BT | | | |

and four more buttons: `BTN_KNOB_BANK`, `BTN_FADER_BANK`, `BTN_LEFT`, `BTN_RIGHT`. The keys have no LEDs (`KEY_LED`
does nothing); the buttons have.

What the SMK-37 has more:

| | |
| --- | --- |
| `fm1_in.key_vel[SMK_KEY_F3 + k]` | the velocity (1..127) of note key k's last press, from the unit's factory curves |
| `smk_extkey_take()` | the outer keys (C3..E3, G#5..C6): `key << 8 \| velocity` (0 = let go, key 0..36), -1 = none |
| `fm1_in.keys` | all 37 keys held now (64 bits, bit 0 = the lowest C) |
| pads | by default the 16 white keys F3..G5 (they set `fm1_in.notes`); `smk_pad_events = 1` makes them events instead: `smk_pad_take()` = `pad << 8 \| velocity` (0 = let go), -1 = none. Pads 0..7 are the top row, 8..15 the bottom row |
| `padled_set(pad, r, g, b)`, `padled_flush()` | the pads' colours (0..255; ~1 ms to send, only when one changed) |
| `fm1_in.fader[0..3]` | the faders, 0..127 |
| `fm1_in.wheel[0]`, `[1]` | the pitch and the mod wheel, raw 0..1023; `smk_pw_bend()` (smk_wheel.h) makes the pitch wheel a self-calibrating bend |
| `fm1_in.pad_press[pad]` | a held pad's pressure, 0..127 |

The SMK-37's store is shared by the ports of the kit: at boot an owner tag ("SMK37DATA" + `DATA_FAMILY`) says whose
data it is; anything else is cleared once, never read. Its update loader stays dark (no screen).
