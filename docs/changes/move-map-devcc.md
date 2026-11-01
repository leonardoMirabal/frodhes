## Changes

- On the Move: a pressure mapping (MAPPINGS, AT CC / PA CC) set to one of the FM-1's developer or test CCs (2-4,
  8-11, 41, 42, 114, 116, 119) now sends nothing. Before, pressure mapped to CC 119 switched the limiter off, so a
  loud note could clip, and CC 10 reset every effect's state.

## Tested

- tests/py/test_move_maps.py test_map_never_sends_a_developer_cc: channel pressure with the mapping on CC 119, 10,
  41 and 116 sends nothing, and on CC 18 it sends. `make test-move` and the mapping and monkey tests pass.
