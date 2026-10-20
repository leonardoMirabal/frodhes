# Contributing to Frodhes

Thank you for helping. A pull request is easiest to merge when it touches only its own files, so two pull requests
never fight over the same lines. The version lives in one place, the GitHub release (its tag, page, notes and
download), and the release tool fills in everything that depends on it.

## What a pull request touches

- **The code** (`firmware/src/`, `tools/`, `web/` ...). Integers only on the chip, no samples; the rules are in
  [docs/RULES.md](docs/RULES.md).
- **Its tests**: a host script in `tests/scripts/` or a pytest in `tests/py/` for the new behaviour.
- **The manual and README** where the change shows (a page, a control, a CC), without any version number.
- **Its own release-note fragment**: a new file `docs/changes/<topic>.md` (lowercase, digits and `-`), for example
  `docs/changes/display-page.md`. Only these sections, in this order, each optional, each a Markdown list:

  ```markdown
  ## New
  - **The DISPLAY page.** What it does, in plain words.
  ## Changes
  - What behaves differently now.
  ## Fixed
  - What was wrong, and since when (history like "0.9.3 had this too" is fine).
  ## Tools
  - New or changed tools and tests.
  ## Tested
  - What was tested, on the host and on the FM-1.
  ```

  `tools/changes.py` checks the fragments and `tools/changes.py --preview` shows the notes they make. At release time
  `tools/release.py` assembles every fragment (in file-name order) into `docs/releases/vX.Y.Z.md` and removes them.
- **The screenshots**, if the screen changed: run `tools/manual_shots.py` and commit `docs/manual/img/` and the
  README / MANUAL changes it makes (it sets every picture link's `?v=` to the file's hash).

## What a pull request leaves alone

- **No version numbers**: not in README / MANUAL, not in a fragment (the release names itself), not in
  `./build.sh --release`.
- **No `docs/releases/vX.Y.Z.md`**: the release tool writes it from the fragments.
- **No `?v=` edited by hand**: `tools/manual_shots.py` (or `tools/doc_links.py --fix`, after editing a picture by
  hand, such as `overview.png`) sets them. A link changes only when its picture does.

## Conflicts on screenshots

Never resolve a conflict on a `.png` by hand, and never pick one side. Resolve the code first, then re-run
`tools/manual_shots.py`, which draws every screenshot from the merged code and sets the links; commit the result.
A conflict on a `?v=` line in README / MANUAL is resolved the same way (or with `tools/doc_links.py --fix`).

## Tests

```sh
make setup      # once: .local/venv, the toolchain, the SDK files
make test       # the host scripts
make pytest     # the Python tests, in parallel
```

Both must pass. Among them: `test_manual_shots.py` (the committed screenshots are pixel for pixel what the code
draws), `test_doc_links.py` (every picture link's `?v=` matches its file, no version in the docs) and
`test_changes.py` (the fragments are well formed). If you have an FM-1, say in the pull request what you tested on
it; if not, say so, and it will be tested before the release.
