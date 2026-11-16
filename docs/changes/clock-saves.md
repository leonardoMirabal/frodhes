## Fixed

- With a MIDI clock running (CLOCK on EXT, a computer or a drum machine sending it), setup changes such as the MIDI
  channel, the display or a tapped tempo were not saved until the clock stopped, so switching off first lost them.
  Now they are saved as usual, about 2 s after the change once the sound has died away, and the sequencer's pattern
  too while the sequencer is stopped. The clock keeps its tempo and its beat through the save.
