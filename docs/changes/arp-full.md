## Fixed
- With the arpeggiator holding 16 notes (for example the sequencer's pattern feeding it), a new note replaces the
  oldest one instead of being lost (0.9.5 did the same).

## Tested
- Host: tests/scripts/arp_full.txt (the arpeggiator fed past 16 notes: the newest note plays, the oldest leaves),
  Gate R (the new script's render added; every other render unchanged).
