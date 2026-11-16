## Fixed

- TEMPO page: after a tapped tempo or a MIDI clock left the tempo between whole numbers (such as 120.1 BPM), turning
  **BPM** kept the tenth (119.1, 121.1), so 120 could not be set again by hand. Now the first step of the knob
  lands on the whole BPM in the direction you turn (120.1 turns down to 120.0, up to 121.0), then it steps whole BPM.
  The plugin's BPM - / + keys and arrow keys on the settings sheet do the same.
