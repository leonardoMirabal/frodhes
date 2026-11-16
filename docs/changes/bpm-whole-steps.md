## Fixed

- TEMPO page: after a tapped tempo or a MIDI clock left the tempo between whole numbers (such as 120.1 BPM), turning
  **BPM** kept the tenth (119.1, 121.1), so 120 could not be set again by hand. Now a slow turn steps 0.1 BPM, so
  120.1 turns down to 120.0 and every tenth can be set by hand; a fast turn moves 1 to 4 BPM a click. The plugin's
  BPM - / + keys step 0.1 BPM too (Shift with the arrow keys: 1 BPM).
