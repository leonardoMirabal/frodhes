# SPDX-License-Identifier: GPL-3.0-only
# Frodhes: common tasks. Everything installs and builds inside the project (.local/, build/).
PY      ?= $(if $(wildcard .local/venv/bin/python),.local/venv/bin/python,python3)
CC      ?= cc
HOSTBIN ?= build/host/frodhes
HOST_DEFS ?=                # experiments only, e.g. make test HOST_DEFS=-DFR_VOICES=10u HOSTBIN=build/host10/frodhes
CFLAGS_HOST := -O2 -Wall -Wno-unused-function -DDINGHY_HOST -DAPP_VER='"dev"' -Ifirmware/src -Ifirmware/gen $(HOST_DEFS)

.PHONY: all setup host test test10 pytest firmware clean

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

test10:                    ## experiment: every script on a 10-voice host build (-DFR_VOICES=10u), the 8-voice steal
	@$(MAKE) -s build/host10/frodhes HOSTBIN=build/host10/frodhes HOST_DEFS=-DFR_VOICES=10u   # scripts replaced by scripts10/
	@fail=0; for s in tests/scripts/*.txt tests/scripts10/*.txt; do n=$$(basename $$s .txt); \
	  case $$s in tests/scripts/*_steal_release.txt) continue;; esac; \
	  if build/host10/frodhes build/host10/$$n $$s > build/host10/$$n.log 2>&1; then echo "ok    $$n"; \
	  else echo "FAIL  $$n (build/host10/$$n.log)"; fail=1; fi; done; exit $$fail

pytest:                    ## Python tools' unit tests (analysis, generators)
	$(PY) -m pytest -q tests/py

firmware:                  ## build/frodhes.fwsc (Docker on macOS)
	PYTHON=$(PY) ./build.sh

clean:
	rm -rf build
