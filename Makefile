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
