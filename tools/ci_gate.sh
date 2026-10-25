#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# ci_gate.sh [sha]: did the ci workflow pass on exactly this commit? Used by .github/workflows/release.yml.
# Writes passed=true to $GITHUB_OUTPUT only when a ci run on <sha> is completed with conclusion success and every job
# succeeded (and the jobs in CI_GATE_JOBS exist); the release then skips its own host tests. If ci is still running
# on <sha> it waits (CI_GATE_WAIT_S, default 40 min) and uses that result. Anything else (ci failed, never ran, timed
# out, an API error) writes passed=false and the release runs the tests itself. It always exits 0: the gate can only
# skip tests that ci has already passed, never let a failure through.
# The same code by construction: before that, a completed, successful ci run on another commit whose git tree is
# identical to <sha>'s (`git rev-parse <sha>^{tree}`, or the API's commit.tree.sha) counts as a pass too (with the
# same every-job-green check), e.g. the PR's run on its head when GitHub's merge commit on main has the PR head's tree.
# Only ci.yml, only conclusion success, only exact tree equality; anything unclear falls through to the rule above.
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

jobs_bad() {  # $1 run id: prints the jobs that are not green (empty = all green), or exits 1 on an API error
    jobs=$(gh run view "$1" --json jobs) || return 1
    printf '%s' "$jobs" | REQ="$REQ" python3 -I -c '
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
'
}

tree_of() {  # $1 commit sha: its git tree sha (the local object, else the API), empty if neither knows it
    git rev-parse -q --verify "$1^{tree}" 2>/dev/null \
        || gh api "repos/{owner}/{repo}/commits/$1" --jq .commit.tree.sha 2>/dev/null || true
}

# a green ci run on a commit with exactly this tree (the same code): the PR's run when main's merge commit has its tree
tree=$(tree_of "$SHA")
case "$tree" in
[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*)
    recent=$(gh run list --workflow "$WF" --status success --json databaseId,headSha,status,conclusion -L 50) \
        || recent="[]"
    cands=$(printf '%s' "$recent" | SHA="$SHA" python3 -I -c '
import json, os, sys
seen = set()
try:
    rows = json.load(sys.stdin)
except Exception:
    rows = []
for r in rows:
    h = str(r.get("headSha") or "")
    if r.get("status") == "completed" and r.get("conclusion") == "success" and h and h != os.environ["SHA"] \
            and h not in seen and len(seen) < 20:
        seen.add(h)
        print(r["databaseId"], h)
')
    tmpf=$(mktemp)
    printf '%s\n' "$cands" | while read -r rid h; do
        [ -n "$h" ] || continue
        [ "$(tree_of "$h")" = "$tree" ] || continue
        bad=$(jobs_bad "$rid") || continue
        [ -z "$bad" ] || { echo "ci run $rid on $h (tree $tree) has jobs not green ($bad); not reused"; continue; }
        echo "$rid $h"
        break
    done > "$tmpf"
    hit=$(grep -E '^[0-9]+ [0-9a-f]+$' "$tmpf" | head -n 1)
    grep -v -E '^[0-9]+ [0-9a-f]+$' "$tmpf" || true
    rm -f "$tmpf"
    if [ -n "$hit" ]; then
        # shellcheck disable=SC2086
        set -- $hit
        decide true "host tests: ci run $1 passed on $2, whose git tree $tree is identical to $SHA's (the same code); reused"
    fi
    ;;
*) echo "the git tree of $SHA is unknown; looking for a ci run on exactly this commit" ;;
esac

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
        bad=$(jobs_bad "$2") || decide false "host tests: gh run view $2 failed; running them here"
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
