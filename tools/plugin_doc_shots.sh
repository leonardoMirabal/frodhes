#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The desktop plugin's pictures in docs/manual/img/plugin/ (README, MANUAL appendix 17, docs/PLUGIN.md), in the
# window's own colours at 2x (1920 x 1200):
#   plugin-main.png, plugin-presets.png, plugin-save.png   the built plugin's window (build/plugin/frp_gui_shot,
#                                                          headless: make plugin first)
#   plugin-effects.png                                     the plugin's own UI code playing a sound with the
#                                                          compressor and the tape delay on (plugin/ui frui_render)
# Then tools/doc_links.py --fix sets the ?v= of every link to the new files.
#   tools/plugin_doc_shots.sh [PLUGIN_BUILD]               (default build/plugin)
set -eu
cd "$(dirname "$0")/.."
B=${1:-build/plugin}
T=build/plugin-doc-shots
OUT=docs/manual/img/plugin
[ -x "$B/frp_gui_shot" ] && [ -d "$B/bundles/Frodhes.clap" ] || { echo "no $B/frp_gui_shot or Frodhes.clap: make plugin first" >&2; exit 1; }
rm -rf "$T" && mkdir -p "$T/presets" "$OUT"
FRODHES_PRESET_DIR="$T/presets" FRP_TEST_EXT=1 "$B/frp_gui_shot" "$B/bundles/Frodhes.clap" "$T" --headless >"$T/gui_shot.log"
make -s -C plugin/ui renders OUT="$PWD/$T/ui" >"$T/ui.log"
cp "$T/open.png" "$OUT/plugin-main.png"
cp "$T/presets.png" "$OUT/plugin-presets.png"
cp "$T/save-typed.png" "$OUT/plugin-save.png"
cp "$T/ui/directions-a3/A3-framed-tab-comp-delay@2x.png" "$OUT/plugin-effects.png"
python3 tools/doc_links.py --fix
