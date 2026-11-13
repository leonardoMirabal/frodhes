#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Run a command on the MPC port's ARM build in Docker (docs/MPC.md): arm64 natively on an arm64 Mac, arm32 under
# Docker's emulation (QEMU: the code is right or wrong there as on the device; its timing means nothing). Debian
# bookworm (glibc 2.36; the plugin asks for 2.31 at most), the repo mounted at its own path.
#   mpc/tests/arm_run.sh arm32|arm64 COMMAND...
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
case "${1:-}" in
    arm32) PLAT=linux/arm/v7 ;;
    arm64) PLAT=linux/arm64 ;;
    *) echo "usage: mpc/tests/arm_run.sh arm32|arm64 COMMAND..." >&2; exit 2 ;;
esac
shift
IMG="${MPC_TEST_IMAGE:-debian:bookworm-slim}"
exec nice -n 15 docker run --rm --platform "$PLAT" -v "$ROOT:$ROOT" -w "$ROOT" "$IMG" "$@"
