# SPDX-License-Identifier: GPL-3.0-only
# The Linux plugin's build and test machine: Ubuntu 20.04 (glibc 2.31: the oldest the binaries need), clang 12 + lld
# (the same flags as macOS: full LTO on the engine slots), libX11 headers, Xvfb for the view's self-test.
#   docker build --platform linux/amd64 -t frodhes-plugin-linux:focal-amd64 -f plugin/tests/docker/linux.Dockerfile plugin/tests/docker
FROM ubuntu:20.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends clang-12 lld-12 llvm-12 ninja-build python3 \
        python3-pip git libx11-dev xvfb xauth ca-certificates file \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir cmake==3.28.3
