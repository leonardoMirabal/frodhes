# SPDX-License-Identifier: GPL-3.0-only
# The Windows build's headless check without a Windows machine: wine (64-bit) + Xvfb on Debian bookworm, amd64.
#   docker build --platform linux/amd64 -t frodhes-wine:bookworm-amd64 -f plugin/tests/docker/wine.Dockerfile plugin/tests/docker
# then tools/plugin_win.sh test runs the Windows test programs in it (the coroutine, the controller, the view's
# self-test, and the headless CLAP host loading Frodhes.clap). wine is not Windows: a pass here is a smoke test.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends wine wine xvfb xauth ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV WINEDEBUG=-all WINEPREFIX=/wine
RUN wine wineboot --init >/dev/null 2>&1 || true; wineserver -w || true
