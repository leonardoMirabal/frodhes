## Fixed

- **Newer FM-1s work with Frodhes** ([#31](https://github.com/leonardoMirabal/frodhes/issues/31)). Newer units carry
  another flash chip (UC25HQ80), which Frodhes did not accept: installing from the stock firmware was refused, an
  update from Frodhes and the USB rescue refused too, and settings, presets and the pattern were not kept over a power
  cycle. It is accepted everywhere now (as in upstream Dinghy). A save on it pauses the audio for a moment, only when
  the FM-1 is silent.
