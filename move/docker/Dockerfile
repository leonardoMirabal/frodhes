# SPDX-License-Identifier: GPL-3.0-only
# The Move build image: Debian bookworm (glibc 2.36, the Move's userland class), gcc for aarch64.
# On an arm64 host (Apple Silicon, CI ubuntu-24.04-arm) the native gcc builds and can also RUN the aarch64
# binaries (host tests, the probe's dlopen test). On an x86_64 host the cross gcc (aarch64-linux-gnu-gcc) builds
# only. Base pinned by digest so builds are reproducible.
#   docker build --platform linux/arm64 -t frodhes-move:bookworm move/docker
FROM debian:bookworm-slim@sha256:7ff6ba31527b9e336162fa061746c366c4b2032f4d2cff2078bea43f78efe3f8
ARG TARGETARCH
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev make binutils file \
 && if [ "$TARGETARCH" != "arm64" ]; then \
      apt-get install -y --no-install-recommends gcc-aarch64-linux-gnu libc6-dev-arm64-cross; fi \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
