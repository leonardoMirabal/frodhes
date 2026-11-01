## Changes

- Docs: the manual (docs/MANUAL.md) is the one sound manual, sections 2-14 written once for every device. The
  device manuals, docs/MOVE.md and docs/SMK37.md, say only how to reach the pages on that device and what differs,
  and link each page to the manual's section for it instead of describing its controls again.
- Manual: the appendices 15 (SMK-37) and 16 (Move) are one paragraph, one picture and the link to the device's
  manual each; the SMK-37's list of differences moved to docs/SMK37.md (What differs from the FM-1).
- docs/SMK37.md: a picture of the FRODHES page as the SMK-37 draws it (its cards on K5..K8); the voice count no longer
  names the FM-1's (it links the manual's polyphony section).

## Tools

- `tools/manual_shots.py`: also draws the SMK-37's pictures, from the SMK host build, into `docs/manual/img/smk/`
  (one picture folder per device: `img/` the FM-1's, `img/move/` the Move's, `img/smk/` the SMK-37's).
- `tools/doc_links.py`: also checks docs/MOVE.md, docs/MOVE-DEV.md and docs/SMK37.md (each picture's `?v=` its
  file's hash, no version named), and every link to a heading (`MANUAL.md#4-the-pages`, `#install`) in all five
  docs.
- `tests/py/test_manual_shots.py`: the SMK-37's pictures are what the code draws, and every device picture is linked
  from its manual, the MANUAL's appendices showing one each. `tests/py/test_doc_links.py`: the device manuals and
  the heading links.

## Tested

- Host: `make test`, `make pytest` (`PYTEST_N=2`), `tools/ci_move.sh`, `tools/doc_links.py` 0 problems,
  `tools/changes.py`, `tools/manual_shots.py` (the FM-1's pictures unchanged pixel for pixel, `img/smk/` new) and
  `node tools/move_ui_shots.mjs --check`. Docs and tools only, no firmware change; the FM-1 and the Move not used.
