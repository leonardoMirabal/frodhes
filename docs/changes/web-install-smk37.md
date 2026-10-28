## New

- The emulator page installs Frodhes on the SMK-37 Pro, SMK-37 Elite and MKE-P37 (experimental, as the files), over the stock firmware or over Frodhes; an install that was cut off resumes. The release publishes the three SMK-37 files next to the page, with their sha256 in `firmware.json`.

## Fixed

- The emulator page's installer on an SMK-37 (GitHub #23): since the SMK-37's USB name carries "Frodhes" (0.9.7), the page found the unit and offered the FM-1 package. It refused that package before sending anything, but it offered nothing that would install. Now the page reads the unit's identity and picks the package for its model.

## Tested

- `tests/web/install_test.js` (node, fake devices): an install on each SMK-37 model over Frodhes and over the stock firmware, the resume from each model's update loader, the FM-1 package refused on each SMK-37 and an SMK-37 package on the FM-1, nothing sent.
- The page's selftest in Chromium: a fake FM-1 with a chosen file, then a fake SMK-37 Pro with its package picked from `firmware.json`.
- `tools/web_firmware.sh`: the three SMK-37 files under their release names, a wrong or repeated identity refused with nothing copied.
