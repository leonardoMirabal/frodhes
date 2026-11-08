# SPDX-License-Identifier: GPL-3.0-only
# Frodhes plugin UI (B-M2a): the renderer and the headless PNG tool.
#   make -C plugin/ui renders     -> build/plugin-ui/*.png (the design, drawn by the plugin's own UI code)
ROOT := ../..
OUT := $(ROOT)/build/plugin-ui
CC ?= cc
CFLAGS ?= -O2 -std=c99 -D_DEFAULT_SOURCE -D_DARWIN_C_SOURCE -Wall -Wextra -Wshadow -Wno-unused-const-variable
SRC := frui.c frui_font.c frui_gfx.c frui_text.c
FONT := $(ROOT)/assets/fonts/InterTight[wght].ttf
DFONT := $(ROOT)/assets/fonts/Michroma-Regular.ttf

.PHONY: renders clean
renders: $(OUT)/frui_render
	rm -f $(OUT)/*.png $(OUT)/directions-a3/*.png
	$(OUT)/frui_render "$(FONT)" $(OUT) $(DFONT)
	mkdir -p $(OUT)/directions-a3 && FRUI_DIRECTIONS=1 $(OUT)/frui_render "$(FONT)" $(OUT) $(DFONT)

$(OUT)/frui_render: $(SRC) tools/frui_render.c frui.h frui_font.h frui_gfx.h frui_params.h frui_text.h
	mkdir -p $(OUT)
	nice -n 10 $(CC) $(CFLAGS) -o $@.tmp $(SRC) tools/frui_render.c -lz -lm && mv $@.tmp $@

clean:
	rm -rf $(OUT)
