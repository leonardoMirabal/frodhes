## Changes
- The USB name carries the model (GitHub #21): the FM-1 is **Frodhes FM-1** on the computer (it was "Frodhes"), an
  SMK-37 **Frodhes SMK-37 Pro** (Elite, MKE-P37 likewise). It is the name of the MIDI port, the audio input and the
  USB device. The community firmware flasher now finds the unit for its DOWNLOAD MODE. Each model has a new USB
  product id, so the computer sets the unit up as a new device by itself: in a DAW, switch the new port on once where
  the old one was. The update mode keeps its name, "Felucca Update".

## Tested
- Host: tests/usb_desc_test.c and tests/py/test_usb_desc.py (the FM-1, the FM-1 without USB audio, the three SMK-37
  models and the update loader: product id and name); the installers' tests (terminal and web) find and update a
  unit on either name. The sound is unchanged: every render byte for byte the same (tools/gate_r.py).
