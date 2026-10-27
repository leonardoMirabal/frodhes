# SPDX-License-Identifier: GPL-3.0-only
# Frodhes: common tasks. Everything installs and builds inside the project (.local/, build/).
PY      ?= $(if $(wildcard .local/venv/bin/python),.local/venv/bin/python,python3)
CC      ?= cc
HOSTBIN ?= build/host/frodhes
HOST_DEFS ?=                # other builds, e.g. make test HOST_DEFS=-DRH_VOICES=9u HOSTBIN=build/host9/frodhes (default: 10)
CFLAGS_HOST := -O2 -Wall -Wno-unused-function -DDINGHY_HOST -DAPP_VER='"dev"' -Ifirmware/src -Ifirmware/gen $(HOST_DEFS)

.PHONY: all setup host test testv test10 test-smk pytest firmware clean

all: test pytest

setup:                     ## project-local venv, toolchain and the 3 SDK files
	python3 -m venv .local/venv
	.local/venv/bin/pip install -q -r requirements.txt
	[ -x .local/jieli/toolchain/pi32v2/bin/clang ] || sh tools/get_toolchain.sh .local/jieli
	sh tools/get_sdk.sh .local/fw-AC79_AIoT_SDK

$(HOSTBIN): firmware/src/*.c firmware/src/*.h firmware/gen/*.h tests/host.c
	@mkdir -p build/host $(dir $@)
	$(CC) $(CFLAGS_HOST) -o $@ firmware/src/frodhes.c -lm

host: $(HOSTBIN)           ## the firmware on this computer (fake FM-1)
	PYTHON=$(PY) tests/run_host.sh

test: $(HOSTBIN)           ## every script in tests/scripts must pass its expects
	@fail=0; for s in tests/scripts/*.txt; do n=$$(basename $$s .txt); \
	  if $(HOSTBIN) build/host/$$n $$s > build/host/$$n.log 2>&1; then echo "ok    $$n"; \
	  else echo "FAIL  $$n (build/host/$$n.log)"; fail=1; fi; done; exit $$fail

testv:                     ## another voice count: make testv V=8 (or 9, 10) runs every script on build/host<V>
                           ## (-DFR_VOICES=<V>u: both engines at V), tests/scripts<V>/ in place of the default steal
                           ## scripts (every engine 10 voices)
	@test -n "$(V)" -a -d tests/scripts$(V) || { echo "make testv V=N needs tests/scripts<N>/ (its steal scripts)"; exit 2; }
	@$(MAKE) -s build/host$(V)/frodhes HOSTBIN=build/host$(V)/frodhes HOST_DEFS=-DFR_VOICES=$(V)u
	@fail=0; for s in tests/scripts/*.txt tests/scripts$(V)/*.txt; do n=$$(basename $$s .txt); \
	  case $$s in tests/scripts/*_steal_release.txt) continue;; esac; \
	  if build/host$(V)/frodhes build/host$(V)/$$n $$s > build/host$(V)/$$n.log 2>&1; then echo "ok    $$n"; \
	  else echo "FAIL  $$n (build/host$(V)/$$n.log)"; fail=1; fi; done; exit $$fail

test10:                    ## make testv V=10
	@$(MAKE) -s testv V=10

SMKBIN  := build/host-smk/frodhes
SMK_DEFS := -DBOARD_SMK37=1 -DFR_VOICES=7u
$(SMKBIN): firmware/src/*.c firmware/src/*.h firmware/hal/*.h firmware/gen/*.h tests/host.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS_HOST) $(SMK_DEFS) -o $@ firmware/src/frodhes.c -lm

test-smk: $(SMKBIN)        ## the SMK-37 build on the host (7 voices): every script (the FM-1's button / knob / LED
                           ## ids on the SMK buttons of the same name, tests/host.c), tests/scripts7/ for the steal
                           ## scripts, tests/smk/ (its own controls); each flash call checked against the SMK allow-list.
                           ## The FM-1-only scripts (TRS IN, the key LEDs) are listed in tests/smk/known.txt: reported, not
                           ## failed
	@fail=0; for s in tests/scripts/*.txt tests/scripts7/*.txt tests/smk/*.txt; do n=$$(basename $$s .txt); \
	  case $$s in tests/scripts/*_steal_release.txt|tests/smk/known.txt) continue;; esac; \
	  if $(SMKBIN) build/host-smk/$$n $$s > build/host-smk/$$n.log 2>&1; then echo "ok    $$n"; \
	  elif grep -q "FAIL flash" build/host-smk/$$n.log; then echo "FAIL  $$n: a flash call refused (build/host-smk/$$n.log)"; fail=1; \
	  elif grep -qx "$$n" tests/smk/known.txt; then echo "known $$n (FM-1 only, tests/smk/known.txt; build/host-smk/$$n.log)"; \
	  else echo "FAIL  $$n (build/host-smk/$$n.log)"; fail=1; fi; done; exit $$fail

PYTEST_N ?= auto           # make pytest PYTEST_N=4 (or 0: serial); serial anyway without pytest-xdist
SHARD ?=                   # make pytest SHARD=2/4: only shard 2 of 4 (tools/pytest_shard.py; ci.yml's host jobs)
PYTEST_FILES = $(if $(strip $(SHARD)),$$($(PY) tools/pytest_shard.py $(subst /, ,$(strip $(SHARD)))),tests/py)
pytest:                    ## Python tools' unit tests (analysis, generators), in parallel with pytest-xdist
	@if [ "$(strip $(PYTEST_N))" != 0 ] && $(PY) -c "import xdist" 2>/dev/null; then \
	  echo "$(PY) -m pytest -q -n $(strip $(PYTEST_N)) --dist loadgroup $(PYTEST_FILES)"; \
	  $(PY) -m pytest -q -n $(strip $(PYTEST_N)) --dist loadgroup $(PYTEST_FILES); \
	else echo "$(PY) -m pytest -q $(PYTEST_FILES)  (serial: no pytest-xdist)"; $(PY) -m pytest -q $(PYTEST_FILES); fi

firmware:                  ## build/frodhes.fwsc (Docker on macOS)
	PYTHON=$(PY) ./build.sh

clean:
	rm -rf build

# ---- the Move (a Schwung sound generator, aarch64 Linux): move/, private plan 05 ----
# On macOS everything runs in the arm64 bookworm image (move/docker; light: 2 CPUs); on Linux arm64 (CI) natively:
# make move MOVE_RUN=
MOVE_IMG  ?= frodhes-move:bookworm
MOVE_RUN  ?= $(if $(filter Darwin,$(shell uname -s)),docker run --rm --cpus=2 --platform linux/arm64 -v "$(CURDIR)":/src -w /src $(MOVE_IMG),)
MOVE_CC   ?= gcc
MOVE_VER  ?= 0.0.0-dev
MOVE_DEFS ?= -DFR_VOICES=16u
MOVE_CFLAGS := -O2 -fPIC -ffp-contract=off -std=gnu11 -Wall -Wno-unused-function -Wno-unused-variable \
  -fvisibility=hidden -ffile-prefix-map=$(CURDIR)=. -DAPP_VER='"$(MOVE_VER)"' $(MOVE_DEFS) \
  -Imove/compat -Ifirmware/src -Ifirmware/gen
MOVE_OUT  := build/move/frodhes

.PHONY: move-image move move-native move-tools-native test-move test-move-native gate-p gate-p-native gate-m move-probe test-move-probe
move-image:                ## the Move build image (Debian bookworm arm64, about 250 MB)
	docker build --platform linux/arm64 -t $(MOVE_IMG) move/docker

move:                      ## build/move/frodhes/{dsp.so,module.json} + build/move/frodhes-module.tar.gz
	$(MOVE_RUN) make -s move-native

move-native:
	@mkdir -p $(MOVE_OUT)
	$(MOVE_CC) $(MOVE_CFLAGS) -shared -Wl,-z,defs -o $(MOVE_OUT)/dsp.so move/move_plugin.c move/coro/coro_aarch64.S -lm
	@if [ -f move/module/module.json ]; then cp move/module/module.json $(MOVE_OUT)/; else \
	  printf '{"id":"frodhes","name":"Frodhes","version":"%s","dsp":"dsp.so","api_version":2,"capabilities":{"audio_out":true,"midi_in":true,"component_type":"sound_generator"}}\n' "$(MOVE_VER)" > $(MOVE_OUT)/module.json; fi
	@if [ -f move/module/help.json ]; then cp move/module/help.json $(MOVE_OUT)/; fi
	@cp LICENSE $(MOVE_OUT)/LICENSE
	tar -C build/move -czf build/move/frodhes-module.tar.gz $$(cd build/move && ls frodhes/dsp.so frodhes/module.json frodhes/help.json frodhes/LICENSE 2>/dev/null)
	@ls -l $(MOVE_OUT)/dsp.so build/move/frodhes-module.tar.gz

test-move:                 ## the Move checks: exports/imports, the coroutine, the bridge (api, Gate P, eq), persistence (M1-08)
	$(MOVE_RUN) make -s test-move-native

test-move-native: move-native
	@syms=$$(nm -D --defined-only $(MOVE_OUT)/dsp.so | awk '{print $$3}'); \
	  [ "$$syms" = move_plugin_init_v2 ] || { echo "FAIL exports: $$syms"; exit 1; }; echo "ok    exports: $$syms"
	@bad=$$(nm -D --undefined-only $(MOVE_OUT)/dsp.so | awk '{print $$2}' | grep -vE '^(_ITM_|__cxa_finalize|__gmon_start__)' | \
	  grep -vE '^(calloc|free|malloc|aligned_alloc|memcmp|memcpy|memmove|memset|strlen|strcmp|strncmp|strchr|strstr|snprintf|sprintf|__isoc99_sscanf|atoi|strtol|clock_gettime|abort|__getauxval|pthread_create|pthread_join|pthread_self|pthread_setschedparam|sched_setaffinity|nanosleep|open|open64|close|read|write|fsync|rename|unlink|mkdir|stat|stat64|__errno_location|fopen|fopen64|fclose|fwrite)@' || true); \
	  [ -z "$$bad" ] || { echo "FAIL imports beyond the allow-list: $$bad"; exit 1; }; echo "ok    imports"
	@mkdir -p build/move/t
	@$(MOVE_CC) -O2 -Wall -Wextra -o build/move/t/coro move/tests/test_coro.c move/coro/coro_aarch64.S
	build/move/t/coro
	@build/move/t/coro selftest >/dev/null 2>&1; [ $$? -ne 0 ] && echo "ok    seccomp guard kills a syscall" || { echo "FAIL seccomp guard"; exit 1; }
	@$(MOVE_CC) -O1 -std=gnu11 -Wall -o build/move/smoke move/tests/smoke.c -ldl
	build/move/smoke $(MOVE_OUT)/dsp.so
	@$(MOVE_CC) -O1 -g -std=gnu11 -Wall -Wextra -o build/move/t/test_bridge move/tests/test_bridge.c -ldl
	build/move/t/test_bridge api $(MOVE_OUT)/dsp.so
	build/move/t/test_bridge gatep $(MOVE_OUT)/dsp.so 10000
	@$(MOVE_CC) -O1 -std=gnu11 -Wall -Wextra -o build/move/t/test_store move/tests/test_store.c
	@rm -rf build/move/t/store build/move/t/persist build/move/t/run.*
	build/move/t/test_store build/move/t/store 40
	@mkdir -p build/move/t/persist
	build/move/t/test_bridge persist $(MOVE_OUT)/dsp.so build/move/t/persist save
	build/move/t/test_bridge persist $(MOVE_OUT)/dsp.so build/move/t/persist check
	@rm -rf build/move/t/store build/move/t/persist build/move/t/run.*
	MOVE_DEFS='$(MOVE_DEFS)' move/tests/run_eq.sh

move-tools-native: move-native   ## (the plugin and build/move/t/test_bridge only: tests/py/test_move_params.py)
	@mkdir -p build/move/t
	@$(MOVE_CC) -O1 -g -std=gnu11 -Wall -Wextra -o build/move/t/test_bridge move/tests/test_bridge.c -ldl

gate-p:                    ## Gate P (M1-11): test-move's guards, then ASan + UBSan builds through every bridge test
	$(MOVE_RUN) make -s gate-p-native

# (-fno-sanitize=shift-base: shared DSP shifts negative values left at 16 places, gcc defines it; fixed after 0.9.7)
MOVE_SAN := -O1 -g -fsanitize=address,undefined -fno-sanitize=shift-base -fno-sanitize-recover=undefined -fno-omit-frame-pointer
gate-p-native: test-move-native
	@mkdir -p build/move/san
	$(MOVE_CC) $(filter-out -O2,$(MOVE_CFLAGS)) $(MOVE_SAN) -shared -o build/move/san/dsp.so move/move_plugin.c move/coro/coro_aarch64.S -lm 2>/dev/null
	$(MOVE_CC) $(MOVE_SAN) -DNO_GUARDS -std=gnu11 -o build/move/san/test_bridge move/tests/test_bridge.c -ldl
	ASAN_OPTIONS=detect_leaks=0 build/move/san/test_bridge api build/move/san/dsp.so
	ASAN_OPTIONS=detect_leaks=0 build/move/san/test_bridge gatep build/move/san/dsp.so 10000
	@rm -rf build/move/san/persist; mkdir -p build/move/san/persist
	ASAN_OPTIONS=detect_leaks=0 build/move/san/test_bridge persist build/move/san/dsp.so build/move/san/persist save
	ASAN_OPTIONS=detect_leaks=0 build/move/san/test_bridge persist build/move/san/dsp.so build/move/san/persist check
	@rm -rf build/move/san/persist build/move/t/run.*
	@for s in move/tests/scripts/*.txt; do n=$$(basename $$s .txt); \
	  build/move/t/host build/move/san/$$n $$s > /dev/null 2>&1 && \
	  ASAN_OPTIONS=detect_leaks=0 build/move/san/test_bridge eq build/move/san/dsp.so $$s build/move/san/$$n.wav || exit 1; \
	  rm -f build/move/san/$$n.wav build/move/san/$$n-*.ppm; done
	@echo "ok    gate P"

gate-m: move               ## Gate M (M1-10): the shipping dsp.so replays every MIDI-driven Gate R render byte-identical
	python3 tools/gate_m.py

move-probe:                ## the device-session probe (move/probe/README.md)
	$(MOVE_RUN) make -s -C move/probe -j2

test-move-probe:           ## the probe's dlopen host test
	$(MOVE_RUN) make -s -C move/probe -j2 test
