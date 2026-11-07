## Fixed

- **Newer FM-1s keep their settings, presets and pattern** ([#31](https://github.com/leonardoMirabal/frodhes/issues/31)).
  Newer units carry another flash chip (UC25HQ80), which Frodhes did not accept, so on them nothing was saved over a
  power cycle. It is accepted now (as in upstream Dinghy). A save on it pauses the audio for a moment, only when the
  FM-1 is silent.
