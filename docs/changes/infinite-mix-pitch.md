## Fixed
- REVERB INFINITE: turning MIX no longer bends the pitch of the tail. With SIZE on AUTO, MIX also set the size of
  the space, and a size change stretches a ringing tail (a held chord went 50 to 250 cents flat while MIX moved).
  On INFINITE, AUTO is now a fixed 5.0 and MIX sets only the level. HALL and PLATE keep AUTO from MIX.

## Tested
- Host: a held 440 Hz tone in INFINITE FREEZE with MIX swept from 64 to 120: the tail stays within 0.2 cents of the
  same take with MIX held (it was up to 180 cents flat). INFINITE on AUTO renders the same as SIZE 5.0, sample for
  sample. HALL renders are unchanged (Gate R).
