<!-- Public text: player terms only; never name or compare with a reference instrument or a brand.
VERIFY after sonic-0910 lands (the numbers are the host prototypes' of 2026-11-08: S1b velocity map 65876d86, S2 tine
1f596312, S3 decay/damper 624ed178 option D, S4 dynamics): every number below against the merged build (host) and the
FM-1 jack; add the FM-1 Tested lines after the release gates; drop an item that did not ship. -->
## Changes

- **The electric piano's touch** (every electric piano type: STAGE 77, SUITCASE, EARLY FELT, BELL DYNO). Soft notes
  stay mellow and round up to a medium touch. Above that the bark comes in clearly, and the point where it starts
  rises with the register: from about velocity 58 in the bass to about 108 from E5 up, so a medium touch no longer
  growls in the upper keys while a firm one still barks in the bass. Soft and medium notes keep their level, so
  presets keep their balance.
- **A sweeter upper register**: the tine's high, inharmonic ring at the start of a hard note is turned down key by
  key from A3 up. At velocity 127, in the first 20 ms, it sits about 40 dB under the note at C5, 55 dB at C6 and
  65 dB at C7 (it was 12, 33 and 32 dB). The bell stays; the hard, brittle edge goes. Keys below A3 and the bark are
  unchanged.
- **Longer sustain in the middle register**: a held note around C4 now rings about twice as long before it fades
  away (about 20 s to silence, was 10 s); the bass and the top keep their own decay. DECAY scales it as before.
- **A quicker, more natural damper**: letting go stops a note about twice as fast (10 dB down in 25-45 ms), as the
  felt lands and settles, with no click. RELEASE keeps its range (127 still lets a note ring on for about 2 s),
  release velocity and NOISE shape it as before, and letting go never makes the sound louder.
- The Move's higher-quality engine and the desktop plugin get the same changes.

## Tested

- Host: keys 28-100 at velocities 20, 40, 64, 90, 110 and 127, before and after: the velocity knee per register, the
  tine band in the first 20 ms, H2 against H1, the attack above 1 kHz, the level, the decay per register and the
  damper time; the never-louder-after-key-up check (10 ms windows sliding by 1 ms against the onset, with three
  synthetic regression cases) on every sound at the softest and the hardest velocity; VIBES and MARIMBA
  byte-identical; Gate R, Gate H and Gate M references renewed for the intended change; `make test`, `make pytest`.
