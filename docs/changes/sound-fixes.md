## Fixed

- DELAY: after one note through ANA, every later turn of TIME glided for many seconds (39 ms to 1.17 s took about
  18 s) and the next echoes came bunched and bent. The delay now comes fully to rest once its last echo has died away,
  and TIME then jumps straight to the new value, as it always did before any note.
- TUNE turned while notes were held: the held notes jumped to the new tuning at the next pitch-bend message. A note now
  keeps the TUNE it was struck with; the bend moves it by the bend alone.
- Pitch bend on a big chord: with more than four notes sounding, the notes followed the wheel in steps and up to 20
  cents apart from each other. Every sounding note now follows the wheel together.
- Half pedal (CC 64 between 42 and 63): the key-up knock and the felt's sound came at full strength, as with the pedal
  up. They now follow how lightly the dampers touch.
- Playing more notes than there are voices: the cut of the oldest note, when a new one takes its voice, is smoother
  (a loud low note no longer ticks under a soft new one); the new note starts as early as before.
