# SPDX-License-Identifier: GPL-3.0-only
# Frodhes: common tasks. Everything installs and builds inside the project (.local/, build/).
PY      ?= $(if $(wildcard .local/venv/bin/python),.local/venv/bin/python,python3)
CC      ?= cc
HOSTBIN ?= build/host/frodhes
HOST_DEFS ?=                # other builds, e.g. make test HOST_DEFS=-DFR_VOICES=10u HOSTBIN=build/host10/frodhes
CFLAGS_HOST := -O2 -Wall -Wno-unused-function -DDINGHY_HOST -DAPP_VER='"dev"' -Ifirmware/src -Ifirmware/gen $(HOST_DEFS)

.PHONY: all setup host test testv test10 pytest firmware clean

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

testv:                     ## another voice count: make testv V=8 (or 10) runs every script on build/host<V>
                           ## (-DFR_VOICES=<V>u), tests/scripts<V>/ in place of the 9-voice steal scripts
	@test -n "$(V)" -a -d tests/scripts$(V) || { echo "make testv V=N needs tests/scripts<N>/ (its steal scripts)"; exit 2; }
	@$(MAKE) -s build/host$(V)/frodhes HOSTBIN=build/host$(V)/frodhes HOST_DEFS=-DFR_VOICES=$(V)u
	@fail=0; for s in tests/scripts/*.txt tests/scripts$(V)/*.txt; do n=$$(basename $$s .txt); \
	  case $$s in tests/scripts/*_steal_release.txt) continue;; esac; \
	  if build/host$(V)/frodhes build/host$(V)/$$n $$s > build/host$(V)/$$n.log 2>&1; then echo "ok    $$n"; \
	  else echo "FAIL  $$n (build/host$(V)/$$n.log)"; fail=1; fi; done; exit $$fail

test10:                    ## make testv V=10
	@$(MAKE) -s testv V=10

PYTEST_N ?= auto           # make pytest PYTEST_N=4 (or 0: serial); serial anyway without pytest-xdist
pytest:                    ## Python tools' unit tests (analysis, generators), in parallel with pytest-xdist
	@if [ "$(strip $(PYTEST_N))" != 0 ] && $(PY) -c "import xdist" 2>/dev/null; then \
	  echo "$(PY) -m pytest -q -n $(strip $(PYTEST_N)) --dist loadgroup tests/py"; \
	  $(PY) -m pytest -q -n $(strip $(PYTEST_N)) --dist loadgroup tests/py; \
	else echo "$(PY) -m pytest -q tests/py  (serial: no pytest-xdist)"; $(PY) -m pytest -q tests/py; fi

firmware:                  ## build/frodhes.fwsc (Docker on macOS)
	PYTHON=$(PY) ./build.sh

clean:
	rm -rf build
