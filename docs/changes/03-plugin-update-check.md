## New

- Desktop plugin: it tells you when a newer Frodhes is out. A lit lamp on the window's gear, and in SETTINGS an
  UPDATE X.Y.Z key beside VERSION that opens the release page in your browser. It asks GitHub for the latest
  release at most once a day, only while the plugin's window is open, in the background (never the audio or the
  host); nothing is sent but that request, nothing is downloaded or installed. SETTINGS > UPDATES OFF turns it off
  (kept for every project). On Linux it uses the system's libcurl when there is one, else it does not check.
  What it contacts and how in [docs/PLUGIN.md](https://github.com/leonardoMirabal/frodhes/blob/main/docs/PLUGIN.md).
