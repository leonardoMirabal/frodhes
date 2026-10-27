#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Install / update / pull logs / remove the probe on the Move. DRY RUN by default: it prints every command.
# Add --go to run them. It never installs SSH keys (the user adds the key on the Move's developer page).
#   move/probe/install.sh [--go] install|update|pull|facts|remove
#     install  copy build/move/probe/<id>/{dsp.so,module.json} into the sound generator folder (both ids)
#     update   delete the module folders first, then install (as a module update would): the probe's marker in
#              the module folder must then be gone, the markers elsewhere must survive
#     pull     fetch every probe log and summary into build/move/probe/pulled/<time>/
#     facts    print the device facts (kernel, CPU, RAM, OS, glibc, Schwung version) into the pulled folder
#     remove   delete both module folders and every marker file the probe wrote
set -eu
GO=0
[ "${1:-}" = "--go" ] && { GO=1; shift; }
CMD=${1:-install}
HOST=${MOVE_HOST:-ableton@move.local}
SSH="ssh -o BatchMode=yes -o ConnectTimeout=5"
SCP="scp -o BatchMode=yes -o ConnectTimeout=5 -q"
MODS=/data/UserData/schwung/modules/sound_generators
IDS="frodhes-probe frodhes-probe-raw"
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BUILD=$ROOT/build/move/probe
STAMP=$(date +%Y%m%d-%H%M%S)
# the candidate data dirs (keep in step with probe_dirs() in probe.c); $HOME is the remote user's
MARKDIRS='/data/UserData/schwung /data/UserData/schwung/config /data/UserData/schwung/module_data /data/UserData/schwung/patches /data/UserData $HOME /var/tmp /tmp'

run() { echo "+ $*"; if [ $GO = 1 ]; then sh -c "$*" || echo "  (exit $?)"; fi; }
need() { echo "+ $*"; if [ $GO = 1 ]; then sh -c "$*" || { echo "  failed: stop"; exit 1; }; fi; }

[ $GO = 1 ] || echo "(dry run: add --go to execute; host $HOST)"
case $CMD in
install|update)
    for id in $IDS; do
        [ -f "$BUILD/$id/dsp.so" ] && [ -f "$BUILD/$id/module.json" ] || {
            echo "missing $BUILD/$id: build first (make move-probe)"; exit 1; }
    done
    need "$SSH $HOST 'test -d $MODS'"
    for id in $IDS; do
        [ $CMD = update ] && run "$SSH $HOST 'rm -rf $MODS/$id'"
        run "$SSH $HOST 'mkdir -p $MODS/$id'"
        # never overwrite a loaded dsp.so in place (scp truncates the mapped file: the host process crashes);
        # copy beside it, then rename (a new inode; a running instance keeps the old mapping)
        run "$SCP '$BUILD/$id/dsp.so' $HOST:$MODS/$id/.dsp.so.new && $SCP '$BUILD/$id/module.json' $HOST:$MODS/$id/.module.json.new"
        run "$SSH $HOST 'cd $MODS/$id && mv -f .dsp.so.new dsp.so && mv -f .module.json.new module.json'"
    done
    run "$SSH $HOST 'ls -la $MODS/frodhes-probe $MODS/frodhes-probe-raw'" ;;
pull)
    D=$BUILD/pulled/$STAMP
    run "mkdir -p '$D'"
    for id in $IDS; do
        run "mkdir -p '$D/$id' && $SCP '$HOST:$MODS/$id/probe-*' '$D/$id/' || echo 'no logs in $id'"
    done
    run "$SCP '$HOST:/tmp/frodhes-probe*.log' '$D/' 2>/dev/null || true"
    run "$SSH $HOST 'for d in $MARKDIRS $MODS/frodhes-probe $MODS/frodhes-probe-raw; do for f in \$d/.frodhes-probe*-marker; do [ -f \$f ] && echo \$f: \$(cat \$f); done; done' > '$D/markers.txt' || true"
    run "ls -la '$D' '$D'/*" ;;
facts)
    D=$BUILD/pulled/$STAMP
    run "mkdir -p '$D'"
    run "$SSH $HOST 'uname -a; echo; cat /etc/os-release; echo; ldd --version | head -1; echo; free -m; echo; nproc; grep -E \"model name|Hardware|Revision|CPU part\" /proc/cpuinfo | sort | uniq -c; echo; cat /data/UserData/schwung/VERSION 2>/dev/null || ls /data/UserData/schwung; echo; df -h /data; echo; ps -eLo pid,tid,cls,rtprio,psr,comm | grep -iE \"move|spi|schwung\" | head -40' > '$D/facts.txt'"
    run "cat '$D/facts.txt'" ;;
remove)
    for id in $IDS; do run "$SSH $HOST 'rm -rf $MODS/$id'"; done
    run "$SSH $HOST 'for d in $MARKDIRS $MODS; do rm -f \$d/.frodhes-probe-marker \$d/.frodhes-probe-raw-marker; done; rm -f /tmp/frodhes-probe*.log'" ;;
*) echo "unknown command $CMD"; exit 2 ;;
esac
