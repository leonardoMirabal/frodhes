## Changes
- The browser emulator is published with every release, at the same moment, and shows the version it runs (in the
  title, the heading, the footer and on the emulated screen).
- The emulator's knobs turn more evenly: a trackpad swipe or a mouse wheel notch moves them a step or two (a gentle
  swipe used to jump about 30 steps), and a drag moves one step every 10 pixels.

## Tools
- CI builds the emulator on every pull request and runs its selftest in Chromium and WebKit; the release workflow
  builds it from the tag, tests it, and publishes the release only once GitHub Pages serves it.
