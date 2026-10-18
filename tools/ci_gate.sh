#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# ci_gate.sh [sha]: did the ci workflow pass on exactly this commit? Used by .github/workflows/release.yml.
# Writes passed=true to $GITHUB_OUTPUT only when a ci run on <sha> is completed with conclusion success and every job
# succeeded (and the jobs in CI_GATE_JOBS exist); the release then skips its own host tests. If ci is still running
# on <sha> it waits (CI_GATE_WAIT_S, default 40 min) and uses that result. Anything else (ci failed, never ran, timed
# out, an API error) writes passed=false and the release runs the tests itself. It always exits 0: the gate can only
# skip tests that ci has already passed, never let a failure through.
SHA="${1:-$GITHUB_SHA}"
WF="${CI_WORKFLOW:-ci.yml}"
WAIT="${CI_GATE_WAIT_S:-2400}"
POLL="${CI_GATE_POLL_S:-30}"
REQ="${CI_GATE_JOBS:-host generators-macos}"
OUT="${GITHUB_OUTPUT:-/dev/null}"

decide() {  # $1 true|false, $2 message
    echo "passed=$1" >> "$OUT"
    echo "$2"
    exit 0
}
[ -n "$SHA" ] || decide false "host tests: no commit sha given; running them here"

start=$(date +%s)
while :; do
    rows=$(gh run list --workflow "$WF" --commit "$SHA" --json databaseId,headSha,status,conclusion -L 50) \
        || decide false "host tests: gh run list failed; running them here"
    # success <id> | pending <id> | failed <id> <conclusion> | none
    state=$(printf '%s' "$rows" | SHA="$SHA" python3 -I -c '
import json, os, sys
sha = os.environ["SHA"]
try:
    rows = [r for r in json.load(sys.stdin) if r.get("headSha") == sha]
except Exception:
    rows = []
ok = [r for r in rows if r.get("status") == "completed" and r.get("conclusion") == "success"]
run = [r for r in rows if r.get("status") != "completed"]
if ok:
    print("success", ok[0]["databaseId"])
elif run:
    print("pending", run[0]["databaseId"])
elif rows:
    print("failed", rows[0]["databaseId"], rows[0].get("conclusion"))
else:
    print("none")
')
    # shellcheck disable=SC2086  # split the state into its words on purpose
    set -- $state
    case "$1" in
    success)
        jobs=$(gh run view "$2" --json jobs) || decide false "host tests: gh run view $2 failed; running them here"
        bad=$(printf '%s' "$jobs" | REQ="$REQ" python3 -I -c '
import json, os, sys
try:
    jobs = json.load(sys.stdin)["jobs"]
except Exception:
    print("unreadable-jobs")
    sys.exit()
names = {j.get("name") for j in jobs}
bad = ["%s=%s" % (j.get("name"), j.get("conclusion")) for j in jobs if j.get("conclusion") != "success"]
bad += ["%s=missing" % n for n in os.environ["REQ"].split() if n not in names]
print(" ".join(bad) if bad or jobs else "no-jobs")
')
        [ -z "$bad" ] || decide false "host tests: ci run $2 on $SHA has jobs not green ($bad); running them here"
        decide true "host tests: ci run $2 passed on $SHA"
        ;;
    pending)
        now=$(date +%s)
        [ $((now - start)) -lt "$WAIT" ] \
            || decide false "host tests: ci run $2 on $SHA still running after $WAIT s; running them here"
        echo "ci run $2 on $SHA is still running; waiting $POLL s"
        sleep "$POLL"
        ;;
    failed)
        decide false "host tests: ci run $2 on $SHA concluded $3; running them here" ;;
    *)
        decide false "host tests: no ci run on $SHA; running them here" ;;
    esac
done
