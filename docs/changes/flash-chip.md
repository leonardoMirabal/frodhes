## Fixed

- **Newer units work with Frodhes** ([#31](https://github.com/leonardoMirabal/frodhes/issues/31)). Newer FM-1s and
  SMK-37s carry another flash chip (UC25HQ80), which Frodhes did not accept: the install from the stock firmware, an
  update and the USB rescue were refused, and settings, presets and the pattern were lost at power off. It is
  accepted everywhere now, as in upstream Dinghy. A save on it pauses the audio for a moment, only when the unit is
  silent.
