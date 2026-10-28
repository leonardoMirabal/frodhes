# SPDX-License-Identifier: GPL-3.0-only
# The probe dsp.so. Run inside the Move image (move/docker): native gcc on arm64, CC=aarch64-linux-gnu-gcc on x86_64.
#   make -C move/probe            -> build/move/probe/{frodhes-probe,frodhes-probe-raw}/{dsp.so,module.json}
#   make -C move/probe test       -> the dlopen host test (arm64 only: it runs the .so)
CC     ?= gcc
OUT    ?= ../../build/move/probe
CFLAGS := -O2 -g0 -fPIC -std=gnu11 -Wall -Wextra -Wno-unused-parameter -fvisibility=hidden \
          -ffile-prefix-map=$(CURDIR)=move/probe
LDLIBS := -lm -lpthread
IDS    := frodhes-probe frodhes-probe-raw

all: $(foreach i,$(IDS),$(OUT)/$(i)/dsp.so $(OUT)/$(i)/module.json)

$(OUT)/frodhes-probe/dsp.so: probe.c ../vendor/plugin_api_v1.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -shared -Wl,-z,defs -DPROBE_ID='"frodhes-probe"' -o $@ probe.c $(LDLIBS)
$(OUT)/frodhes-probe-raw/dsp.so: probe.c ../vendor/plugin_api_v1.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -shared -Wl,-z,defs -DPROBE_ID='"frodhes-probe-raw"' -o $@ probe.c $(LDLIBS)
$(OUT)/frodhes-probe/module.json: module.json.in
	@mkdir -p $(dir $@)
	sed -e 's/@ID@/frodhes-probe/' -e 's/@NAME@/Frodhes Probe/' -e 's/@RAW@/false/' $< > $@
$(OUT)/frodhes-probe-raw/module.json: module.json.in
	@mkdir -p $(dir $@)
	sed -e 's/@ID@/frodhes-probe-raw/' -e 's/@NAME@/Frodhes Probe Raw/' -e 's/@RAW@/true/' $< > $@

$(OUT)/test_probe: test_probe.c ../vendor/plugin_api_v1.h
	@mkdir -p $(dir $@)
	$(CC) -O1 -g -std=gnu11 -Wall -Wextra -Wno-unused-parameter -o $@ test_probe.c -ldl -lm

test: all $(OUT)/test_probe
	$(OUT)/test_probe $(OUT)/frodhes-probe/dsp.so $(OUT)/frodhes-probe-raw/dsp.so

clean:
	rm -rf $(OUT)
.PHONY: all test clean
