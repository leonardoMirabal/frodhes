#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The Linux plugin from macOS (or any Docker host): tools/plugin_pack.sh build linux + linux + the dependency check,
# inside ubuntu:20.04 (tools/plugin_linux_env.sh), for this machine's architecture (arm64 here: aarch64; CI builds
# x86_64 the same way). One Docker build at a time (the repository's Docker lock, as tools/build.py), 2 CPUs.
#   tools/plugin_linux_docker.sh            -> build/plugin-linux, build/plugin-pkg/frodhes-plugin-linux-<arch>.zip
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEPS=${FRP_DEPS_DIR:-"$ROOT/.local/plugin-deps"}
COMMON=$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)
VOLS="-v $ROOT:$ROOT"
for d in "$DEPS"/*; do   # (dependency folders linked in from another checkout: mounted where the links point)
    if [ -L "$d" ]; then t=$(readlink "$d"); VOLS="$VOLS -v $t:$t:ro"; fi
done
# shellcheck disable=SC2086
exec python3 - "$COMMON/frodhes-docker.lock" docker run --rm --cpus 2 $VOLS -w "$ROOT" -e FRP_DEPS_DIR="$DEPS" \
    -e PLUGIN_VER="${PLUGIN_VER:-0.0.0-dev}" ubuntu:20.04 sh -c \
    'sh tools/plugin_linux_env.sh && sh tools/plugin_pack.sh build linux && sh tools/plugin_pack.sh linux &&
     python3 tools/plugin_depcheck.py linux build/plugin-linux/bundles --glibc 2.31 &&
     build/plugin-linux/frp_test_coro && build/plugin-linux/frp_clap_render build/plugin-linux/bundles/Frodhes.clap --selftest' <<'PY'
import fcntl, subprocess, sys
with open(sys.argv[1], "w") as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    sys.exit(subprocess.call(sys.argv[2:]))
PY
