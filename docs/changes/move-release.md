## New

- The Move installs Frodhes through Schwung Manager (`http://<move-ip>:7700`; the Move shows its IP under Global
  Settings > System > Web Manager): Install Custom Module > From GitHub URL with
  `https://github.com/leonardoMirabal/frodhes`, or the downloaded `frodhes-module.tar.gz` under From Tarball, and
  from Schwung's module catalog once Frodhes is listed there. Updating and removing are in the same place.
  `docs/MOVE.md` has the steps; the ssh route and `tools/move_install.py` moved to `docs/MOVE-DEV.md`.

## Tools

- `tools/move_catalog.py`: `release-json X.Y.Z` writes `release.json` (the version and the tag's
  `frodhes-module.tar.gz`, the file Schwung Manager reads from main), `check TARBALL X.Y.Z` checks that the package
  unpacks to `frodhes/` alone and that its module.json carries the version. `release.yml` checks the tag's tarball
  with it, and its new job `release-json` writes and pushes `release.json` to main after a release that carries the
  Move package.

## Tested

- Host: `tests/py/test_move_catalog.py` (release.json's JSON and URL for a version and an asset, the repository
  from the origin remote, no step back to an older version, the package checks on good and broken tarballs, the
  built package, release.yml's steps), `tests/py/test_move_doc.py` (the install through Schwung Manager, the ssh
  route in MOVE-DEV.md, no "in development" left).
