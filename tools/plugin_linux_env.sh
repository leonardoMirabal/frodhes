#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The Linux plugin's build image: Ubuntu 20.04 (glibc 2.31, the oldest glibc the bundles need: tools/plugin_depcheck.py
# --glibc 2.31) with clang 12 + lld + the LLVM binutils, run as root inside a fresh ubuntu:20.04 container (CI's
# plugin jobs and tools/plugin_linux_docker.sh). CMake comes from PyPI (20.04's is older than the 3.21 plugin/ needs).
# The X11 headers are for the plugin's own window (libX11 and libxcb are the only libraries it may link).
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends clang-12 lld-12 llvm-12 make ninja-build git curl ca-certificates \
    unzip xz-utils python3 python3-pip libx11-dev libxcb1-dev >/dev/null
pip3 install -q cmake==3.31.6
cmake --version | head -1
