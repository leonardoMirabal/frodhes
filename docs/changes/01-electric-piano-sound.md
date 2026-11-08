<!-- DRAFT: finalise every [TODO ...] from the sound agents' reports (host numbers + the FM-1 on the jack) and
the user's listening round; drop any item that did not ship. Public text: player terms only, no reference
instrument or brand named or compared. -->
## Changes

- **The electric piano's touch** (every electric piano type: STAGE 77, SUITCASE, EARLY FELT, BELL DYNO). Soft notes
  stay mellow and round all the way up to a medium touch. Above that the bark comes in clearly, and the point where
  it starts rises with the register: about velocity [TODO] in the bass (C2), [TODO] in the middle (C4) and [TODO] in
  the treble (C6), so a medium touch no longer growls in the upper keys while a firm one still barks in the bass.
- **A wider dynamic range**: from the softest note to the hardest, [TODO] dB on the headphone jack (was [TODO] dB),
  at the same loudness for a medium note, so presets keep their level.
- **A sweeter upper register**: hard notes in the top two octaves keep their bell but lose the hard, brittle edge
  ([TODO: the measured change, e.g. "about [TODO] dB less above 5 kHz at C6, velocity 127"]).
- **Longer sustain in the middle register**: a held note around C4 rings for about [TODO] s before it fades away
  (was [TODO] s); the bass and the top keep their own decay. DECAY scales it as before.
- **A faster, more natural damper**: letting go stops a note in about [TODO] ms (was [TODO] ms), as the felt lands
  and settles, with no click; RELEASE, release velocity and NOISE shape it as before, and letting go never makes
  the sound louder.
- **KEY VEL**: with the new touch the bark comes in above about [TODO] (was about 100); 96 is still the default
  medium press. [TODO: say so only if the threshold moved.]
- [TODO: only if they changed: "The factory presets are rebalanced for the new touch; your user presets keep their
  settings."]

## Tested

- Host: [TODO: the new pytest names] (the velocity map per register, the dynamic range, the top octave's
  spectrum, the middle register's decay, the damper time); the dynamic-range retest of every sound at velocity 1
  and 127 (none louder after the key-up); Gate R renders updated [TODO]; `make test`, `make pytest`.
- FM-1 (release build): the CPU worst case with 10 voices and every effect on [TODO] us (limit 4500, nothing shed);
  the crackle test at 44.1 and 48 kHz; T1 and T2 scans; the jack recordings of every type at velocities 1-127
  [TODO: numbers].
- Listening: [TODO: the user's round on the jack].
