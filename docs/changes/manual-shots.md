## Changes

- Manual: the SEQUENCER screenshot shows the loop playing while it records (BAR 1/2 . 1, F MIN7 now, NEXT A MIN7,
  the bar's cells and the playhead), as its text describes; before, it showed the stopped step-entry view.
- Manual: a new screenshot on REVERB, TYPE INFINITE with DECAY at the top: the card reads FREEZE (120-126 read
  SUSTAIN). The intro picture's caption names the type it shows (BELL DYNO).

## Tools

- `tools/manual_shots.py`: the SEQUENCER shot starts the loop (CC 60) before it records, with the chord strum back
  at 0 after the CHORD shot (a strummed chord was named by its last note); the new shot `reverb-inf.png`.
- `tests/py/test_manual_shots.py`: a new test fails when a screenshot in `docs/manual/img/` is linked from no doc, or
  when the MANUAL's link to it sits under another page's heading than the one it shows.

## Tested

- Host: `tools/manual_shots.py` into a scratch folder against the committed pictures, pixel by pixel: before this
  change all 36 matched; after it only `seq.png` differs and `reverb-inf.png` is new. `make test`, `make pytest`
  (`PYTEST_N=2`), `tools/doc_links.py` 0 problems, `tools/changes.py`. No firmware change; the FM-1 not used.

<!--
## Proposal (not done): how the FM-1 and Move manuals sit together
1. MANUAL.md is the sound manual: sections 2-14 (every page and card) written once for every device.
2. Each device's manual says only how to reach those pages and what differs: docs/MOVE.md, docs/SMK37.md, linking back.
3. Appendices 15 and 16 shrink to a paragraph, one picture and the link; their difference lists move to the device files.
4. Pictures: one folder, one tool, one test per device (img/*.png manual_shots.py; img/move/ move_ui_shots.mjs). No renames.
5. tools/doc_links.py also checks docs/MOVE.md and docs/SMK37.md (MOVE.md has ?v= hashes nothing enforces yet).
6. README: one row and one picture pair per device, each linking its manual.
7. Owners: FM-1 session MANUAL 1-15, README, img/*.png; Move session MANUAL 16, MOVE.md, img/move/.
8. A shared behaviour change goes into MANUAL section 4; MOVE.md notes only a difference.
-->
